"""Alert grouping and flapping detection for service-scoped intake."""

from __future__ import annotations

import dataclasses
import hashlib
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import AlertFingerprintState, Incident, Service
from backend.db.repos import IncidentCommentRepo, OrganizationRepo
from backend.ingest.adapters.base import ParsedIncident
from backend.notifications import (
    CATEGORY_INCIDENT,
    emit_notification,
    org_user_ids_with_roles,
)

GROUPING_SIMILARITY = 0.82
GROUPING_WINDOW_MIN = 15
FLAP_MIN_TRANSITIONS = 6
FLAP_WINDOW_MIN = 30
FLAP_SUPPRESS_MIN = 30

_UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_ISO_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[t\s]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:z|[+-]\d{2}:?\d{2})?)?\b",
    re.IGNORECASE,
)
_HEX_RE = re.compile(r"\b(?:0x)?[0-9a-f]{8,}\b", re.IGNORECASE)
_DIGIT_RE = re.compile(r"\d+")
_TRAILING_HOST_RE = re.compile(
    r"(?:^|\s)[a-z0-9][a-z0-9-]*\d+(?:\.[a-z0-9][a-z0-9-]*\d+)*\s*$",
    re.IGNORECASE,
)


@dataclasses.dataclass
class NoiseDecision:
    incident: Incident | None = None
    dedup_action: str | None = None
    grouped: bool = False
    suppressed: bool = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def normalize_title_tokens(title: str) -> tuple[str, ...]:
    """Return normalized tokens used for alert title similarity."""

    cleaned = title.lower()
    cleaned = _UUID_RE.sub(" ", cleaned)
    cleaned = _ISO_RE.sub(" ", cleaned)
    cleaned = _HEX_RE.sub(" ", cleaned)
    cleaned = _TRAILING_HOST_RE.sub(" ", cleaned)
    cleaned = _DIGIT_RE.sub(" ", cleaned)
    return tuple(token for token in re.split(r"[^a-z0-9]+", cleaned) if token)


def fingerprint_for(parsed: ParsedIncident) -> str:
    if parsed.external_source and parsed.external_id:
        return f"{parsed.external_source}:{parsed.external_id}"[:300]
    tokens = sorted(set(normalize_title_tokens(parsed.title)))
    digest = hashlib.sha256("|".join(tokens).encode("utf-8")).hexdigest()
    return f"title:{digest}"[:300]


def _incident_fingerprint(incident: Incident) -> str:
    if incident.external_source and incident.external_id:
        return f"{incident.external_source}:{incident.external_id}"[:300]
    tokens = sorted(set(normalize_title_tokens(incident.title)))
    digest = hashlib.sha256("|".join(tokens).encode("utf-8")).hexdigest()
    return f"title:{digest}"[:300]


def _jaccard(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


async def alert_grouping_enabled(
    db: AsyncSession,
    org_id: uuid.UUID,
    service: Service | None,
) -> bool:
    if service is None:
        return False
    setting = service.alert_grouping or "inherit"
    if setting == "on":
        return True
    if setting == "off":
        return False
    org = await OrganizationRepo.get_by_id(db, org_id)
    return bool(org and org.alert_grouping_default)


async def _get_state(
    db: AsyncSession,
    org_id: uuid.UUID,
    service_id: uuid.UUID,
    fingerprint: str,
) -> AlertFingerprintState | None:
    stmt = select(AlertFingerprintState).where(
        AlertFingerprintState.org_id == org_id,
        AlertFingerprintState.service_id == service_id,
        AlertFingerprintState.fingerprint == fingerprint,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _get_or_create_state(
    db: AsyncSession,
    org_id: uuid.UUID,
    service_id: uuid.UUID,
    fingerprint: str,
    now: datetime,
) -> AlertFingerprintState:
    state = await _get_state(db, org_id, service_id, fingerprint)
    if state is not None:
        return state
    state = AlertFingerprintState(
        org_id=org_id,
        service_id=service_id,
        fingerprint=fingerprint,
        first_seen_at=now,
        last_seen_at=now,
        transitions=[],
    )
    db.add(state)
    await db.flush()
    return state


def _record_transition(
    state: AlertFingerprintState,
    *,
    kind: str,
    now: datetime,
) -> None:
    if state.first_seen_at is None:
        state.first_seen_at = now
    state.last_seen_at = now
    if kind == "fired":
        state.occurrences = int(state.occurrences or 0) + 1
    transitions = list(state.transitions or [])
    transitions.append({"at": now.isoformat(), "kind": kind})
    state.transitions = transitions[-50:]


def _transition_time(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed)


def _recent_transition_count(state: AlertFingerprintState, now: datetime) -> int:
    cutoff = now - timedelta(minutes=FLAP_WINDOW_MIN)
    count = 0
    for transition in state.transitions or []:
        if not isinstance(transition, dict):
            continue
        if transition.get("kind") not in {"fired", "cleared"}:
            continue
        at = _transition_time(transition.get("at"))
        if at is not None and at >= cutoff:
            count += 1
    return count


def _suppression_active(state: AlertFingerprintState, now: datetime) -> bool:
    until = _aware(state.flapping_until)
    return until is not None and until > now


async def is_flapping_suppression_active(
    db: AsyncSession,
    org_id: uuid.UUID,
    service: Service | None,
    parsed: ParsedIncident,
) -> bool:
    if service is None:
        return False
    state = await _get_state(db, org_id, service.id, fingerprint_for(parsed))
    return bool(state and _suppression_active(state, _utcnow()))


async def _emit_flapping_notification(
    db: AsyncSession,
    org_id: uuid.UUID,
    service: Service,
    incident: Incident | None,
    until: datetime,
) -> None:
    user_ids = await org_user_ids_with_roles(db, org_id, ("admin", "operator"))
    if not user_ids:
        return
    until_text = until.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    await emit_notification(
        db,
        org_id,
        user_ids[0],
        event_type="incident.flapping",
        category=CATEGORY_INCIDENT,
        title=f"Service {service.name} is flapping - paging suppressed until {until_text}",
        link=(
            f"/dashboard/incidents/detail?id={incident.id}"
            if incident is not None
            else None
        ),
        incident_id=incident.id if incident is not None else None,
    )


async def _maybe_trip_flapping(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    service: Service,
    state: AlertFingerprintState,
    incident: Incident | None,
    now: datetime,
) -> None:
    if _recent_transition_count(state, now) < FLAP_MIN_TRANSITIONS:
        return
    if incident is not None:
        incident.flapping = True
    if _suppression_active(state, now):
        return
    until = now + timedelta(minutes=FLAP_SUPPRESS_MIN)
    state.flapping_until = until
    await _emit_flapping_notification(db, org_id, service, incident, until)


async def _system_group_comment(
    db: AsyncSession,
    org_id: uuid.UUID,
    incident: Incident,
    title: str,
) -> None:
    await IncidentCommentRepo.create(
        db,
        org_id,
        incident_id=incident.id,
        body=f"Grouped alert: {title.strip() or 'Untitled alert'}",
        author_user_id=None,
        source="system",
    )


async def _matching_open_incident(
    db: AsyncSession,
    org_id: uuid.UUID,
    service: Service,
    parsed: ParsedIncident,
    fingerprint: str,
    now: datetime,
) -> Incident | None:
    since = now - timedelta(minutes=GROUPING_WINDOW_MIN)
    stmt = (
        select(Incident)
        .where(
            Incident.org_id == org_id,
            Incident.service_id == service.id,
            Incident.status.in_(("open", "in_progress")),
            Incident.updated_at >= since,
        )
        .order_by(Incident.updated_at.desc(), Incident.created_at.desc())
    )
    candidates = (await db.execute(stmt)).scalars().all()
    parsed_tokens = normalize_title_tokens(parsed.title)
    for candidate in candidates:
        if _incident_fingerprint(candidate) == fingerprint:
            return candidate
        if (
            _jaccard(parsed_tokens, normalize_title_tokens(candidate.title))
            >= GROUPING_SIMILARITY
        ):
            return candidate
    return None


async def evaluate_noise_before_create(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    service: Service | None,
    parsed: ParsedIncident,
    is_p0_refire: bool = False,
) -> NoiseDecision:
    if service is None or parsed.status == "resolved":
        return NoiseDecision()
    if not await alert_grouping_enabled(db, org_id, service):
        return NoiseDecision()

    now = _utcnow()
    fingerprint = fingerprint_for(parsed)
    state = await _get_or_create_state(db, org_id, service.id, fingerprint, now)
    if _suppression_active(state, now) and not is_p0_refire:
        incident = (
            await db.get(Incident, state.incident_id) if state.incident_id else None
        )
        _record_transition(state, kind="fired", now=now)
        state.incident_id = incident.id if incident is not None else state.incident_id
        if incident is not None:
            incident.flapping = True
            incident.updated_at = now
            await _system_group_comment(db, org_id, incident, parsed.title)
        await db.flush()
        return NoiseDecision(
            incident=incident,
            dedup_action="updated",
            suppressed=True,
        )

    match = await _matching_open_incident(db, org_id, service, parsed, fingerprint, now)
    if match is None:
        return NoiseDecision()

    match.correlated_count = int(match.correlated_count or 0) + 1
    match.updated_at = now
    state.incident_id = match.id
    _record_transition(state, kind="fired", now=now)
    await _system_group_comment(db, org_id, match, parsed.title)
    await _maybe_trip_flapping(
        db,
        org_id,
        service=service,
        state=state,
        incident=match,
        now=now,
    )
    await db.flush()
    return NoiseDecision(
        incident=match,
        dedup_action="updated",
        grouped=True,
    )


async def record_existing_transition(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    service: Service | None,
    parsed: ParsedIncident,
    incident: Incident,
    kind: str,
    is_p0_refire: bool = False,
) -> bool:
    if service is None or not await alert_grouping_enabled(db, org_id, service):
        return False
    now = _utcnow()
    state = await _get_or_create_state(
        db,
        org_id,
        service.id,
        fingerprint_for(parsed),
        now,
    )
    was_suppressed = _suppression_active(state, now)
    state.incident_id = incident.id
    _record_transition(state, kind=kind, now=now)
    if kind == "fired" and was_suppressed and not is_p0_refire:
        incident.flapping = True
        incident.updated_at = now
        await _system_group_comment(db, org_id, incident, parsed.title)
        await db.flush()
        return True
    await _maybe_trip_flapping(
        db,
        org_id,
        service=service,
        state=state,
        incident=incident,
        now=now,
    )
    await db.flush()
    return False


async def attach_created_incident(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    service: Service | None,
    parsed: ParsedIncident,
    incident: Incident,
) -> None:
    if service is None or not await alert_grouping_enabled(db, org_id, service):
        return
    now = _utcnow()
    state = await _get_or_create_state(
        db,
        org_id,
        service.id,
        fingerprint_for(parsed),
        now,
    )
    state.incident_id = incident.id
    _record_transition(state, kind="fired", now=now)
    await _maybe_trip_flapping(
        db,
        org_id,
        service=service,
        state=state,
        incident=incident,
        now=now,
    )
    await db.flush()
