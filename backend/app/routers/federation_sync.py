"""Decentralized data sync between NeighbourGood instances.

Endpoints:
  GET  /federation/sync/snapshot         – public; exposes this instance's public data for peers to pull
  POST /federation/sync/pull             – admin; pulls public data from all reachable known instances
  GET  /federation/sync/status           – authenticated; last sync result per known instance
  GET  /federation/federated-resources   – authenticated; browse resources synced from other instances
  GET  /federation/federated-skills      – authenticated; browse skills synced from other instances
"""

import datetime
import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models.community import Community
from app.models.federation import KnownInstance
from app.models.resource import Resource
from app.models.skill import Skill
from app.models.sync import FederatedResource, FederatedSkill, InstanceSyncLog
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["federation-sync"])


# ── Schemas ─────────────────────────────────────────────────────────────────


class SnapshotResource(BaseModel):
    remote_id: int
    title: str
    description: str
    category: str
    condition: str
    community_name: str
    owner_display_name: str
    is_available: bool
    created_at: Optional[str]


class SnapshotSkill(BaseModel):
    remote_id: int
    title: str
    description: str
    category: str
    skill_type: str
    community_name: str
    owner_display_name: str
    created_at: Optional[str]


class InstanceSnapshot(BaseModel):
    instance_url: str
    instance_name: str
    snapshot_at: str
    resources: list[SnapshotResource]
    skills: list[SnapshotSkill]


class SyncStatusEntry(BaseModel):
    instance_id: int
    instance_url: str
    instance_name: str
    last_synced_at: Optional[datetime.datetime]
    resources_synced: int
    skills_synced: int
    status: str
    error_message: str


class SyncPullResult(BaseModel):
    instances_attempted: int
    instances_ok: int
    instances_failed: int
    total_resources_synced: int
    total_skills_synced: int


class FederatedResourceOut(BaseModel):
    id: int
    source_instance_id: int
    source_instance_url: str
    source_instance_name: str
    remote_id: int
    title: str
    description: str
    category: str
    condition: str
    community_name: str
    owner_display_name: str
    is_available: bool
    remote_created_at: Optional[datetime.datetime]
    synced_at: datetime.datetime

    model_config = {"from_attributes": True}


class FederatedSkillOut(BaseModel):
    id: int
    source_instance_id: int
    source_instance_url: str
    source_instance_name: str
    remote_id: int
    title: str
    description: str
    category: str
    skill_type: str
    community_name: str
    owner_display_name: str
    remote_created_at: Optional[datetime.datetime]
    synced_at: datetime.datetime

    model_config = {"from_attributes": True}


# ── Public snapshot endpoint (consumed by remote peers) ─────────────────────


@router.get("/federation/sync/snapshot", response_model=InstanceSnapshot)
def get_sync_snapshot(
    since: Optional[str] = Query(
        None,
        description="ISO-8601 datetime; return only items created/updated after this timestamp",
    ),
    db: Session = Depends(get_db),
):
    """Return a snapshot of all public resources and skills on this instance.

    Remote instances call this endpoint during a pull sync. The optional `since`
    parameter enables incremental sync — only items modified after that timestamp
    are returned, reducing payload size on subsequent pulls.
    """
    since_dt: Optional[datetime.datetime] = None
    if since:
        try:
            since_dt = datetime.datetime.fromisoformat(since.replace("Z", "+00:00")).replace(
                tzinfo=None
            )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid `since` timestamp — use ISO-8601 format",
            )

    resource_query = db.query(Resource).filter(Resource.is_available.is_(True))
    if since_dt:
        resource_query = resource_query.filter(Resource.updated_at >= since_dt)
    resources = resource_query.all()

    skill_query = db.query(Skill)
    if since_dt:
        skill_query = skill_query.filter(Skill.updated_at >= since_dt)
    skills = skill_query.all()

    # Pre-fetch community names to avoid N+1 queries
    community_ids = set()
    for r in resources:
        if r.community_id:
            community_ids.add(r.community_id)
    for s in skills:
        if s.community_id:
            community_ids.add(s.community_id)

    communities: dict[int, str] = {}
    if community_ids:
        for c in db.query(Community).filter(Community.id.in_(community_ids)).all():
            communities[c.id] = c.name

    # Pre-fetch owner display names
    user_ids = {r.owner_id for r in resources} | {s.owner_id for s in skills}
    users: dict[int, str] = {}
    if user_ids:
        for u in db.query(User).filter(User.id.in_(user_ids)).all():
            users[u.id] = u.display_name

    def _dt(v: Optional[datetime.datetime]) -> Optional[str]:
        return v.isoformat() if v else None

    return InstanceSnapshot(
        instance_url=settings.instance_url or "",
        instance_name=settings.instance_name or "",
        snapshot_at=datetime.datetime.utcnow().isoformat(),
        resources=[
            SnapshotResource(
                remote_id=r.id,
                title=r.title,
                description=r.description or "",
                category=r.category,
                condition=r.condition or "",
                community_name=communities.get(r.community_id, "") if r.community_id else "",
                owner_display_name=users.get(r.owner_id, ""),
                is_available=r.is_available,
                created_at=_dt(r.created_at),
            )
            for r in resources
        ],
        skills=[
            SnapshotSkill(
                remote_id=s.id,
                title=s.title,
                description=s.description or "",
                category=s.category,
                skill_type=s.skill_type,
                community_name=communities.get(s.community_id, "") if s.community_id else "",
                owner_display_name=users.get(s.owner_id, ""),
                created_at=_dt(s.created_at),
            )
            for s in skills
        ],
    )


# ── Admin: trigger pull from all known instances ─────────────────────────────


@router.post("/federation/sync/pull", response_model=SyncPullResult)
def pull_from_all_instances(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Pull public data from every reachable known instance and store it locally.

    Only available to admins. Performs incremental sync: passes the timestamp of
    the last successful sync to each instance so only new/updated items are
    transferred. Results are written to `federated_resources` and
    `federated_skills`; sync outcome is recorded in `instance_sync_logs`.
    """
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

    instances = db.query(KnownInstance).filter(KnownInstance.is_reachable.is_(True)).all()

    total_resources = 0
    total_skills = 0
    ok_count = 0
    fail_count = 0

    for inst in instances:
        # Determine the `since` cursor from the most recent successful sync log
        last_ok_log = (
            db.query(InstanceSyncLog)
            .filter(
                InstanceSyncLog.instance_id == inst.id,
                InstanceSyncLog.status == "ok",
            )
            .order_by(InstanceSyncLog.synced_at.desc())
            .first()
        )
        since_param = last_ok_log.synced_at.isoformat() if last_ok_log else None

        resources_synced, skills_synced, error = _pull_instance_snapshot(db, inst, since_param)

        log = InstanceSyncLog(
            instance_id=inst.id,
            synced_at=datetime.datetime.utcnow(),
            resources_synced=resources_synced,
            skills_synced=skills_synced,
            status="error" if error else "ok",
            error_message=error or "",
        )
        db.add(log)

        if error:
            fail_count += 1
        else:
            ok_count += 1
            total_resources += resources_synced
            total_skills += skills_synced

    db.commit()

    return SyncPullResult(
        instances_attempted=len(instances),
        instances_ok=ok_count,
        instances_failed=fail_count,
        total_resources_synced=total_resources,
        total_skills_synced=total_skills,
    )


def _pull_instance_snapshot(
    db: Session,
    inst: KnownInstance,
    since: Optional[str],
) -> tuple[int, int, Optional[str]]:
    """Fetch /federation/sync/snapshot from a remote instance and upsert local records.

    Returns (resources_synced, skills_synced, error_message_or_None).
    """
    url = f"{inst.url}/federation/sync/snapshot"
    params = {"since": since} if since else {}
    try:
        resp = httpx.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Sync pull from %s failed: %s", inst.url, exc)
        return 0, 0, str(exc)

    resources_synced = 0
    skills_synced = 0

    for item in data.get("resources", []):
        remote_id = item.get("remote_id")
        if not remote_id:
            continue
        existing = (
            db.query(FederatedResource)
            .filter(
                FederatedResource.source_instance_id == inst.id,
                FederatedResource.remote_id == remote_id,
            )
            .first()
        )
        remote_created_at = None
        if item.get("created_at"):
            try:
                remote_created_at = datetime.datetime.fromisoformat(
                    item["created_at"].replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except ValueError:
                pass

        if existing:
            existing.title = item.get("title", existing.title)
            existing.description = item.get("description", existing.description)
            existing.category = item.get("category", existing.category)
            existing.condition = item.get("condition", existing.condition)
            existing.community_name = item.get("community_name", existing.community_name)
            existing.owner_display_name = item.get("owner_display_name", existing.owner_display_name)
            existing.is_available = item.get("is_available", existing.is_available)
            existing.remote_created_at = remote_created_at
            existing.synced_at = datetime.datetime.utcnow()
        else:
            db.add(
                FederatedResource(
                    source_instance_id=inst.id,
                    remote_id=remote_id,
                    title=item.get("title", ""),
                    description=item.get("description", ""),
                    category=item.get("category", "other"),
                    condition=item.get("condition", ""),
                    community_name=item.get("community_name", ""),
                    owner_display_name=item.get("owner_display_name", ""),
                    is_available=item.get("is_available", True),
                    remote_created_at=remote_created_at,
                    synced_at=datetime.datetime.utcnow(),
                )
            )
        resources_synced += 1

    for item in data.get("skills", []):
        remote_id = item.get("remote_id")
        if not remote_id:
            continue
        existing = (
            db.query(FederatedSkill)
            .filter(
                FederatedSkill.source_instance_id == inst.id,
                FederatedSkill.remote_id == remote_id,
            )
            .first()
        )
        remote_created_at = None
        if item.get("created_at"):
            try:
                remote_created_at = datetime.datetime.fromisoformat(
                    item["created_at"].replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except ValueError:
                pass

        if existing:
            existing.title = item.get("title", existing.title)
            existing.description = item.get("description", existing.description)
            existing.category = item.get("category", existing.category)
            existing.skill_type = item.get("skill_type", existing.skill_type)
            existing.community_name = item.get("community_name", existing.community_name)
            existing.owner_display_name = item.get("owner_display_name", existing.owner_display_name)
            existing.remote_created_at = remote_created_at
            existing.synced_at = datetime.datetime.utcnow()
        else:
            db.add(
                FederatedSkill(
                    source_instance_id=inst.id,
                    remote_id=remote_id,
                    title=item.get("title", ""),
                    description=item.get("description", ""),
                    category=item.get("category", "other"),
                    skill_type=item.get("skill_type", "offer"),
                    community_name=item.get("community_name", ""),
                    owner_display_name=item.get("owner_display_name", ""),
                    remote_created_at=remote_created_at,
                    synced_at=datetime.datetime.utcnow(),
                )
            )
        skills_synced += 1

    return resources_synced, skills_synced, None


# ── Sync status ──────────────────────────────────────────────────────────────


@router.get("/federation/sync/status", response_model=list[SyncStatusEntry])
def get_sync_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the most recent sync result for each known instance."""
    instances = db.query(KnownInstance).all()

    result = []
    for inst in instances:
        last_log = (
            db.query(InstanceSyncLog)
            .filter(InstanceSyncLog.instance_id == inst.id)
            .order_by(InstanceSyncLog.synced_at.desc())
            .first()
        )
        result.append(
            SyncStatusEntry(
                instance_id=inst.id,
                instance_url=inst.url,
                instance_name=inst.name,
                last_synced_at=last_log.synced_at if last_log else None,
                resources_synced=last_log.resources_synced if last_log else 0,
                skills_synced=last_log.skills_synced if last_log else 0,
                status=last_log.status if last_log else "never",
                error_message=last_log.error_message if last_log else "",
            )
        )
    return result


# ── Browse federated data ────────────────────────────────────────────────────


def _enrich_with_instance(rows, db: Session) -> tuple[dict[int, KnownInstance], list]:
    """Pre-fetch KnownInstance records for a list of federated rows."""
    instance_ids = {r.source_instance_id for r in rows}
    instances: dict[int, KnownInstance] = {}
    if instance_ids:
        for inst in db.query(KnownInstance).filter(KnownInstance.id.in_(instance_ids)).all():
            instances[inst.id] = inst
    return instances, rows


@router.get("/federation/federated-resources", response_model=list[FederatedResourceOut])
def list_federated_resources(
    category: Optional[str] = Query(None, description="Filter by resource category"),
    instance_id: Optional[int] = Query(None, description="Filter by source instance"),
    available_only: bool = Query(True, description="Only show available resources"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Browse resources synced from other NeighbourGood instances."""
    query = db.query(FederatedResource)
    if available_only:
        query = query.filter(FederatedResource.is_available.is_(True))
    if category:
        query = query.filter(FederatedResource.category == category)
    if instance_id:
        query = query.filter(FederatedResource.source_instance_id == instance_id)
    rows = query.order_by(FederatedResource.synced_at.desc()).offset(skip).limit(limit).all()

    instances, rows = _enrich_with_instance(rows, db)

    output = []
    for row in rows:
        inst = instances.get(row.source_instance_id)
        output.append(
            FederatedResourceOut(
                id=row.id,
                source_instance_id=row.source_instance_id,
                source_instance_url=inst.url if inst else "",
                source_instance_name=inst.name if inst else "",
                remote_id=row.remote_id,
                title=row.title,
                description=row.description,
                category=row.category,
                condition=row.condition,
                community_name=row.community_name,
                owner_display_name=row.owner_display_name,
                is_available=row.is_available,
                remote_created_at=row.remote_created_at,
                synced_at=row.synced_at,
            )
        )
    return output


@router.get("/federation/federated-skills", response_model=list[FederatedSkillOut])
def list_federated_skills(
    category: Optional[str] = Query(None, description="Filter by skill category"),
    skill_type: Optional[str] = Query(None, description="Filter by skill_type: offer or request"),
    instance_id: Optional[int] = Query(None, description="Filter by source instance"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Browse skills synced from other NeighbourGood instances."""
    query = db.query(FederatedSkill)
    if category:
        query = query.filter(FederatedSkill.category == category)
    if skill_type:
        query = query.filter(FederatedSkill.skill_type == skill_type)
    if instance_id:
        query = query.filter(FederatedSkill.source_instance_id == instance_id)
    rows = query.order_by(FederatedSkill.synced_at.desc()).offset(skip).limit(limit).all()

    instances, rows = _enrich_with_instance(rows, db)

    output = []
    for row in rows:
        inst = instances.get(row.source_instance_id)
        output.append(
            FederatedSkillOut(
                id=row.id,
                source_instance_id=row.source_instance_id,
                source_instance_url=inst.url if inst else "",
                source_instance_name=inst.name if inst else "",
                remote_id=row.remote_id,
                title=row.title,
                description=row.description,
                category=row.category,
                skill_type=row.skill_type,
                community_name=row.community_name,
                owner_display_name=row.owner_display_name,
                remote_created_at=row.remote_created_at,
                synced_at=row.synced_at,
            )
        )
    return output
