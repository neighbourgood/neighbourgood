"""Booking endpoints – calendar-based borrow requests with approve/reject flow."""

import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.dependencies import get_current_user
from app.models.booking import Booking
from app.models.resource import Resource
from app.models.user import User
from app.services import actions
from app.schemas.booking import (
    BookingCreate,
    BookingList,
    BookingOut,
    BookingStatusUpdate,
)

router = APIRouter(prefix="/bookings", tags=["bookings"])


def _booking_to_out(b: Booking) -> BookingOut:
    return BookingOut(
        id=b.id,
        resource_id=b.resource_id,
        resource_title=b.resource.title if b.resource else None,
        borrower_id=b.borrower_id,
        borrower=b.borrower,
        start_date=b.start_date,
        end_date=b.end_date,
        message=b.message,
        status=b.status,
        created_at=b.created_at,
        updated_at=b.updated_at,
    )


@router.post("", response_model=BookingOut, status_code=status.HTTP_201_CREATED)
def create_booking(
    body: BookingCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Request to borrow a resource for a date range."""
    booking = actions.create_booking(
        db,
        current_user,
        resource_id=body.resource_id,
        start_date=body.start_date,
        end_date=body.end_date,
        message=body.message,
        defer=background_tasks.add_task,
    )
    return _booking_to_out(booking)


@router.get("", response_model=BookingList)
def list_bookings(
    resource_id: int | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    role: str | None = Query(None, description="'borrower' or 'owner' – filter by your role"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List bookings relevant to the current user (as borrower or resource owner)."""
    query = db.query(Booking).options(
        joinedload(Booking.borrower),
        joinedload(Booking.resource),
    )

    if role == "owner":
        # Bookings for resources I own
        owned_ids = [r.id for r in db.query(Resource.id).filter(Resource.owner_id == current_user.id).all()]
        query = query.filter(Booking.resource_id.in_(owned_ids))
    elif role == "borrower":
        query = query.filter(Booking.borrower_id == current_user.id)
    else:
        # Default: show both
        owned_ids = [r.id for r in db.query(Resource.id).filter(Resource.owner_id == current_user.id).all()]
        query = query.filter(
            or_(
                Booking.borrower_id == current_user.id,
                Booking.resource_id.in_(owned_ids),
            )
        )

    if resource_id is not None:
        query = query.filter(Booking.resource_id == resource_id)
    if status_filter:
        query = query.filter(Booking.status == status_filter)

    total = query.count()
    items = query.order_by(Booking.created_at.desc()).offset(skip).limit(limit).all()
    return BookingList(items=[_booking_to_out(b) for b in items], total=total)


@router.get("/{booking_id}", response_model=BookingOut)
def get_booking(
    booking_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a single booking (must be borrower or resource owner)."""
    booking = (
        db.query(Booking)
        .options(joinedload(Booking.borrower), joinedload(Booking.resource))
        .filter(Booking.id == booking_id)
        .first()
    )
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    resource = db.query(Resource).filter(Resource.id == booking.resource_id).first()
    if booking.borrower_id != current_user.id and (not resource or resource.owner_id != current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your booking")

    return _booking_to_out(booking)


@router.patch("/{booking_id}", response_model=BookingOut)
def update_booking_status(
    booking_id: int,
    body: BookingStatusUpdate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update booking status. Owners (lenders) can approve/reject/complete; borrowers can cancel."""
    booking = actions.set_booking_status(
        db, current_user, booking_id=booking_id, new_status=body.status, defer=background_tasks.add_task
    )
    return _booking_to_out(booking)


@router.get("/resource/{resource_id}/calendar", response_model=list[BookingOut])
def get_resource_calendar(
    resource_id: int,
    month: int = Query(..., ge=1, le=12),
    year: int = Query(..., ge=2020, le=2100),
    db: Session = Depends(get_db),
):
    """Get all active bookings for a resource in a given month (for calendar display)."""
    resource = db.query(Resource).filter(Resource.id == resource_id).first()
    if not resource:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")

    first_day = datetime.date(year, month, 1)
    if month == 12:
        last_day = datetime.date(year + 1, 1, 1) - datetime.timedelta(days=1)
    else:
        last_day = datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)

    bookings = (
        db.query(Booking)
        .options(joinedload(Booking.borrower), joinedload(Booking.resource))
        .filter(
            Booking.resource_id == resource_id,
            Booking.status.in_(["pending", "approved"]),
            Booking.start_date <= last_day,
            Booking.end_date >= first_day,
        )
        .order_by(Booking.start_date)
        .all()
    )
    return [_booking_to_out(b) for b in bookings]
