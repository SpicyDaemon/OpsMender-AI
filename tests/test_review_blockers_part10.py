"""Round-2 review blockers (X7): deny ordering in the tier gate (KI-045) and
intake URLs hidden from viewers (KI-047). The approval, merge-recovery and
escalate-now fixes are tested beside their existing suites."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from backend.integrations.base import IntegrationCapability
from backend.integrations.tools import (
    IntegrationToolDescriptor,
    IntegrationToolRuntime,
    _authored_policy,
    merge_integration_skill,
)
from backend.skills.parser import (
    OperationClassification,
    OperationTierPolicy,
    SkillDefinition,
)
from backend.tiers.enforcement import check
from backend.tiers.sandbox import Tier0Sandbox
from tests.test_connector_instructions_part7 import _connector, _empty_base
from tests.test_ingest import (
    _create_paged_service,
    admin_headers as _admin_headers_fixture,
    app as _app_fixture,
    client as _client_fixture,
    viewer_headers as _viewer_headers_fixture,
)
from tests.test_tool_source_overlap import (
    integration_db as _integration_db_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
admin_headers = pytest.fixture(_admin_headers_fixture.__wrapped__)
viewer_headers = pytest.fixture(_viewer_headers_fixture.__wrapped__)
integration_db = pytest.fixture(_integration_db_fixture.__wrapped__)

_AUTONOMOUS = {
    0: OperationTierPolicy(enabled=True, mode="autonomous"),
    1: OperationTierPolicy(enabled=True, mode="autonomous"),
    2: OperationTierPolicy(enabled=True, mode="advisory"),
}


def _op(tool: str, **kw) -> OperationClassification:
    kw.setdefault("classification", "safe")
    kw.setdefault("tiers", dict(_AUTONOMOUS))
    return OperationClassification(tool=tool, **kw)


def _skill(*ops: OperationClassification) -> SkillDefinition:
    return SkillDefinition(version="1", environment="t", operations=list(ops))


# ── KI-045: deny wins whatever the order ───────────────────────────────────


@pytest.mark.parametrize("deny_first", [False, True])
def test_a_deny_after_a_broad_glob_still_blocks_at_every_tier(deny_first):
    deny = _op("delete_*", classification="destructive", deny=True)
    everything = _op("*")
    skill = _skill(deny, everything) if deny_first else _skill(everything, deny)

    for tier in (0, 1, 2):
        result = check("delete_namespace", tier, skill)
        assert result.permitted is False, (tier, result.reason)
        assert "deny-list" in result.reason
    tools = [SimpleNamespace(name="delete_namespace"), SimpleNamespace(name="get_pods")]
    sandbox = Tier0Sandbox.from_skill(skill, available_tools=tools)
    assert sandbox.allowed_tool_names == frozenset({"get_pods"})


def test_an_exact_entry_cannot_carve_an_exception_out_of_a_deny_glob():
    skill = _skill(
        _op("delete_*", classification="destructive", deny=True),
        _op("delete_tmp_files"),
    )
    for tier in (0, 1, 2):
        assert check("delete_tmp_files", tier, skill).permitted is False


def test_allow_generic_counts_only_on_an_exact_entry():
    glob = _skill(_op("run_*", allow_generic=True))
    assert glob.allows_generic("run_command") is False
    assert check("run_command", 0, glob).permitted is False
    exact = _skill(_op("run_command", allow_generic=True))
    assert exact.allows_generic("run_command") is True


def test_a_connector_skill_deny_after_a_glob_blocks_its_tool():
    connector = uuid.uuid4()
    name = f"integration__jira__create_issue__{connector.hex}"
    # A glob deny below a broader glob: first-match would pick the broad one.
    connector_skill = _skill(
        _op("integration__jira__*"),
        _op("integration__jira__create_issue__*", deny=True),
    )
    descriptor = IntegrationToolDescriptor(
        name=name,
        description="Create an issue",
        connector_id=connector,
        capability=IntegrationCapability(
            action="create_issue",
            description="Create an issue",
            mutating=True,
            classification="caution",
        ),
        authored_operation=_authored_policy(connector_skill, name),
    )
    merged = merge_integration_skill(_skill(), [descriptor])
    assert check(name, 1, merged).permitted is False


async def test_a_stored_connector_skill_deny_after_a_glob_blocks_at_runtime(
    integration_db,
):
    factory, org_id = integration_db
    skill_md = (
        "---\n"
        'version: "1"\n'
        "environment: jira-connector\n"
        "operations:\n"
        '  - tool: "integration__jira__*"\n'
        "    classification: caution\n"
        "    tiers:\n"
        "      T0: {enabled: true, mode: autonomous}\n"
        "      T1: {enabled: true, mode: autonomous}\n"
        "      T2: {enabled: true, mode: advisory}\n"
        '  - tool: "integration__jira__create_issue__*"\n'
        "    deny: true\n"
        "---\n"
    )
    jira = await _connector(factory, org_id, "Jira", skill_md)
    runtime = await IntegrationToolRuntime.create(
        factory, org_id, allowed_connector_ids={jira}
    )
    create = f"integration__jira__create_issue__{jira.hex}"
    (descriptor,) = [d for d in runtime.descriptors if d.name == create]
    assert descriptor.authored_operation.deny is True
    merged = merge_integration_skill(
        _empty_base(), runtime.descriptors, runtime.connector_instructions
    )
    for tier in (0, 1, 2):
        assert check(create, tier, merged).permitted is False


# ── KI-047: intake URLs for admins and operators only ──────────────────────


async def test_viewers_do_not_get_intake_urls(
    app, client: AsyncClient, admin_headers, viewer_headers
):
    await _create_paged_service(client, app, admin_headers, name="Secret")
    as_admin = await client.get("/services", headers=admin_headers)
    as_viewer = await client.get("/services", headers=viewer_headers)
    assert as_viewer.status_code == 200
    assert all(item["intake_url"] for item in as_admin.json()["items"])
    assert all(item["intake_url"] is None for item in as_viewer.json()["items"])
