"""M1-65: a stored key setting that is not a variable name is hidden from every
role and reported at startup by id; explain is not a safe verb; a bare
published key that does not fit is a refusal (tests/test_sso.py)."""

from __future__ import annotations

import logging
import uuid

import pytest

from backend.skills.suggest import suggest_classification
from tests.test_model_discovery_access import PASTED_KEY, _viewer_token
from tests.test_role_access import _headers, client as _client_fixture

client = pytest.fixture(_client_fixture.__wrapped__)


async def test_a_stored_pasted_key_is_hidden_from_every_role_and_reported_by_id(
    client, caplog
):
    from backend.api.deps import get_current_session_factory
    from backend.db.repos import ModelConfigRepo
    from backend.llm.bootstrap import report_invalid_key_settings

    h = await _headers(client)
    me = (await client.get("/auth/me", headers=h["admin"])).json()
    org_id = uuid.UUID(me["primary_org_id"])
    factory = get_current_session_factory()
    async with factory() as db:
        pasted = await ModelConfigRepo.create(
            db,
            org_id,
            name="pasted-before-m1-60",
            provider="openai_compatible",
            model_id="m",
            api_key_env_var=PASTED_KEY,
            is_default=True,
        )
        named = await ModelConfigRepo.create(
            db,
            org_id,
            name="named",
            provider="openai_compatible",
            model_id="m",
            api_key_env_var="OPENAI_API_KEY",
        )
        await db.commit()
        pasted_id, named_id = str(pasted.id), str(named.id)

    for headers in (
        h["admin"],
        h["operator"],
        h["viewer"],
        await _viewer_token(client, h),
    ):
        configs = await client.get("/models/configs", headers=headers)
        assert configs.status_code == 200, configs.text
        assert PASTED_KEY not in configs.text
        items = {item["id"]: item for item in configs.json()["items"]}
        assert items[pasted_id]["api_key_env_var"] is None
        assert items[pasted_id]["key_setting_invalid"] is True
        assert items[named_id]["api_key_env_var"] == "OPENAI_API_KEY"
        assert items[named_id]["key_setting_invalid"] is False

        bootstrap = await client.get("/models/bootstrap", headers=headers)
        assert bootstrap.status_code == 200, bootstrap.text
        assert PASTED_KEY not in bootstrap.text
        assert bootstrap.json()["default_config"]["key_setting_invalid"] is True

    with caplog.at_level(logging.WARNING, logger="backend.llm.bootstrap"):
        reported = await report_invalid_key_settings(factory)
    assert [str(config_id) for config_id in reported] == [pasted_id]
    assert pasted_id in caplog.text and PASTED_KEY not in caplog.text


def test_explain_is_not_a_safe_verb():
    suggestion = suggest_classification("explain_query_plan")
    assert suggestion.classification == "caution"
    assert suggestion.needs_review is True
    assert suggest_classification("describe_table").classification == "safe"
