"""Model selection for service-bound incidents and sessions."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import ModelConfig
from backend.db.repos import ModelConfigRepo, ServiceRepo, SessionRepo


async def has_active_model_configs(
    db: AsyncSession,
    org_id: uuid.UUID,
) -> bool:
    return any(model.is_active for model in await ModelConfigRepo.list_all(db, org_id))


async def choose_model_config_by_identity(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    provider: str,
    model_id: str,
    respect_capacity: bool = False,
) -> tuple[ModelConfig | None, bool]:
    """Resolve an explicit provider/model pair to a saved config.

    Returns ``(available_config, has_matching_config)`` so callers can preserve
    legacy ad-hoc provider/model starts while still enforcing caps whenever the
    pair belongs to one or more saved configurations.
    """
    matches = [
        model
        for model in await ModelConfigRepo.list_all(db, org_id)
        if model.is_active
        and model.provider == provider
        and model.model_id == model_id
    ]
    if not matches:
        return None, False
    if not respect_capacity:
        return matches[0], True

    locked = list(await ModelConfigRepo.list_all_for_update(db, org_id))
    occupancy = await SessionRepo.active_occupancy_by_model_config(db, org_id)
    matching_ids = {model.id for model in matches}
    for model in locked:
        if model.id not in matching_ids or not model.is_active:
            continue
        cap = model.max_concurrent_sessions
        if cap is None or cap <= 0 or occupancy.get(model.id, 0) < cap:
            return model, True
    return None, True


async def choose_model_for_incident_service(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    service_id: uuid.UUID | None,
    ingestion_model_config_id: uuid.UUID | None = None,
    respect_capacity: bool = False,
):
    """Choose an active model using incident, service, then org preference.

    Capacity checks are opt-in because this helper is also used by ingest/triage
    classification, which deliberately does not consume incident-session slots.
    """
    models = list(await ModelConfigRepo.list_all(db, org_id))
    by_id = {model.id: model for model in models if model.is_active}
    ordered_ids: list[uuid.UUID] = []

    def add_candidate(raw_id) -> None:
        if raw_id is None:
            return
        try:
            config_id = uuid.UUID(str(raw_id))
        except (TypeError, ValueError):
            return
        if config_id in by_id and config_id not in ordered_ids:
            ordered_ids.append(config_id)

    add_candidate(ingestion_model_config_id)

    service = (
        await ServiceRepo.get_by_id(db, org_id, service_id)
        if service_id is not None
        else None
    )
    if service is not None:
        for raw_id in service.preferred_model_config_ids or []:
            add_candidate(raw_id)

    default_model = await ModelConfigRepo.get_default(db, org_id)
    if default_model is not None and default_model.is_active:
        add_candidate(default_model.id)

    for model in models:
        if model.is_active:
            add_candidate(model.id)

    if not ordered_ids:
        return None
    if not respect_capacity:
        return by_id[ordered_ids[0]]

    # Lock all model rows in a stable order before counting occupancy. This
    # serializes concurrent allocators in PostgreSQL and prevents two requests
    # from both claiming the final slot. SQLite ignores FOR UPDATE in tests.
    locked = list(await ModelConfigRepo.list_all_for_update(db, org_id))
    locked_by_id = {model.id: model for model in locked if model.is_active}
    occupancy = await SessionRepo.active_occupancy_by_model_config(db, org_id)
    for config_id in ordered_ids:
        model = locked_by_id.get(config_id)
        if model is None:
            continue
        cap = model.max_concurrent_sessions
        if cap is None or cap <= 0 or occupancy.get(config_id, 0) < cap:
            return model

    return None
