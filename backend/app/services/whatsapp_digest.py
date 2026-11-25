"""Daily or weekly summary posted into linked WhatsApp groups.

Busy neighbourhood groups would drown in one message per new item, so
everyday activity is collected here instead. A background loop (started in
main.lifespan when the bridge is configured) calls ``run_due_digests``; each
community is claimed with a conditional UPDATE so several workers or
replicas never post the same digest twice.
"""

import asyncio
import datetime
import logging

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.community import Community, CommunityMember
from app.models.event import Event
from app.models.resource import Resource
from app.models.skill import Skill
from app.models.user import User
from app.services import whatsapp as wa
from app.services.chat_commands import app_url, plain
from app.services.chat_i18n import t, word
from app.services.whatsapp_notify import community_locale

logger = logging.getLogger(__name__)

INTERVALS = {"daily": datetime.timedelta(days=1), "weekly": datetime.timedelta(days=7)}
CHECK_EVERY_SECONDS = 600
MAX_PER_SECTION = 5


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def build_digest(db: Session, community: Community, since: datetime.datetime, now: datetime.datetime) -> str | None:
    """Digest text for activity in (since, now], or None when nothing happened."""
    locale = community_locale(community)
    items = db.query(Resource).filter(
        Resource.community_id == community.id, Resource.is_available == True,  # noqa: E712
        Resource.created_at > since,
    ).order_by(Resource.created_at.desc()).all()
    skills = db.query(Skill).filter(Skill.community_id == community.id, Skill.created_at > since).all()
    offers = [s for s in skills if s.skill_type == "offer"]
    requests = [s for s in skills if s.skill_type == "request"]
    horizon = now + INTERVALS.get(community.whatsapp_digest, INTERVALS["weekly"])
    events = db.query(Event).filter(
        Event.community_id == community.id, Event.start_at >= now, Event.start_at <= horizon
    ).order_by(Event.start_at).all()
    members = (
        db.query(User.display_name)
        .join(CommunityMember, CommunityMember.user_id == User.id)
        .filter(CommunityMember.community_id == community.id, CommunityMember.joined_at > since)
        .all()
    )
    if not (items or offers or requests or members):
        # Upcoming events alone are not news; they were in the last digest too
        return None

    title_key = "digest_daily" if community.whatsapp_digest == "daily" else "digest_weekly"
    lines = [t(locale, title_key, community=plain(community.name))]

    def section(key: str, rows: list[str]) -> None:
        if rows:
            extra = len(rows) - MAX_PER_SECTION
            lines.extend(["", f"*{t(locale, key)}*", *rows[:MAX_PER_SECTION]])
            if extra > 0:
                lines.append(f"+{extra}")

    section("digest_items", [f"#{r.id} {plain(r.title)} ({plain(r.owner.display_name)})" for r in items])
    section("digest_offers", [f"{plain(s.title)} – {plain(s.owner.display_name)}" for s in offers])
    section("digest_requests", [f"{plain(s.title)} – {plain(s.owner.display_name)}" for s in requests])
    section("digest_events", [f"#{e.id} {e.start_at:%d.%m. %H:%M} {plain(e.title)}" for e in events])
    if members:
        lines.extend(["", f"*{t(locale, 'digest_members')}*: " + ", ".join(plain(m[0]) for m in members)])
    lines.extend(["", t(locale, "digest_footer", search=word(locale, "search"), url=app_url())])
    return "\n".join(lines)


def run_due_digests(db: Session, now: datetime.datetime | None = None) -> int:
    """Post every digest that is due. Returns how many were sent."""
    if not wa.is_configured():
        return 0
    now = now or _utcnow()
    sent = 0
    communities = db.query(Community).filter(
        Community.whatsapp_group_id.isnot(None),
        Community.is_active == True,  # noqa: E712
        Community.whatsapp_digest.in_(list(INTERVALS)),
    ).all()
    for community in communities:
        interval = INTERVALS[community.whatsapp_digest]
        last = community.whatsapp_last_digest_at
        if last is not None and last > now - interval:
            continue
        # Claim: only the worker whose UPDATE matches the old value posts
        claimed = db.execute(
            update(Community)
            .where(Community.id == community.id)
            .where(
                Community.whatsapp_last_digest_at.is_(None)
                if last is None
                else Community.whatsapp_last_digest_at == last
            )
            .values(whatsapp_last_digest_at=now)
        ).rowcount
        db.commit()
        if claimed != 1:
            continue
        # First digest after linking covers one interval back
        text = build_digest(db, community, last or now - interval, now)
        if text:
            wa.send_text(community.whatsapp_group_id, text)
            sent += 1
    return sent


async def digest_loop(session_factory) -> None:
    while True:
        try:
            await asyncio.to_thread(_run_once, session_factory)
        except Exception:  # noqa: BLE001
            logger.exception("WhatsApp digest run failed")
        await asyncio.sleep(CHECK_EVERY_SECONDS)


def _run_once(session_factory) -> None:
    with session_factory() as db:
        run_due_digests(db)
