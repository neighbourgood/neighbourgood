"""SQLAlchemy models for users."""

import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    neighbourhood: Mapped[str | None] = mapped_column(String(100), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="member", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # WhatsApp chat id from the bridge (e.g. "4915112345678@c.us"); never shown to other users
    whatsapp_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    whatsapp_notify: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    # Last direct message from this user to the bot. The bot only writes privately to
    # people who wrote to it first (unsolicited messages get bridge numbers banned).
    whatsapp_dm_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    # "full" (app account) or "whatsapp" (light account created by the bot, no login)
    account_type: Mapped[str] = mapped_column(String(20), default="full", server_default="full", nullable=False)
    language_code: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mesh_public_key: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    @property
    def whatsapp_linked(self) -> bool:
        return self.whatsapp_id is not None

    resources: Mapped[list["Resource"]] = relationship(back_populates="owner")  # noqa: F821
