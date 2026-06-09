"""v1 safety hardening — generic-tool guardrail, deny-list precedence, approval
routing, and conservative defaults at the backend tier gate.

These lock in the guarantee that the AI cannot execute beyond the selected tier
and the MCP Skill policy, regardless of prompt text.
"""

from __future__ import annotations

import uuid

import pytest

from backend.skills.parser import SkillDefinition, OperationClassification, loads
from backend.tiers.enforcement import check
from backend.tiers.generic_tools import is_generic_execution_tool

_ORG = uuid.UUID("00000000-0000-0000-0000-000000000000")


def _skill(*ops: OperationClassification) -> SkillDefinition:
    return SkillDefinition(version="1", environment="test", operations=list(ops))


# ---------------------------------------------------------------------------
# Generic command-execution guardrail
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["shell", "bash", "run_command", "kubectl", "aws_cli", "gcloud", "az",
     "terraform", "sql", "python", "exec_anything", "do_command", "run_query"],
)
def test_generic_tools_detected(name):
    assert is_generic_execution_tool(name) is True


@pytest.mark.parametrize("name", ["get_pods", "scale_deployment", "describe_node", "list_services"])
def test_normal_tools_not_generic(name):
    assert is_generic_execution_tool(name) is False


def test_generic_tool_blocked_at_tier_0_and_2():
    sd = _skill()  # no policy
    assert check("kubectl", 0, sd).permitted is False
    assert check("kubectl", 0, sd).classification == "generic_execution"
    assert check("kubectl", 2, sd).permitted is False


def test_generic_tool_requires_approval_at_tier_1():
    sd = _skill()
    r = check("run_command", 1, sd)
    assert r.permitted is True
    assert r.requires_approval is True
    assert r.classification == "generic_execution"


def test_generic_tool_opt_out_with_allow_generic():
    # allow_generic opts a narrowly-scoped wrapper out of the guardrail.
    sd = _skill(
        OperationClassification(tool="kubectl", classification="safe", allow_generic=True)
    )
    r = check("kubectl", 0, sd)
    # Now normal rules apply: safe + reversible runs at Tier 0.
    assert r.classification == "safe"
    assert r.permitted is True


def test_generic_opt_out_does_not_apply_to_glob():
    # allow_generic only applies to the exact matched entry; a different generic
    # tool with no entry is still guarded.
    sd = _skill(
        OperationClassification(tool="kubectl", classification="safe", allow_generic=True)
    )
    assert check("bash", 0, sd).permitted is False


# ---------------------------------------------------------------------------
# Deny-list precedence (deny always wins)
# ---------------------------------------------------------------------------


def test_deny_wins_over_safe_classification_all_tiers():
    sd = _skill(OperationClassification(tool="get_secret", classification="safe", deny=True))
    for tier in (0, 1, 2):
        r = check("get_secret", tier, sd)
        assert r.permitted is False
        assert "deny-list" in r.reason


def test_deny_wins_over_allow_generic():
    # deny beats allow_generic.
    sd = _skill(
        OperationClassification(
            tool="kubectl", classification="safe", allow_generic=True, deny=True
        )
    )
    assert check("kubectl", 1, sd).permitted is False


def test_deny_glob_pattern():
    sd = _skill(OperationClassification(tool="delete_*", classification="destructive", deny=True))
    assert check("delete_database", 0, sd).permitted is False
    assert check("delete_database", 1, sd).permitted is False


def test_deny_entry_without_classification_parses():
    # A deny entry may omit classification; it defaults to destructive.
    sd = loads(
        """---
version: "1"
environment: test
operations:
  - tool: drop_table
    deny: true
---
"""
    )
    r = check("drop_table", 0, sd)
    assert r.permitted is False
    assert "deny-list" in r.reason


# ---------------------------------------------------------------------------
# Conservative defaults: no/empty skill, unknown actions
# ---------------------------------------------------------------------------


def test_empty_skill_denies_write_actions_at_every_tier():
    sd = _skill()  # no operations at all
    for tier in (0, 1, 2):
        # Unknown (unclassified) write-like action is denied — never silently run.
        assert check("delete_pod", tier, sd).permitted is False


def test_destructive_requires_approval_at_tier_1():
    sd = _skill(OperationClassification(tool="delete_pod", classification="destructive"))
    r = check("delete_pod", 1, sd)
    assert r.permitted is True
    assert r.requires_approval is True


def test_destructive_blocked_at_tier_2():
    sd = _skill(OperationClassification(tool="delete_pod", classification="destructive"))
    assert check("delete_pod", 2, sd).permitted is False


# ---------------------------------------------------------------------------
# Auditor (Environment Scans) — read-only execution gate (Part 1 bypass fix)
# ---------------------------------------------------------------------------


class TestAuditorReadOnlyGate:
    """The auditor runs LLM-chosen MCP tools outside the session tier gate, so
    execute_call must independently allow ONLY read-only (safe) tools."""

    async def test_read_only_gate_blocks_non_safe_tools(self, tmp_path):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.db.models import Base
        from backend.db.repos import MCPServerRepo, SkillRepo
        from backend.auditor._helpers import _ensure_read_only, CallSpec
        from backend.auditor.base import AnalyzerContext, AnalyzerError

        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            srv = await MCPServerRepo.create(
                db, _ORG, name="k8s", transport="stdio", command="x", args=[]
            )
            await SkillRepo.create(
                db, _ORG, name="g", assignment="server", mcp_server_id=srv.id,
                content_md="""---
version: "1"
environment: t
operations:
  - tool: get_pods
    classification: safe
  - tool: delete_pod
    classification: destructive
  - tool: secret_read
    classification: safe
    deny: true
---
""",
            )
            await db.commit()

        async with factory() as db:
            ctx = AnalyzerContext(db=db, org_id=_ORG, pool=None, config=None, params={})

            def call(tool):
                return CallSpec(server_name="k8s", tool_name=tool, params={})

            # safe read-only tool passes the gate (no raise).
            await _ensure_read_only(ctx, call("get_pods"))
            # destructive blocked
            with pytest.raises(AnalyzerError):
                await _ensure_read_only(ctx, call("delete_pod"))
            # generic command tool blocked
            with pytest.raises(AnalyzerError):
                await _ensure_read_only(ctx, call("run_command"))
            # unknown tool blocked
            with pytest.raises(AnalyzerError):
                await _ensure_read_only(ctx, call("frobnicate"))
            # deny-listed (even though classified safe) blocked
            with pytest.raises(AnalyzerError):
                await _ensure_read_only(ctx, call("secret_read"))
        await engine.dispose()

    async def test_read_only_gate_blocks_everything_with_no_skill(self, tmp_path):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.db.models import Base
        from backend.auditor._helpers import _ensure_read_only, CallSpec
        from backend.auditor.base import AnalyzerContext, AnalyzerError

        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            ctx = AnalyzerContext(db=db, org_id=_ORG, pool=None, config=None, params={})
            # No skill at all -> even a read-looking tool is unknown -> blocked.
            with pytest.raises(AnalyzerError):
                await _ensure_read_only(
                    ctx, CallSpec(server_name="nope", tool_name="get_pods", params={})
                )
        await engine.dispose()


# ---------------------------------------------------------------------------
# Malformed policy / missing identifiers / case variants (Part 2 + Part 3)
# ---------------------------------------------------------------------------


def test_malformed_yaml_fails_closed():
    # Invalid YAML front-matter raises rather than silently producing a
    # permissive skill — the session runner therefore fails closed (no exec).
    bad = """---
version: 1
operations: [ this is : not valid yaml :::
---
"""
    with pytest.raises(Exception):
        loads(bad)


def test_missing_tool_identifier_denied():
    # An action with no/empty tool name never matches a policy entry -> unknown
    # -> denied at every tier (a missing identifier can't authorize execution).
    sd = _skill(OperationClassification(tool="delete_pod", classification="destructive"))
    for tier in (0, 1, 2):
        r = check("", tier, sd)
        assert r.permitted is False
        assert r.classification == "unknown"


@pytest.mark.parametrize("name", ["BASH", "KubeCtl", "RUN_COMMAND", "Shell", "AWS_CLI"])
def test_generic_detection_is_case_insensitive(name):
    assert is_generic_execution_tool(name) is True
    assert check(name, 0, _skill()).permitted is False


async def test_server_specific_deny_overrides_global_allow():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from backend.db.models import Base
    from backend.db.repos import MCPServerRepo, SkillRepo
    from backend.tiers.enforcement import load_skill_for_mcp_server

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        srv = await MCPServerRepo.create(
            db, _ORG, name="srv", transport="stdio", command="x", args=[]
        )
        # Global fallback ALLOWS restart_service (caution); server-specific DENIES it.
        await SkillRepo.create(
            db, _ORG, name="global", assignment="global",
            content_md="""---
version: "1"
environment: t
operations:
  - tool: restart_service
    classification: caution
    reversible: true
---
""",
        )
        await SkillRepo.create(
            db, _ORG, name="srv-skill", assignment="server", mcp_server_id=srv.id,
            content_md="""---
version: "1"
environment: t
operations:
  - tool: restart_service
    deny: true
---
""",
        )
        await db.commit()

    async with factory() as db:
        # The server-specific skill wins: under the global skill restart_service
        # would be permitted (caution @ Tier 1); under the resolved skill it is
        # deny-listed and therefore blocked — proving server-specific precedence.
        effective = await load_skill_for_mcp_server(db, _ORG, srv.id)
        assert effective.is_denied("restart_service") is True
        assert check("restart_service", 1, effective).permitted is False
    await engine.dispose()
