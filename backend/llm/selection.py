"""Model selection for service-bound incidents and sessions."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.repos import ModelConfigRepo, ServiceRepo


async def choose_model_for_incident_service(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    service_id: uuid.UUID | None,
    ingestion_model_config_id: uuid.UUID | None = None,
):
    """Choose an active model using incident, service, then org preference."""
    if ingestion_model_config_id is not None:
        ingestion_model = await ModelConfigRepo.get_by_id(
            db, org_id, ingestion_model_config_id
        )
        if ingestion_model is not None and ingestion_model.is_active:
            return ingestion_model

    if service_id is not None:
        service = await ServiceRepo.get_by_id(db, org_id, service_id)
        for raw_id in service.preferred_model_config_ids if service is not None else []:
            try:
                config_id = uuid.UUID(str(raw_id))
            except (TypeError, ValueError):
                continue
            model = await ModelConfigRepo.get_by_id(db, org_id, config_id)
            if model is not None and model.is_active:
                return model

    default_model = await ModelConfigRepo.get_default(db, org_id)
    if default_model is not None and default_model.is_active:
        return default_model

    return next(
        (
            model
            for model in await ModelConfigRepo.list_all(db, org_id)
            if model.is_active
        ),
        None,
    )
