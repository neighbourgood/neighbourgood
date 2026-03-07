"""SQLAlchemy models for decentralized instance data sync."""

import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class InstanceSyncLog(Base):
    """Tracks the outcome of each sync pull from a known remote instance."""

    __tablename__ = "instance_sync_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("known_instances.id", ondelete="CASCADE"), index=True, nullable=False
    )
    synced_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    resources_synced: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skills_synced: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ok", nullable=False)  # ok | error
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)


class FederatedResource(Base):
    """A public resource synced from a remote NeighbourGood instance."""

    __tablename__ = "federated_resources"
    __table_args__ = (
        UniqueConstraint("source_instance_id", "remote_id", name="uq_fedres_instance_remote"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_instance_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("known_instances.id", ondelete="CASCADE"), index=True, nullable=False
    )
    remote_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    category: Mapped[str] = mapped_column(String(100), default="other", nullable=False)
    condition: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    community_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    owner_display_name: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    remote_created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    synced_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class FederatedSkill(Base):
    """A public skill listing synced from a remote NeighbourGood instance."""

    __tablename__ = "federated_skills"
    __table_args__ = (
        UniqueConstraint("source_instance_id", "remote_id", name="uq_fedskill_instance_remote"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_instance_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("known_instances.id", ondelete="CASCADE"), index=True, nullable=False
    )
    remote_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    category: Mapped[str] = mapped_column(String(100), default="other", nullable=False)
    skill_type: Mapped[str] = mapped_column(String(20), default="offer", nullable=False)
    community_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    owner_display_name: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    remote_created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    synced_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
