"""WhatsApp command engine.

``handle(db, msg)`` takes one incoming message and returns the replies to
send as (chat_id, text) pairs; the router hands them to the bridge client.
All writes go through ``app.services.actions`` so the bot follows exactly the
same rules as the app. Message text is never stored.
"""

import datetime
import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import and_, delete, exists, or_, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base
from app.models.booking import Booking
from app.models.community import Community, CommunityMember
from app.models.crisis import CrisisVote, EmergencyTicket
from app.models.event import Event
from app.models.resource import Resource
from app.models.skill import Skill
from app.models.user import User
from app.models.webhook import TelegramLinkToken
from app.services import actions
from app.services import whatsapp as wa
from app.services.account_deletion import delete_account, remove_image_files
from app.services.auth import hash_password
from app.services.chat_i18n import LOCALES, catalog, locale_for_country, parse, t, word
from app.services.crisis_votes import pending_vote_type, threshold_needed
from app.services.mode import effective_mode
from app.utils.authorization import is_community_member

logger = logging.getLogger(__name__)

LIGHT_EMAIL_DOMAIN = "whatsapp.invalid"
DEFAULT_LOAN_DAYS = 3
CLAIM_TTL = datetime.timedelta(hours=1)
LINK_TTL = datetime.timedelta(minutes=30)
MAX_LINES = 5

# Commands that show or change personal data: private chat only
PRIVATE_ONLY = {"mine", "account", "stop", "start", "delete"}
# Commands that do not need a member account
OPEN_COMMANDS = {"help", "join", "link", "app_only"}


@dataclass
class Incoming:
    sender_id: str          # WhatsApp id of the person who wrote
    chat_id: str            # where the message was written (group or private chat)
    is_group: bool
    text: str
    push_name: str | None = None


@dataclass
class _Ctx:
    db: Session
    msg: Incoming
    user: User | None
    community: Community | None
    locale: str

    def t(self, key: str, **values) -> str:
        return t(self.locale, key, **values)

    def w(self, command: str) -> str:
        return word(self.locale, command)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def plain(value) -> str:
    """User text inside a reply: strip WhatsApp formatting characters and newlines."""
    return re.sub(r"[*_~`\r\n]+", " ", str(value)).strip()


def app_url(path: str = "") -> str:
    return f"{settings.frontend_url.rstrip('/')}{path}"


def private_link(text: str = "") -> str:
    return wa.bot_link(text) or app_url()


def light_email(whatsapp_id: str) -> str:
    digest = hashlib.sha256(whatsapp_id.encode()).hexdigest()[:16]
    return f"wa-{digest}@{LIGHT_EMAIL_DOMAIN}"


# ── Entry point ───────────────────────────────────────────────────


def handle(db: Session, msg: Incoming) -> list[tuple[str, str]]:
    user = db.query(User).filter(User.whatsapp_id == msg.sender_id, User.is_active == True).first()  # noqa: E712
    if user and not msg.is_group:
        user.whatsapp_dm_at = _utcnow()
        db.commit()

    parsed = parse(msg.text, require_prefix=msg.is_group)
    if msg.is_group:
        community = (
            db.query(Community)
            .filter(Community.whatsapp_group_id == msg.chat_id, Community.is_active == True)  # noqa: E712
            .first()
        )
    else:
        community = _primary_community(db, user) if user else None

    alias_locale = parsed[2] if parsed else None
    ctx = _Ctx(db, msg, user, community, _pick_locale(user, community, alias_locale))

    if parsed is None:
        if msg.is_group:
            return []  # ordinary group chat: never read or answer
        if user and community:
            return _reply(ctx, _free_text(ctx, msg.text))
        return _reply(ctx, ctx.t("dm_unknown", join=ctx.w("join")) if not user else ctx.t("unknown", help=ctx.w("help")))

    command, args, _ = parsed
    try:
        return _reply(ctx, _dispatch(ctx, command, args))
    except HTTPException as exc:
        db.rollback()
        return _reply(ctx, _error_text(ctx, exc))


def _reply(ctx: _Ctx, text: str | None) -> list[tuple[str, str]]:
    return [(ctx.msg.chat_id, text)] if text else []


def _pick_locale(user: User | None, community: Community | None, alias_locale: str | None) -> str:
    if user and user.language_code in LOCALES:
        return user.language_code
    # An unknown sender typing "hilfe" gets German, even in an English community
    if alias_locale and alias_locale != "en":
        return alias_locale
    if community:
        if community.primary_language in LOCALES:
            return community.primary_language
        return locale_for_country(community.country_code) or "en"
    return alias_locale or "en"


def _primary_community(db: Session, user: User) -> Community | None:
    membership = db.query(CommunityMember).filter(CommunityMember.user_id == user.id).first()
    if not membership:
        return None
    return db.query(Community).filter(
        Community.id == membership.community_id, Community.is_active == True  # noqa: E712
    ).first()


def _error_text(ctx: _Ctx, exc: HTTPException) -> str:
    detail = plain(exc.detail) if isinstance(exc.detail, str) else ""
    if exc.status_code == 404:
        return ctx.t("err_not_found")
    if exc.status_code == 403:
        return ctx.t("err_forbidden")
    if exc.status_code == 409:
        return ctx.t("err_conflict", detail=detail)
    return ctx.t("err_invalid", detail=detail)


def _dispatch(ctx: _Ctx, command: str, args: list[str]) -> str | None:
    msg = ctx.msg
    if command == "help":
        return _help(ctx)
    if command == "app_only":
        return ctx.t("app_only", url=app_url())
    if command == "link":
        return _link(ctx, args)
    if msg.is_group and ctx.community is None:
        return ctx.t("group_not_linked")
    if command == "join":
        return _join(ctx, args)
    if msg.is_group and command in PRIVATE_ONLY:
        return ctx.t("private_only", link=private_link(ctx.w(command)))

    if ctx.user is None:
        if msg.is_group:
            return ctx.t("not_member", join=ctx.w("join"))
        return ctx.t("dm_unknown", join=ctx.w("join"))

    # Personal settings work without a community
    if command in ("account", "stop", "start", "delete"):
        return HANDLERS[command](ctx, args)

    if ctx.community is None or not is_community_member(ctx.db, ctx.community.id, ctx.user.id):
        return ctx.t("not_member", join=ctx.w("join"))
    return HANDLERS[command](ctx, args)


def _free_text(ctx: _Ctx, text: str) -> str:
    """Private chat without a command word: use the AI classifier when one is configured."""
    from app.services.ai_client import get_ai_client
    from app.services.telegram_ai import _classify_intent

    if not get_ai_client():
        return ctx.t("unknown", help=ctx.w("help"))
    result = _classify_intent(text[:1000], effective_mode(ctx.community))
    intent = result.get("intent")
    query = (result.get("query") or "").split()
    try:
        if intent in ("search_resource", "search_skill"):
            return _search(ctx, query)
        if intent == "list_resources":
            return _search(ctx, [])
        if intent == "summarize_crisis":
            return _status(ctx, [])
        if intent == "create_request":
            title = result.get("title") or text
            return _sos(ctx, title.split())
    except HTTPException as exc:
        ctx.db.rollback()
        return _error_text(ctx, exc)
    return _help(ctx)


# ── Help, joining and linking ─────────────────────────────────────


_HELP_ORDER = (
    "search", "borrow", "lend", "offer", "need", "yes", "no", "returned", "remove",
    "mine", "events", "rsvp", "profile", "sos", "status", "vote", "join",
    "account", "stop", "start", "delete", "help",
)


def _help(ctx: _Ctx) -> str:
    hints = {**catalog("en")["args"], **catalog(ctx.locale).get("args", {})}
    lines = [ctx.t("help_title"), ctx.t("help_group_hint"), ""]
    for command in _HELP_ORDER:
        usage = f"!{ctx.w(command)} {hints.get(command, '')}".strip()
        lines.append(f"{usage} – {ctx.t('desc.' + command)}")
    lines += ["", ctx.t("help_footer", url=app_url())]
    return "\n".join(lines)


def _join(ctx: _Ctx, args: list[str]) -> str:
    msg, db, community = ctx.msg, ctx.db, ctx.community
    if not msg.is_group or community is None:
        return ctx.t("dm_unknown", join=ctx.w("join"))

    user = ctx.user
    if user and is_community_member(db, community.id, user.id):
        return ctx.t("join_already", community=plain(community.name))

    if user is None:
        name = plain(" ".join(args) or (msg.push_name or ""))[:100]
        if not name:
            return ctx.t("join_need_name", join=ctx.w("join"))
        user = User(
            email=light_email(msg.sender_id),
            hashed_password=hash_password(secrets.token_urlsafe(32)),
            display_name=name,
            account_type="whatsapp",
            whatsapp_id=msg.sender_id,
            language_code=ctx.locale,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        ctx.user = user

    try:
        actions.join_community(db, user, community.id, defer=actions.run_now)
    except HTTPException as exc:
        db.rollback()
        if exc.status_code == 409:
            return ctx.t("join_other")
        raise

    text = ctx.t("join_done", name=plain(user.display_name), community=plain(community.name))
    if user.whatsapp_dm_at is None:
        text += "\n" + ctx.t("join_dm_hint", link=private_link(ctx.w("help")))
    return text


def _valid_token(db: Session, token: str, token_type: str) -> TelegramLinkToken | None:
    return (
        db.query(TelegramLinkToken)
        .filter(
            TelegramLinkToken.token == token,
            TelegramLinkToken.token_type == token_type,
            TelegramLinkToken.used == False,  # noqa: E712
            TelegramLinkToken.expires_at > _utcnow(),
        )
        .first()
    )


def _link(ctx: _Ctx, args: list[str]) -> str:
    db, msg = ctx.db, ctx.msg
    if not args:
        return ctx.t("missing_text", example=f"!{ctx.w('link')} ABC123")
    code = args[0].strip()

    if msg.is_group:
        token = _valid_token(db, code, "wa_group")
        community = db.query(Community).filter(Community.id == token.owner_id).first() if token else None
        if not token or not community or not community.is_active:
            return ctx.t("link_invalid")
        # One community per group
        db.query(Community).filter(Community.whatsapp_group_id == msg.chat_id).update(
            {Community.whatsapp_group_id: None}, synchronize_session=False
        )
        community.whatsapp_group_id = msg.chat_id
        token.used = True
        db.commit()
        ctx.community = community
        ctx.locale = _pick_locale(ctx.user, community, None)
        return ctx.t("linked_group", community=plain(community.name), help=ctx.w("help"))

    token = _valid_token(db, code, "wa_user")
    target = db.query(User).filter(User.id == token.owner_id, User.is_active == True).first() if token else None  # noqa: E712
    if not token or not target:
        return ctx.t("link_invalid")
    existing = db.query(User).filter(User.whatsapp_id == msg.sender_id).first()
    if existing and existing.id != target.id:
        if existing.account_type != "whatsapp":
            return ctx.t("link_taken")
        merge_light_account(db, existing, target)
    target.whatsapp_id = msg.sender_id
    target.whatsapp_dm_at = _utcnow()
    token.used = True
    db.commit()
    ctx.user = target
    return ctx.t("linked_user", name=plain(target.display_name))


def merge_light_account(db: Session, light: User, target: User) -> None:
    """Move everything a WhatsApp light account owns over to ``target`` and close it.

    Walks every column that references users.id, so new models are covered
    automatically. Rows that would collide with a unique constraint (e.g. the
    same RSVP or vote twice) keep the target's row.
    """
    # One community per user: keep the target's membership if it has one
    if db.query(CommunityMember.id).filter(CommunityMember.user_id == target.id).first():
        db.query(CommunityMember).filter(CommunityMember.user_id == light.id).delete(synchronize_session=False)
    if db.query(CrisisVote.id).filter(CrisisVote.user_id == target.id).first():
        db.query(CrisisVote).filter(CrisisVote.user_id == light.id).delete(synchronize_session=False)

    for table in Base.metadata.sorted_tables:
        if table.name == "users":
            continue
        for column in table.columns:
            if not any(fk.column.table.name == "users" and fk.column.name == "id" for fk in column.foreign_keys):
                continue
            for unique_cols in _unique_column_sets(table):
                if column.name not in unique_cols:
                    continue
                others = [table.c[name] for name in unique_cols if name != column.name]
                twin = table.alias()
                clash = exists(
                    select(1).select_from(twin).where(
                        and_(twin.c[column.name] == target.id, *[twin.c[o.name] == o for o in others])
                    )
                )
                db.execute(delete(table).where(and_(column == light.id, clash)))
            db.execute(update(table).where(column == light.id).values({column.name: target.id}))

    light.whatsapp_id = None
    light.whatsapp_dm_at = None
    light.is_active = False
    light.display_name = target.display_name
    db.flush()


def _unique_column_sets(table) -> list[set[str]]:
    from sqlalchemy import UniqueConstraint

    sets = [
        {c.name for c in constraint.columns}
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    ]
    sets += [{c.name for c in index.columns} for index in table.indexes if index.unique]
    return [s for s in sets if len(s) > 1]


# ── Sharing and borrowing ─────────────────────────────────────────


def _search(ctx: _Ctx, args: list[str]) -> str:
    db, community = ctx.db, ctx.community
    query = " ".join(args).strip()
    items_q = db.query(Resource).filter(
        Resource.community_id == community.id, Resource.is_available == True  # noqa: E712
    )
    skills_q = db.query(Skill).filter(Skill.community_id == community.id, Skill.skill_type == "offer")
    if query:
        like = f"%{query}%"
        items_q = items_q.filter(or_(Resource.title.ilike(like), Resource.description.ilike(like)))
        skills_q = skills_q.filter(or_(Skill.title.ilike(like), Skill.description.ilike(like)))
    items = items_q.order_by(Resource.created_at.desc()).limit(MAX_LINES).all()
    skills = skills_q.order_by(Skill.created_at.desc()).limit(MAX_LINES).all()
    if not items and not skills:
        return ctx.t("search_none", q=plain(query))

    lines: list[str] = []
    if items:
        lines.append(f"*{ctx.t('search_items')}*")
        lines += [f"#{r.id} {plain(r.title)} ({plain(r.owner.display_name)})" for r in items]
    if skills:
        if lines:
            lines.append("")
        lines.append(f"*{ctx.t('search_skills')}*")
        lines += [f"{plain(s.title)} – {plain(s.owner.display_name)}" for s in skills]
    if items:
        lines += ["", ctx.t("search_hint", example=f"!{ctx.w('borrow')} {items[0].id}")]
    return "\n".join(lines)


def _lend(ctx: _Ctx, args: list[str]) -> str:
    title = plain(" ".join(args))[:200]
    if not title:
        return ctx.t("missing_text", example=f"!{ctx.w('lend')} …")
    resource = actions.create_resource(
        ctx.db, ctx.user, title=title, description=None, category="other", condition=None,
        community_id=ctx.community.id, quantity_total=1, reorder_threshold=None, defer=actions.run_now,
    )
    return ctx.t("lend_done", title=title, id=resource.id, url=app_url(f"/resources/{resource.id}"))


def _skill(ctx: _Ctx, args: list[str], skill_type: str) -> str:
    command = "offer" if skill_type == "offer" else "need"
    title = plain(" ".join(args))[:200]
    if not title:
        return ctx.t("missing_text", example=f"!{ctx.w(command)} …")
    actions.create_skill(
        ctx.db, ctx.user, title=title, description=None, category="other",
        skill_type=skill_type, community_id=ctx.community.id, defer=actions.run_now,
    )
    return ctx.t(f"{command}_done", title=title)


def _number(arg: str | None) -> int | None:
    if not arg:
        return None
    digits = arg.lstrip("#").lstrip("sS")
    return int(digits) if digits.isdigit() else None


_DATE_DMY = re.compile(r"^(\d{1,2})[./](\d{1,2})[./]?(\d{2,4})?$")


def parse_date(value: str, today: datetime.date) -> datetime.date | None:
    """Accept 2026-12-24, 24.12.2026, 24.12., 24.12 and 24/12."""
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        pass
    m = _DATE_DMY.match(value.strip())
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
    try:
        if year:
            y = int(year)
            return datetime.date(y + 2000 if y < 100 else y, month, day)
        candidate = datetime.date(today.year, month, day)
        # "24.01." written in December means next January
        return candidate if candidate >= today else datetime.date(today.year + 1, month, day)
    except ValueError:
        return None


def _borrow(ctx: _Ctx, args: list[str]) -> str:
    resource_id = _number(args[0] if args else None)
    if resource_id is None:
        return ctx.t("missing_id", example=f"!{ctx.w('borrow')} 12")
    today = datetime.date.today()
    start, end = today, today + datetime.timedelta(days=DEFAULT_LOAN_DAYS - 1)
    if len(args) >= 2:
        start = parse_date(args[1], today)
        end = parse_date(args[2], today) if len(args) >= 3 else start
        if start is None or end is None:
            return ctx.t("date_invalid")
    booking = actions.create_booking(
        ctx.db, ctx.user, resource_id=resource_id, start_date=start, end_date=end,
        message=None, defer=actions.run_now,
    )
    resource = booking.resource
    owner = resource.owner
    text = ctx.t(
        "borrow_done", owner=plain(owner.display_name), title=plain(resource.title),
        start=start.strftime("%d.%m."), end=end.strftime("%d.%m."), id=booking.id,
    )
    if not (owner.whatsapp_id and owner.whatsapp_dm_at and owner.whatsapp_notify):
        text += "\n" + ctx.t("borrow_no_dm", owner=plain(owner.display_name))
    return text


def _booking_status(ctx: _Ctx, args: list[str], command: str, new_status: str) -> str:
    booking_id = _number(args[0] if args else None)
    if booking_id is None:
        return ctx.t("missing_id", example=f"!{ctx.w(command)} 5")
    booking = actions.set_booking_status(
        ctx.db, ctx.user, booking_id=booking_id, new_status=new_status, defer=actions.run_now
    )
    return ctx.t("booking_done", id=booking.id, status=ctx.t(f"status.{booking.status}"))


def _remove(ctx: _Ctx, args: list[str]) -> str:
    raw = args[0] if args else ""
    number = _number(raw)
    if number is None:
        return ctx.t("missing_id", example=f"!{ctx.w('remove')} 12")
    if raw.lstrip("#")[:1] in ("s", "S"):
        actions.delete_skill(ctx.db, ctx.user, number)
        return ctx.t("removed", id=raw)
    resource = ctx.db.query(Resource).filter(Resource.id == number).first()
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")
    if resource.owner_id != ctx.user.id:
        raise HTTPException(status_code=403, detail="Not your resource")
    # Taken off the list rather than deleted: booking history stays intact
    resource.is_available = False
    ctx.db.commit()
    return ctx.t("removed", id=f"#{number}")


def _mine(ctx: _Ctx, args: list[str]) -> str:
    db, user = ctx.db, ctx.user
    items = db.query(Resource).filter(Resource.owner_id == user.id, Resource.is_available == True).limit(10).all()  # noqa: E712
    skills = db.query(Skill).filter(Skill.owner_id == user.id).limit(10).all()
    owned_ids = select(Resource.id).where(Resource.owner_id == user.id)
    bookings = (
        db.query(Booking)
        .filter(
            Booking.status.in_(["pending", "approved"]),
            or_(Booking.borrower_id == user.id, Booking.resource_id.in_(owned_ids)),
        )
        .order_by(Booking.start_date)
        .limit(10)
        .all()
    )
    if not (items or skills or bookings):
        return ctx.t("mine_empty")
    lines: list[str] = []
    if items:
        lines.append(f"*{ctx.t('mine_items')}*")
        lines += [f"#{r.id} {plain(r.title)}" for r in items]
    if skills:
        lines.append(f"*{ctx.t('mine_skills')}*")
        lines += [f"S{s.id} {plain(s.title)}" for s in skills]
    if bookings:
        lines.append(f"*{ctx.t('mine_bookings')}*")
        for b in bookings:
            lines.append(
                f"{b.id}: {plain(b.resource.title)} {b.start_date:%d.%m.}–{b.end_date:%d.%m.} "
                f"({ctx.t('status.' + b.status)})"
            )
    return "\n".join(lines)


# ── Events, profiles ──────────────────────────────────────────────


def _events(ctx: _Ctx, args: list[str]) -> str:
    events = (
        ctx.db.query(Event)
        .filter(Event.community_id == ctx.community.id, Event.start_at >= _utcnow())
        .order_by(Event.start_at)
        .limit(MAX_LINES)
        .all()
    )
    if not events:
        return ctx.t("events_none")
    lines = [f"*{ctx.t('events_title')}*"]
    lines += [f"#{e.id} {e.start_at:%d.%m. %H:%M} {plain(e.title)}" for e in events]
    lines += ["", ctx.t("events_hint", example=f"!{ctx.w('rsvp')} {events[0].id}")]
    return "\n".join(lines)


def _rsvp(ctx: _Ctx, args: list[str]) -> str:
    event_id = _number(args[0] if args else None)
    if event_id is None:
        return ctx.t("missing_id", example=f"!{ctx.w('rsvp')} 3")
    event = actions.attend_event(ctx.db, ctx.user, event_id)
    return ctx.t("rsvp_done", title=plain(event.title), when=f"{event.start_at:%d.%m. %H:%M}")


def _profile(ctx: _Ctx, args: list[str]) -> str:
    db, community = ctx.db, ctx.community
    name = " ".join(args).strip()
    if not name:
        return ctx.t("missing_text", example=f"!{ctx.w('profile')} Anna")
    member_ids = select(CommunityMember.user_id).where(CommunityMember.community_id == community.id)
    users = (
        db.query(User)
        .filter(User.id.in_(member_ids), User.display_name.ilike(f"%{name}%"), User.is_active == True)  # noqa: E712
        .limit(3)
        .all()
    )
    if not users:
        return ctx.t("profile_none", q=plain(name))
    blocks = []
    for u in users:
        items = db.query(Resource).filter(
            Resource.owner_id == u.id, Resource.community_id == community.id, Resource.is_available == True  # noqa: E712
        ).limit(MAX_LINES).all()
        skills = db.query(Skill).filter(
            Skill.owner_id == u.id, Skill.community_id == community.id, Skill.skill_type == "offer"
        ).limit(MAX_LINES).all()
        nothing = ctx.t("profile_nothing")
        lends = ", ".join(f"#{r.id} {plain(r.title)}" for r in items) or nothing
        can = ", ".join(plain(s.title) for s in skills) or nothing
        blocks.append(
            f"*{plain(u.display_name)}*\n{ctx.t('profile_lends')}: {lends}\n{ctx.t('profile_skills')}: {can}"
        )
    return "\n\n".join(blocks)


# ── Red Sky ───────────────────────────────────────────────────────


def _sos(ctx: _Ctx, args: list[str], *, ping: bool = False) -> str:
    text = plain(" ".join(args))
    command = "ping" if ping else "sos"
    if not text:
        return ctx.t("missing_text", example=f"!{ctx.w(command)} …")
    if ping and effective_mode(ctx.community) != "red":
        return ctx.t("ping_needs_red", sos=f"!{ctx.w('sos')}")
    ticket, _ = actions.create_ticket(
        ctx.db, ctx.user, community_id=ctx.community.id,
        ticket_type="emergency_ping" if ping else "request",
        title=text[:300], description=text[:5000],
        urgency="critical" if ping else "high", due_at=None, client_id=None, defer=actions.run_now,
    )
    return ctx.t("sos_done", id=ticket.id)


def _status(ctx: _Ctx, args: list[str]) -> str:
    community = ctx.community
    open_tickets = (
        ctx.db.query(EmergencyTicket)
        .filter(
            EmergencyTicket.community_id == community.id,
            EmergencyTicket.status.in_(["open", "in_progress"]),
        )
        .all()
    )
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    open_tickets.sort(key=lambda tk: (rank.get(tk.urgency, 4), tk.created_at))
    key = "status_red" if effective_mode(community) == "red" else "status_blue"
    lines = [ctx.t(key, community=plain(community.name), count=len(open_tickets))]
    lines += [
        f"{ctx.t('urgency.' + tk.urgency)}: {plain(tk.title)}" for tk in open_tickets[:MAX_LINES]
    ]
    return "\n".join(lines)


def _vote(ctx: _Ctx, args: list[str]) -> str:
    community = ctx.community
    vote_type = pending_vote_type(community.mode)
    _, new_mode = actions.cast_crisis_vote(
        ctx.db, ctx.user, community_id=community.id, vote_type=vote_type, defer=actions.run_now
    )
    if new_mode == "red":
        return ctx.t("vote_red", community=plain(community.name))
    if new_mode == "blue":
        return ctx.t("vote_blue", community=plain(community.name))
    members = ctx.db.query(CommunityMember).filter(CommunityMember.community_id == community.id).count()
    votes = ctx.db.query(CrisisVote).filter(
        CrisisVote.community_id == community.id, CrisisVote.vote_type == vote_type
    ).count()
    return ctx.t("vote_done", votes=votes, needed=threshold_needed(members))


# ── Personal settings ─────────────────────────────────────────────


def _account(ctx: _Ctx, args: list[str]) -> str:
    db, user = ctx.db, ctx.user
    if user.account_type != "whatsapp":
        return ctx.t("account_full", url=app_url("/login"))
    db.query(TelegramLinkToken).filter(
        TelegramLinkToken.token_type == "wa_claim",
        TelegramLinkToken.owner_id == user.id,
        TelegramLinkToken.used == False,  # noqa: E712
    ).delete(synchronize_session=False)
    token = secrets.token_urlsafe(32)
    db.add(TelegramLinkToken(token=token, token_type="wa_claim", owner_id=user.id, expires_at=_utcnow() + CLAIM_TTL))
    db.commit()
    return ctx.t("account_link", url=app_url(f"/claim/{token}"))


def _notify(ctx: _Ctx, on: bool) -> str:
    ctx.user.whatsapp_notify = on
    ctx.db.commit()
    return ctx.t("start_done") if on else ctx.t("stop_done", start=ctx.w("start"))


def delete_code(user: User, today: datetime.date | None = None) -> str:
    """Six-digit confirmation code, valid for the day it was shown."""
    day = (today or datetime.date.today()).isoformat()
    mac = hmac.new(settings.secret_key.encode(), f"wa-delete:{user.id}:{day}".encode(), hashlib.sha256)
    return f"{int(mac.hexdigest(), 16) % 1_000_000:06d}"


def _delete(ctx: _Ctx, args: list[str]) -> str:
    user = ctx.user
    if user.account_type != "whatsapp":
        return ctx.t("delete_full", url=app_url("/settings"))
    code = delete_code(user)
    if not args or not hmac.compare_digest(args[0], code):
        return ctx.t("delete_confirm", delete=ctx.w("delete"), code=code)
    result = delete_account(ctx.db, user)
    remove_image_files(result.image_paths)
    return ctx.t("delete_done")


HANDLERS = {
    "search": _search,
    "lend": _lend,
    "offer": lambda ctx, args: _skill(ctx, args, "offer"),
    "need": lambda ctx, args: _skill(ctx, args, "request"),
    "borrow": _borrow,
    "yes": lambda ctx, args: _booking_status(ctx, args, "yes", "approved"),
    "no": lambda ctx, args: _booking_status(ctx, args, "no", "rejected"),
    "returned": lambda ctx, args: _booking_status(ctx, args, "returned", "completed"),
    "remove": _remove,
    "mine": _mine,
    "events": _events,
    "rsvp": _rsvp,
    "profile": _profile,
    "sos": _sos,
    "ping": lambda ctx, args: _sos(ctx, args, ping=True),
    "status": _status,
    "vote": _vote,
    "account": _account,
    "stop": lambda ctx, args: _notify(ctx, False),
    "start": lambda ctx, args: _notify(ctx, True),
    "delete": _delete,
}
