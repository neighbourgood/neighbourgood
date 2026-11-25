"""WhatsApp bridge webhook, account/group linking, light-account claiming and bridge pairing."""

import datetime
import logging
import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.community import Community
from app.models.user import User
from app.models.webhook import TelegramLinkToken
from app.schemas.auth import Token
from app.schemas.user import UserProfile
from app.schemas.whatsapp import (
    BridgeQr,
    BridgeStatus,
    ClaimAccount,
    ClaimInfo,
    CommunityWhatsAppStatus,
    CommunityWhatsAppUpdate,
    WhatsAppLinkStart,
    WhatsAppSettingsUpdate,
)
from app.services import chat_commands
from app.services import whatsapp as wa
from app.services.auth import hash_password, issue_token_for_user
from app.services.chat_i18n import word
from app.utils.authorization import require_admin_or_leader

logger = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp"])

# Easy to type on a phone: no 0/O, 1/I/L
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
LINK_TTL = datetime.timedelta(minutes=30)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _new_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))


def _require_configured() -> None:
    if not wa.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WhatsApp integration is not configured on this instance",
        )


def _issue_token(db: Session, token_type: str, owner_id: int, ttl: datetime.timedelta) -> TelegramLinkToken:
    db.query(TelegramLinkToken).filter(
        TelegramLinkToken.token_type == token_type,
        TelegramLinkToken.owner_id == owner_id,
        TelegramLinkToken.used == False,  # noqa: E712
    ).delete(synchronize_session=False)
    token = TelegramLinkToken(
        token=_new_code(), token_type=token_type, owner_id=owner_id, expires_at=_utcnow() + ttl
    )
    db.add(token)
    db.commit()
    return token


# ── Personal account ──────────────────────────────────────────────


@router.post("/users/me/whatsapp/start-link", response_model=WhatsAppLinkStart)
def start_whatsapp_link(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Code the user sends to the bot in a private chat to link their WhatsApp."""
    _require_configured()
    token = _issue_token(db, "wa_user", current_user.id, LINK_TTL)
    message = f"{word(current_user.language_code, 'link')} {token.token}"
    return WhatsAppLinkStart(
        code=token.token, message=message, bot_url=wa.bot_link(message), expires_at=token.expires_at
    )


@router.patch("/users/me/whatsapp", response_model=UserProfile)
def update_whatsapp_settings(
    body: WhatsAppSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_user.whatsapp_notify = body.whatsapp_notify
    db.commit()
    db.refresh(current_user)
    return current_user


@router.delete("/users/me/whatsapp", status_code=status.HTTP_204_NO_CONTENT)
def unlink_whatsapp(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current_user.account_type == "whatsapp":
        # A light account is only reachable through WhatsApp
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Set an email and password first")
    current_user.whatsapp_id = None
    current_user.whatsapp_dm_at = None
    db.commit()


# ── Community group ───────────────────────────────────────────────


def _community_for_admin(db: Session, community_id: int, user: User) -> Community:
    community = db.query(Community).filter(Community.id == community_id).first()
    if not community or not community.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community not found")
    require_admin_or_leader(db, community_id, user.id)
    return community


def _group_status(community: Community) -> CommunityWhatsAppStatus:
    return CommunityWhatsAppStatus(
        configured=wa.is_configured(),
        linked=community.whatsapp_group_id is not None,
        digest=community.whatsapp_digest,
    )


@router.get("/communities/{community_id}/whatsapp", response_model=CommunityWhatsAppStatus)
def get_community_whatsapp(
    community_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return _group_status(_community_for_admin(db, community_id, current_user))


@router.post("/communities/{community_id}/whatsapp/start-link", response_model=WhatsAppLinkStart)
def start_community_whatsapp_link(
    community_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Code an admin or leader posts in the WhatsApp group (after adding the bot) to link it."""
    _require_configured()
    community = _community_for_admin(db, community_id, current_user)
    token = _issue_token(db, "wa_group", community.id, LINK_TTL)
    return WhatsAppLinkStart(
        code=token.token,
        message=f"!{word(current_user.language_code, 'link')} {token.token}",
        bot_url=None,
        expires_at=token.expires_at,
    )


@router.patch("/communities/{community_id}/whatsapp", response_model=CommunityWhatsAppStatus)
def update_community_whatsapp(
    community_id: int,
    body: CommunityWhatsAppUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    community = _community_for_admin(db, community_id, current_user)
    community.whatsapp_digest = body.digest
    db.commit()
    return _group_status(community)


@router.delete("/communities/{community_id}/whatsapp", status_code=status.HTTP_204_NO_CONTENT)
def unlink_community_whatsapp(
    community_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    community = _community_for_admin(db, community_id, current_user)
    community.whatsapp_group_id = None
    db.commit()


# ── Claiming a light account ──────────────────────────────────────


def _claim_token(db: Session, token: str) -> tuple[TelegramLinkToken, User]:
    record = (
        db.query(TelegramLinkToken)
        .filter(
            TelegramLinkToken.token == token,
            TelegramLinkToken.token_type == "wa_claim",
            TelegramLinkToken.used == False,  # noqa: E712
            TelegramLinkToken.expires_at > _utcnow(),
        )
        .first()
    )
    user = db.query(User).filter(User.id == record.owner_id).first() if record else None
    if not record or not user or not user.is_active or user.account_type != "whatsapp":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link expired or invalid")
    return record, user


@router.get("/auth/claim/{token}", response_model=ClaimInfo)
def get_claim(token: str, db: Session = Depends(get_db)):
    _, user = _claim_token(db, token)
    return ClaimInfo(display_name=user.display_name)


@router.post("/auth/claim/{token}", response_model=Token)
def claim_account(token: str, body: ClaimAccount, db: Session = Depends(get_db)):
    """Turn a WhatsApp light account into a full app account (email + password)."""
    record, user = _claim_token(db, token)
    if db.query(User.id).filter(func.lower(User.email) == body.email.lower(), User.id != user.id).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    # Claim the token atomically so a double submit cannot run twice
    claimed = (
        db.query(TelegramLinkToken)
        .filter(TelegramLinkToken.id == record.id, TelegramLinkToken.used == False)  # noqa: E712
        .update({TelegramLinkToken.used: True}, synchronize_session=False)
    )
    if claimed != 1:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link expired or invalid")
    user.email = body.email
    user.hashed_password = hash_password(body.password)
    user.account_type = "full"
    user.token_version += 1
    db.commit()
    return Token(access_token=issue_token_for_user(user))


# ── Bridge pairing (platform admin) ───────────────────────────────


def _require_platform_admin(user: User) -> None:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")


def _bridge_call(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        logger.warning("WhatsApp bridge call failed: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="WhatsApp bridge not reachable")


@router.get("/whatsapp/bridge/status", response_model=BridgeStatus)
def bridge_status(current_user: User = Depends(get_current_user)):
    _require_platform_admin(current_user)
    if not wa.is_configured():
        return BridgeStatus(configured=False, status="NOT_CONFIGURED")
    result = _bridge_call(wa.session_status)
    return BridgeStatus(configured=True, status=result["status"], bot_number=wa.bot_number())


@router.post("/whatsapp/bridge/start", response_model=BridgeStatus)
def bridge_start(current_user: User = Depends(get_current_user)):
    _require_platform_admin(current_user)
    _require_configured()
    result = _bridge_call(wa.start_session)
    return BridgeStatus(configured=True, status=result["status"], bot_number=None)


@router.get("/whatsapp/bridge/qr", response_model=BridgeQr)
def bridge_qr(current_user: User = Depends(get_current_user)):
    _require_platform_admin(current_user)
    _require_configured()
    return BridgeQr(**_bridge_call(wa.get_qr))


# ── Webhook from the bridge ───────────────────────────────────────


_IGNORED_CHATS = ("status@broadcast",)
_IGNORED_SUFFIXES = ("@broadcast", "@newsletter")


def _normalize_id(value) -> str:
    if isinstance(value, dict):  # some engines send {"_serialized": "..."}
        value = value.get("_serialized") or ""
    value = str(value or "")
    return value.replace("@s.whatsapp.net", "@c.us")


def parse_incoming(body: dict) -> chat_commands.Incoming | None:
    """Turn a WAHA "message" event into an Incoming, or None for anything to ignore."""
    if body.get("event") != "message":
        return None
    payload = body.get("payload") or {}
    if payload.get("fromMe"):
        return None
    chat_id = _normalize_id(payload.get("from"))
    text = payload.get("body") or ""
    if not chat_id or not isinstance(text, str) or not text.strip():
        return None
    if chat_id in _IGNORED_CHATS or chat_id.endswith(_IGNORED_SUFFIXES):
        return None
    data = payload.get("_data") or {}
    is_group = chat_id.endswith("@g.us")
    if is_group:
        key = data.get("key") if isinstance(data.get("key"), dict) else {}
        sender = _normalize_id(payload.get("participant") or data.get("author") or key.get("participant"))
        if not sender:
            return None
    else:
        sender = chat_id
    push_name = data.get("notifyName") or data.get("pushName") or payload.get("pushName")
    return chat_commands.Incoming(
        sender_id=sender[:64],
        chat_id=chat_id[:64],
        is_group=is_group,
        text=text[:2000],
        push_name=str(push_name)[:100] if push_name else None,
    )


def _process(bind, msg: chat_commands.Incoming) -> None:
    # Runs after the response; the request session is closed by then (see 2.8.3)
    with Session(bind=bind) as db:
        try:
            replies = chat_commands.handle(db, msg)
        except Exception:  # noqa: BLE001
            logger.exception("WhatsApp command failed")
            return
    for chat_id, text in replies:
        wa.send_text(chat_id, text)


@router.post("/whatsapp/webhook", status_code=status.HTTP_200_OK)
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    x_webhook_hmac: str | None = Header(default=None),
):
    """Receive message events from the WAHA bridge (HMAC-SHA512 signed)."""
    if not wa.is_configured():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WhatsApp integration is not configured")
    raw = await request.body()
    if not wa.verify_signature(raw, x_webhook_hmac):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature")
    try:
        body = await request.json()
    except ValueError:
        return {"ok": True}
    msg = parse_incoming(body) if isinstance(body, dict) else None
    if msg is not None:
        background_tasks.add_task(_process, db.get_bind(), msg)
    return {"ok": True}
