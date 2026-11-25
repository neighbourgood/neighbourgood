"""WhatsApp side of the event fan-out (called from services.webhooks).

Private messages go only to people who wrote to the bot themselves and did
not send ``stop``. Groups get crisis news and urgent requests at once; all
other activity waits for the digest (services.whatsapp_digest).
"""

import logging

from sqlalchemy.orm import Session

from app.models.community import Community
from app.models.user import User
from app.services import whatsapp as wa
from app.services.chat_commands import app_url, plain
from app.services.chat_i18n import LOCALES, locale_for_country, t, word

logger = logging.getLogger(__name__)

URGENT = {"high", "critical"}


def community_locale(community: Community) -> str:
    if community.primary_language in LOCALES:
        return community.primary_language
    return locale_for_country(community.country_code) or "en"


def _personal_text(locale: str, event_type: str, payload: dict) -> str | None:
    if event_type == "booking.created" and payload.get("booking_id"):
        return t(
            locale, "booking_request_dm",
            borrower=plain(payload.get("borrower_name", "")),
            title=plain(payload.get("resource_title", "")),
            start=payload.get("start_date", "?"), end=payload.get("end_date", "?"),
            yes=word(locale, "yes"), no=word(locale, "no"), id=payload["booking_id"],
        )
    if event_type == "booking.status_changed":
        status = payload.get("status", "")
        return t(
            locale, "booking_status_dm",
            title=plain(payload.get("resource_title", "")),
            status=t(locale, f"status.{status}") if status else "?",
        )
    if event_type == "ticket.assigned":
        return t(locale, "ticket_assigned_dm", title=plain(payload.get("title", "")))
    if event_type == "crisis.mode_changed" and payload.get("new_mode") == "red":
        return t(locale, "crisis_dm", community=plain(payload.get("community_name", "")))
    return None


def _group_text(locale: str, event_type: str, payload: dict) -> str | None:
    if event_type == "crisis.mode_changed":
        community = plain(payload.get("community_name", ""))
        if payload.get("new_mode") == "blue":
            return t(locale, "group_blue", community=community)
        return t(locale, "group_red", community=community, sos=word(locale, "sos"), status=word(locale, "status"))
    if event_type == "ticket.created" and payload.get("urgency") in URGENT:
        return t(
            locale, "group_ticket",
            urgency=t(locale, f"urgency.{payload['urgency']}"),
            title=plain(payload.get("title", "")),
            url=app_url("/triage"),
        )
    return None


def fan_out(db: Session, event_type: str, payload: dict, user_ids: list[int], community_id: int | None) -> None:
    if not wa.is_configured():
        return
    group: Community | None = None
    if community_id:
        group = db.query(Community).filter(
            Community.id == community_id, Community.whatsapp_group_id.isnot(None)
        ).first()

    if user_ids:
        # The group already hears about crisis mode; don't repeat it privately
        skip_personal = event_type == "crisis.mode_changed" and group is not None
        if not skip_personal:
            users = db.query(User).filter(
                User.id.in_(user_ids),
                User.whatsapp_id.isnot(None),
                User.whatsapp_dm_at.isnot(None),
                User.whatsapp_notify == True,  # noqa: E712
                User.is_active == True,  # noqa: E712
            ).all()
            for user in users:
                text = _personal_text(user.language_code, event_type, payload)
                if text:
                    wa.send_text(user.whatsapp_id, text)

    if group is not None:
        text = _group_text(community_locale(group), event_type, payload)
        if text:
            wa.send_text(group.whatsapp_group_id, text)
