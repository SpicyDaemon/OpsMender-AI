"""LLM-based field extractor for the Universal ingest adapter.

When the heuristic pass cannot resolve a title from an inbound webhook
payload, this module asks the configured LLM to identify the JSON paths
for the incident fields. The resolved paths are cached on the token's
``shape_cache`` column keyed by a hash of the payload's top-level shape
— so the next payload with the same shape skips the LLM call entirely.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config_loader import AppConfig
from backend.db.models import IngestToken
from backend.db.repos import IngestTokenRepo, ModelConfigRepo
from backend.ingest.adapters.universal import (
    UniversalAdapter,
    _resolve_path,
)
from backend.llm.factory import create_provider

logger = logging.getLogger(__name__)

FIELD_NAMES = ("title", "description", "severity", "external_id", "status")

EXTRACT_PROMPT = """\
You receive an unknown webhook payload from an alerting tool (CloudWatch, Datadog, \
Grafana, Sumo Logic, Slack, custom script, etc.). Identify the JSON \
dot-paths that hold each incident field.

Return ONLY a JSON object with these keys (omit a key if nothing reasonable exists):

{{
  "title": "path.to.title",
  "description": "path.to.description",
  "severity": "path.to.severity",
  "external_id": "path.to.unique_id",
  "status": "path.to.status"
}}

Rules:
- Paths use dots for nested objects and integer indexes for arrays (e.g. \
"records.0.alert.name").
- title should be a short human-readable label for the alert.
- external_id should uniquely identify this alerting condition within its source \
so OpsMender can deduplicate repeated notifications — prefer stable IDs over timestamps.
- severity should be a path to a field whose value maps to critical/high/medium/low \
(priority numbers, sev labels, etc. are fine).
- status should be a path to a field whose value indicates whether the alert is \
firing, resolved, or acknowledged.
- If no path fits a field, omit the key rather than guessing.

Payload (abbreviated):
{payload_preview}
"""


def compute_shape_hash(payload: Any) -> str:
    """Hash the *structure* of a payload — keys and types, not values."""
    skeleton = _skeleton(payload)
    serialized = json.dumps(skeleton, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]


def _skeleton(value: Any) -> Any:
    """Reduce a payload to structure only: dicts → key→type, lists → [type]."""
    if isinstance(value, dict):
        return {k: _skeleton(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        if not value:
            return []
        return [_skeleton(value[0])]
    if value is None:
        return "null"
    return type(value).__name__


def _abbreviate(value: Any, *, max_str: int = 200, max_items: int = 5) -> Any:
    """Shrink a payload so long values don't blow the LLM context."""
    if isinstance(value, dict):
        return {
            k: _abbreviate(v, max_str=max_str, max_items=max_items)
            for k, v in value.items()
        }
    if isinstance(value, list):
        out = [
            _abbreviate(v, max_str=max_str, max_items=max_items)
            for v in value[:max_items]
        ]
        if len(value) > max_items:
            out.append(f"…(+{len(value) - max_items} more)")
        return out
    if isinstance(value, str) and len(value) > max_str:
        return value[:max_str] + "…"
    return value


def _parse_llm_json(text: str) -> dict[str, str] | None:
    """Extract a JSON object of field→path pairs from LLM output."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None

    if not isinstance(data, dict):
        return None

    result: dict[str, str] = {}
    for field in FIELD_NAMES:
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            result[field] = value.strip()
    return result


def _resolve_model_kwargs(config: AppConfig, model_cfg) -> dict[str, Any]:
    # Delegate to the canonical resolver so env-driven openai_compatible
    # base_url (and any future provider additions) stay in one place.
    from backend.auditor._helpers import resolve_provider_kwargs

    return resolve_provider_kwargs(config, model_cfg)


async def extract_paths_via_llm(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    payload: dict[str, Any],
    config: AppConfig,
) -> dict[str, str] | None:
    """Ask the default LLM which paths hold the incident fields."""
    try:
        model_cfg = await ModelConfigRepo.get_default(db, org_id)
        provider = create_provider(**_resolve_model_kwargs(config, model_cfg))
    except Exception as exc:
        logger.warning("ingest.llm_extract: provider init failed: %s", exc)
        return None

    preview = json.dumps(_abbreviate(payload), indent=2, default=str)
    prompt = EXTRACT_PROMPT.format(payload_preview=preview)

    try:
        text = await asyncio.to_thread(provider.complete, prompt)
    except Exception as exc:
        logger.warning("ingest.llm_extract: completion failed: %s", exc)
        return None

    paths = _parse_llm_json(text)
    if not paths:
        logger.warning("ingest.llm_extract: could not parse JSON from LLM output")
        return None

    # Validate each path actually resolves against the payload. Drop bad ones.
    validated: dict[str, str] = {}
    for field, path in paths.items():
        if _resolve_path(payload, path) not in (None, "", [], {}):
            validated[field] = path

    return validated or None


async def apply_shape_cache(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    token: IngestToken,
    payload: dict[str, Any],
    config: AppConfig,
) -> tuple[dict[str, str] | None, bool]:
    """Return field paths for this payload shape, using cache or LLM."""
    shape = compute_shape_hash(payload)
    cached = (token.shape_cache or {}).get(shape)
    if isinstance(cached, dict):
        return cached, True

    paths = await extract_paths_via_llm(db, org_id, payload=payload, config=config)
    if paths:
        next_cache = dict(token.shape_cache or {})
        next_cache[shape] = paths
        await IngestTokenRepo.update_shape_cache(db, org_id, token.id, next_cache)
        token.shape_cache = next_cache  # refresh in-memory too
    return paths, False


def parse_with_paths(
    payload: dict[str, Any],
    paths: dict[str, str] | None,
):
    """Run the Universal adapter with pre-resolved paths injected."""
    adapter = UniversalAdapter(field_mapping=paths or {})
    return adapter.parse(payload)
