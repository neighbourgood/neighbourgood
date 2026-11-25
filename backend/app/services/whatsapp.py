"""WhatsApp bridge client (WAHA, a self-hosted WhatsApp Web HTTP API).

The bridge is unofficial: WhatsApp may ban numbers that behave like bots.
Outgoing messages therefore go through one paced queue: a random gap of
``whatsapp_send_interval_ms`` to twice that between messages, a typing
indicator before each one, and an hourly cap per chat. Callers never block
on delivery.

All functions are no-ops when NG_WHATSAPP_BRIDGE_URL is not configured.
"""

import collections
import hashlib
import hmac
import logging
import queue
import random
import threading
import time
from urllib.parse import quote

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# WAHA answers 463 (reach-out timelock) and 475 (message capping) when WhatsApp
# throttles the account. The session stays connected; the docs say to back off
# rather than restart or re-pair.
_THROTTLED = {463, 475}
_BACKOFF_SECONDS = 15 * 60

_outbox: "queue.Queue[tuple[str, str]]" = queue.Queue(maxsize=1000)
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()
_sent_per_chat: dict[str, collections.deque] = collections.defaultdict(collections.deque)
_paused_until = 0.0


def is_configured() -> bool:
    return bool(settings.whatsapp_bridge_url)


def _url(path: str) -> str:
    return f"{settings.whatsapp_bridge_url.rstrip('/')}{path}"


def _headers() -> dict[str, str]:
    return {"X-Api-Key": settings.whatsapp_api_key} if settings.whatsapp_api_key else {}


def verify_signature(raw_body: bytes, signature: str | None) -> bool:
    """Check WAHA's X-Webhook-Hmac header (HMAC-SHA512 of the raw body, hex)."""
    if not settings.whatsapp_webhook_secret or not signature:
        return False
    expected = hmac.new(
        settings.whatsapp_webhook_secret.encode(), raw_body, hashlib.sha512
    ).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


_paired_number: str | None = None


def bot_number() -> str | None:
    """The bot's phone number: NG_WHATSAPP_BOT_NUMBER, else read once from the paired session."""
    global _paired_number
    if settings.whatsapp_bot_number:
        return settings.whatsapp_bot_number.lstrip("+").replace(" ", "")
    if _paired_number is None and is_configured():
        try:
            me = session_status().get("me") or {}
            number = str(me.get("id", "")).split("@")[0]
            _paired_number = number if number.isdigit() else None
        except Exception:  # noqa: BLE001
            return None
    return _paired_number


def bot_link(text: str = "") -> str | None:
    """wa.me link that opens a private chat with the bot, optionally prefilled."""
    number = bot_number()
    if not number:
        return None
    if not text:
        return f"https://wa.me/{number}"
    return f"https://wa.me/{number}?text={quote(text)}"


# ── Sending ───────────────────────────────────────────────────────


def send_text(chat_id: str, text: str) -> None:
    """Queue a text message. Silently skips when the bridge is not configured."""
    if not is_configured() or not chat_id or not text:
        return
    _ensure_worker()
    try:
        _outbox.put_nowait((chat_id, text))
    except queue.Full:
        logger.warning("WhatsApp outbox full, dropping message")


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run_worker, name="whatsapp-sender", daemon=True)
            _worker.start()


def _run_worker() -> None:
    while True:
        chat_id, text = _outbox.get()
        try:
            _deliver(chat_id, text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("WhatsApp send error: %s", exc)
        gap = settings.whatsapp_send_interval_ms / 1000
        time.sleep(random.uniform(gap, gap * 2))


def _within_hourly_cap(chat_id: str) -> bool:
    now = time.monotonic()
    sent = _sent_per_chat[chat_id]
    while sent and now - sent[0] > 3600:
        sent.popleft()
    if len(sent) >= settings.whatsapp_hourly_limit_per_chat:
        return False
    sent.append(now)
    return True


def _deliver(chat_id: str, text: str) -> None:
    global _paused_until
    if time.monotonic() < _paused_until:
        logger.warning("WhatsApp sending paused after throttling, dropping message")
        return
    if not _within_hourly_cap(chat_id):
        logger.warning("WhatsApp hourly cap reached for a chat, dropping message")
        return
    session = settings.whatsapp_session
    with httpx.Client(timeout=15.0, headers=_headers()) as client:
        # Typing first, like a person would (WAHA's anti-blocking advice)
        try:
            client.post(_url("/api/startTyping"), json={"session": session, "chatId": chat_id})
            time.sleep(random.uniform(0.8, 2.0))
            client.post(_url("/api/stopTyping"), json={"session": session, "chatId": chat_id})
        except httpx.HTTPError:
            pass
        resp = client.post(
            _url("/api/sendText"),
            json={"session": session, "chatId": chat_id, "text": text},
        )
    if resp.status_code in _THROTTLED:
        _paused_until = time.monotonic() + _BACKOFF_SECONDS
        logger.warning("WhatsApp throttled the bridge (%s); pausing sends", resp.status_code)
    elif resp.status_code >= 300:
        logger.warning("WhatsApp sendText failed: %s %s", resp.status_code, resp.text[:200])


# ── Session management (instance admin) ───────────────────────────


def session_status() -> dict:
    """Return {"status": "...", "me": {...} | None} for the configured session."""
    with httpx.Client(timeout=10.0, headers=_headers()) as client:
        resp = client.get(_url(f"/api/sessions/{settings.whatsapp_session}"))
    if resp.status_code == 404:
        return {"status": "STOPPED", "me": None}
    resp.raise_for_status()
    data = resp.json()
    return {"status": data.get("status", "UNKNOWN"), "me": data.get("me")}


def start_session() -> dict:
    """Create the session if needed and start it."""
    name = settings.whatsapp_session
    with httpx.Client(timeout=20.0, headers=_headers()) as client:
        resp = client.get(_url(f"/api/sessions/{name}"))
        if resp.status_code == 404:
            resp = client.post(_url("/api/sessions"), json={"name": name})
        else:
            resp = client.post(_url(f"/api/sessions/{name}/start"))
    if resp.status_code >= 400 and resp.status_code != 422:  # 422: already started
        resp.raise_for_status()
    return session_status()


def get_qr() -> dict:
    """Return the current pairing QR code as {"mimetype": ..., "data": <base64>}."""
    with httpx.Client(timeout=10.0, headers={**_headers(), "Accept": "application/json"}) as client:
        resp = client.get(_url(f"/api/{settings.whatsapp_session}/auth/qr"), params={"format": "image"})
    resp.raise_for_status()
    data = resp.json()
    return {"mimetype": data.get("mimetype", "image/png"), "data": data.get("data", "")}
