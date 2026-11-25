"""Write actions shared by the REST routers and the chat bots.

The rules for borrowing, listing, emergency tickets, crisis votes and RSVPs
live here once, so a WhatsApp command and an API call can never disagree.
Errors are raised as ``HTTPException``: the routers pass them through
unchanged and the chat engine turns them into localised replies.

``defer(fn, *args)`` schedules follow-up work (webhook/Telegram/WhatsApp
fan-out). Routers pass ``background_tasks.add_task``; the chat engine, which
already runs outside the request, passes :func:`run_now`.
"""

import datetime
from typing import Any, Callable

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models.booking import BOOKING_STATUSES, Booking
from app.models.community import Community, CommunityMember
from app.models.crisis import CrisisVote, EmergencyTicket
from app.models.event import Event, EventAttendee
from app.models.message import Message
from app.models.resource import Resource
from app.models.review import Review
from app.models.skill import Skill
from app.models.user import User
from app.services.activity import record_activity
from app.services.crisis_votes import apply_vote_threshold, is_noop_vote
from app.services.mode import effective_mode
from app.services.notifications import notify_booking_request, notify_booking_status
from app.services.webhooks import dispatch_event
from app.utils.authorization import (
    get_active_community_membership,
    is_community_member,
    require_active_membership,
    require_membership,
    users_share_community,
)

Defer = Callable[..., Any]


def run_now(fn: Callable[..., Any], *args: Any) -> None:
    """``defer`` for callers that already run outside the request cycle."""
    fn(*args)


# ── Bookings ──────────────────────────────────────────────────────


def units_exhausted(
    db: Session,
    resource: Resource,
    start: datetime.date,
    end: datetime.date,
    exclude_id: int | None = None,
) -> bool:
    """Return True if every unit of the resource is already reserved at some point in [start, end].

    Each pending/approved booking reserves one unit. The number of units a
    resource can lend at once is ``quantity_available`` (the owner's current
    stock count, see ``PATCH /resources/{id}/inventory``); bookings do not
    decrement it, they are counted here per date instead.
    """
    capacity = max(resource.quantity_available, 1)
    q = db.query(Booking.start_date, Booking.end_date).filter(
        Booking.resource_id == resource.id,
        Booking.status.in_(["pending", "approved"]),
        Booking.start_date <= end,
        Booking.end_date >= start,
    )
    if exclude_id:
        q = q.filter(Booking.id != exclude_id)
    overlapping = q.all()
    if len(overlapping) < capacity:
        return False
    # Peak concurrency inside the requested window is reached on the new
    # booking's first day or on the start day of one of the overlapping bookings.
    check_days = {start} | {b_start for b_start, _ in overlapping if b_start > start}
    return any(
        sum(1 for b_start, b_end in overlapping if b_start <= day <= b_end) >= capacity
        for day in check_days
    )


def allowed_transitions(current: str, is_owner: bool, is_borrower: bool) -> list[str]:
    """Return the statuses a user can transition to from the current status."""
    transitions: list[str] = []
    if current == "pending":
        if is_owner:
            transitions.extend(["approved", "rejected"])
        if is_borrower:
            transitions.append("cancelled")
    elif current == "approved":
        # Only the lender confirms the item came back, so a borrower cannot
        # mark their own booking complete to farm reputation points.
        if is_owner:
            transitions.append("completed")
        if is_borrower:
            transitions.append("cancelled")
    return transitions


def create_booking(
    db: Session,
    user: User,
    *,
    resource_id: int,
    start_date: datetime.date,
    end_date: datetime.date,
    message: str | None,
    defer: Defer,
) -> Booking:
    """Request to borrow a resource for a date range."""
    resource = db.query(Resource).filter(Resource.id == resource_id).first()
    if not resource:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    if not resource.is_available:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Resource is not available")
    if resource.owner_id == user.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot borrow your own resource")

    # Only members of the resource's community may book it. Personal items
    # (no community) can be booked by users who share a community with the owner.
    if resource.community_id is not None:
        if not is_community_member(db, resource.community_id, user.id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You must be a member of this resource's community to book it",
            )
    elif not users_share_community(db, user.id, resource.owner_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must share a community with the owner to book this resource",
        )

    if end_date < start_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_date must be >= start_date",
        )
    if start_date < datetime.date.today():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must not be in the past",
        )

    if units_exhausted(db, resource, start_date, end_date):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Dates overlap with an existing booking",
        )

    booking = Booking(
        resource_id=resource_id,
        borrower_id=user.id,
        start_date=start_date,
        end_date=end_date,
        message=message,
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)
    _ = booking.borrower
    _ = booking.resource

    # Notify resource owner
    owner = db.query(User).filter(User.id == resource.owner_id).first()
    if owner:
        notify_booking_request(owner.email, user.display_name, resource.title)

    record_activity(
        db,
        event_type="resource_borrowed",
        summary=f"requested to borrow \"{resource.title}\"",
        actor_id=user.id,
        community_id=resource.community_id,
    )

    defer(
        dispatch_event,
        db,
        "booking.created",
        {
            "booking_id": booking.id,
            "borrower_name": user.display_name,
            "resource_title": resource.title,
            "start_date": str(start_date),
            "end_date": str(end_date),
        },
        [resource.owner_id],
    )
    return booking


def set_booking_status(
    db: Session, user: User, *, booking_id: int, new_status: str, defer: Defer
) -> Booking:
    """Owners (lenders) can approve/reject/complete; borrowers can cancel."""
    booking = (
        db.query(Booking)
        .options(joinedload(Booking.borrower), joinedload(Booking.resource))
        .filter(Booking.id == booking_id)
        .first()
    )
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    if new_status not in BOOKING_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid status. Must be one of: {BOOKING_STATUSES}",
        )

    resource = db.query(Resource).filter(Resource.id == booking.resource_id).first()
    is_owner = bool(resource and resource.owner_id == user.id)
    is_borrower = booking.borrower_id == user.id

    if not is_owner and not is_borrower:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your booking")

    allowed = allowed_transitions(booking.status, is_owner, is_borrower)
    if new_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot transition from '{booking.status}' to '{new_status}'. Allowed: {allowed}",
        )

    booking.status = new_status
    db.commit()
    db.refresh(booking)

    # Notify borrower about status change
    borrower = db.query(User).filter(User.id == booking.borrower_id).first()
    resource_title = resource.title if resource else "Resource"
    if borrower and borrower.id != user.id:
        notify_booking_status(borrower.email, resource_title, new_status)
        defer(
            dispatch_event,
            db,
            "booking.status_changed",
            {"booking_id": booking.id, "resource_title": resource_title, "status": new_status},
            [borrower.id],
        )

    if new_status == "completed":
        record_activity(
            db,
            event_type="booking_completed",
            summary=f"completed a booking for \"{resource_title}\"",
            actor_id=user.id,
            community_id=resource.community_id if resource else None,
        )
    return booking


# ── Listings ──────────────────────────────────────────────────────


def create_resource(
    db: Session,
    user: User,
    *,
    title: str,
    description: str | None,
    category: str,
    condition: str | None,
    community_id: int | None,
    quantity_total: int,
    reorder_threshold: int | None,
    defer: Defer,
) -> Resource:
    """Create a resource listing. Category/condition are validated by the caller."""
    if community_id is not None:
        require_active_membership(db, community_id, user.id)

    resource = Resource(
        title=title,
        description=description,
        category=category,
        condition=condition,
        owner_id=user.id,
        community_id=community_id,
        quantity_total=quantity_total,
        quantity_available=quantity_total,  # start fully stocked
        reorder_threshold=reorder_threshold,
    )
    db.add(resource)
    db.commit()
    db.refresh(resource)
    _ = resource.owner
    record_activity(
        db,
        event_type="resource_shared",
        summary=f"shared \"{resource.title}\"",
        actor_id=user.id,
        community_id=resource.community_id,
    )

    if resource.community_id:
        defer(
            dispatch_event,
            db,
            "resource.shared",
            {"actor_name": user.display_name, "title": resource.title},
            [],
            resource.community_id,
        )
    return resource


def create_skill(
    db: Session,
    user: User,
    *,
    title: str,
    description: str | None,
    category: str,
    skill_type: str,
    community_id: int,
    defer: Defer,
) -> Skill:
    """Create a skill offer or request. Category/type are validated by the caller."""
    require_active_membership(db, community_id, user.id)

    skill = Skill(
        title=title,
        description=description,
        category=category,
        skill_type=skill_type,
        owner_id=user.id,
        community_id=community_id,
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    _ = skill.owner
    event_type = "skill_offered" if skill.skill_type == "offer" else "skill_requested"
    verb = "offered" if skill.skill_type == "offer" else "is looking for"
    record_activity(
        db,
        event_type=event_type,
        summary=f"{verb} \"{skill.title}\"",
        actor_id=user.id,
        community_id=skill.community_id,
    )

    if skill.community_id:
        defer(
            dispatch_event,
            db,
            "skill.created",
            {
                "actor_name": user.display_name,
                "title": skill.title,
                "category": skill.category,
                "skill_type": skill.skill_type,
            },
            [],
            skill.community_id,
        )
    return skill


def delete_skill(db: Session, user: User, skill_id: int) -> None:
    """Delete a skill listing (owner only)."""
    skill = db.query(Skill).filter(Skill.id == skill_id).first()
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if skill.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your skill listing")
    # Messages and endorsements keep existing after the listing is gone; detach
    # them so the foreign keys (enforced on PostgreSQL) do not block the delete.
    db.query(Message).filter(Message.skill_id == skill_id).update({Message.skill_id: None})
    db.query(Review).filter(Review.skill_id == skill_id).update({Review.skill_id: None})
    db.delete(skill)
    db.commit()


# ── Emergency tickets and crisis votes ────────────────────────────


def get_active_community(db: Session, community_id: int) -> Community:
    community = db.query(Community).filter(Community.id == community_id).first()
    if not community or not community.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Community not found"
        )
    return community


def ticket_by_client_id(db: Session, author_id: int, client_id: str | None) -> EmergencyTicket | None:
    if not client_id:
        return None
    return (
        db.query(EmergencyTicket)
        .options(joinedload(EmergencyTicket.author), joinedload(EmergencyTicket.assigned_to))
        .filter(EmergencyTicket.author_id == author_id, EmergencyTicket.client_id == client_id)
        .first()
    )


def create_ticket(
    db: Session,
    user: User,
    *,
    community_id: int,
    ticket_type: str,
    title: str,
    description: str | None,
    urgency: str,
    due_at: datetime.datetime | None,
    client_id: str | None,
    defer: Defer,
) -> tuple[EmergencyTicket, bool]:
    """Create an emergency ticket. Returns (ticket, created); created is False on an idempotent replay."""
    community = get_active_community(db, community_id)
    require_membership(db, community_id, user.id)

    # Emergency pings require crisis mode to be active
    if ticket_type == "emergency_ping" and effective_mode(community) != "red":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Emergency pings are only available in Red Sky (crisis) mode",
        )

    # Idempotent replay (offline queue, Background Sync, or the mesh copy of the
    # same ticket already synced): return the existing ticket
    existing = ticket_by_client_id(db, user.id, client_id)
    if existing is not None:
        return existing, False

    ticket = EmergencyTicket(
        community_id=community_id,
        author_id=user.id,
        ticket_type=ticket_type,
        title=title,
        description=description,
        urgency=urgency,
        due_at=due_at,
        client_id=client_id,
    )
    db.add(ticket)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent replay with the same client_id won the race
        db.rollback()
        existing = ticket_by_client_id(db, user.id, client_id)
        if existing is None:
            raise
        return existing, False
    db.refresh(ticket)
    _ = ticket.author
    _ = ticket.assigned_to

    record_activity(
        db,
        event_type="ticket_created",
        summary=f'created {ticket_type} ticket "{title}"',
        actor_id=user.id,
        community_id=community_id,
    )

    defer(
        dispatch_event,
        db,
        "ticket.created",
        {
            "title": title,
            "ticket_type": ticket_type,
            "urgency": urgency,
            "community_name": community.name,
        },
        [],
        community_id,
    )
    return ticket, True


def cast_crisis_vote(
    db: Session, user: User, *, community_id: int, vote_type: str, defer: Defer
) -> tuple[CrisisVote, str | None]:
    """Cast or change a crisis vote. Returns (vote, new_mode); new_mode is set when the threshold switched the mode.

    The returned vote object may already be deleted (a mode switch clears all
    votes); its attributes and ``user`` are loaded before that happens.
    """
    community = get_active_community(db, community_id)
    require_membership(db, community_id, user.id)

    # Votes act on the stored mode: "activate" while red (or "deactivate" while
    # blue) could never change anything and would linger as a stale vote
    if is_noop_vote(community.mode, vote_type):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Community is already in {'Red' if community.mode == 'red' else 'Blue'} Sky mode",
        )

    existing = (
        db.query(CrisisVote)
        .filter(CrisisVote.community_id == community_id, CrisisVote.user_id == user.id)
        .first()
    )
    if existing:
        if existing.vote_type == vote_type:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You have already voted this way",
            )
        existing.vote_type = vote_type
        db.commit()
        db.refresh(existing)
        vote = existing
    else:
        vote = CrisisVote(community_id=community_id, user_id=user.id, vote_type=vote_type)
        db.add(vote)
        db.commit()
        db.refresh(vote)

    # Eagerly load user before threshold check (which may delete the vote)
    _ = vote.user
    snapshot = {"id": vote.id, "community_id": vote.community_id, "user_id": vote.user_id,
                "vote_type": vote.vote_type, "created_at": vote.created_at, "user": vote.user}

    new_mode = apply_vote_threshold(db, community_id, vote_type, user.id)
    if new_mode == "red":
        member_ids = [
            row[0]
            for row in db.query(CommunityMember.user_id)
            .filter(CommunityMember.community_id == community_id)
            .all()
        ]
        defer(
            dispatch_event,
            db,
            "crisis.mode_changed",
            {"community_name": community.name, "new_mode": new_mode},
            member_ids,
            community_id,
        )
    return _VoteSnapshot(**snapshot), new_mode


class _VoteSnapshot:
    """Plain copy of a vote, safe to read after the row was deleted."""

    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)


# ── Events ────────────────────────────────────────────────────────


def load_event(event_id: int, db: Session) -> Event:
    event = (
        db.query(Event)
        .options(
            joinedload(Event.organizer),
            joinedload(Event.attendees).joinedload(EventAttendee.user),
        )
        .filter(Event.id == event_id)
        .first()
    )
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return event


def attend_event(db: Session, user: User, event_id: int) -> Event:
    """RSVP to an event. Returns the reloaded event."""
    event = load_event(event_id, db)

    if not is_community_member(db, event.community_id, user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be a member of this community to RSVP to its events",
        )

    already = (
        db.query(EventAttendee)
        .filter(EventAttendee.event_id == event_id, EventAttendee.user_id == user.id)
        .first()
    )
    if already:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Already attending this event"
        )

    if event.max_attendees is not None and len(event.attendees) >= event.max_attendees:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Event is full")

    db.add(EventAttendee(event_id=event_id, user_id=user.id))
    db.commit()
    return load_event(event_id, db)


# ── Membership ────────────────────────────────────────────────────


def join_community(db: Session, user: User, community_id: int, *, defer: Defer) -> CommunityMember:
    """Join a community (one active community per user)."""
    community = db.query(Community).filter(Community.id == community_id).first()
    if not community:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community not found")
    if community.merged_into_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This community has been merged. Join community #{community.merged_into_id} instead.",
        )
    if not community.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community not found")

    existing = (
        db.query(CommunityMember)
        .filter(CommunityMember.community_id == community_id, CommunityMember.user_id == user.id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already a member")

    other_membership = get_active_community_membership(
        db, user.id, exclude_community_id=community_id
    )
    if other_membership:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You're already a member of a community. Leave your current community before joining another.",
        )

    membership = CommunityMember(community_id=community_id, user_id=user.id, role="member")
    db.add(membership)
    db.commit()
    db.refresh(membership)
    _ = membership.user
    record_activity(
        db,
        event_type="member_joined",
        summary=f"joined \"{community.name}\"",
        actor_id=user.id,
        community_id=community_id,
    )

    defer(
        dispatch_event,
        db,
        "member.joined",
        {"actor_name": user.display_name, "community_name": community.name},
        [],
        community_id,
    )
    return membership
