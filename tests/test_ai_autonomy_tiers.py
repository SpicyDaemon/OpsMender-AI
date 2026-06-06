"""AI Autonomy 3-tier model — enforcement, normalization, and skill assignment.

Covers the safety-critical behaviours of the tier rework:
  - Tier 2 (advisory) is the default and blocks all remediation.
  - Tier 0/1 permit execution per policy.
  - Legacy Tier 3 normalizes to Tier 2.
  - Unassigned skills are never injected; global fallback vs server precedence.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, MCPServer
from backend.db.repos import MCPServerRepo, SkillRepo
from backend.skills.parser import SkillDefinition, OperationClassification
from backend.skills.template import build_skill_template
from backend.tiers.enforcement import check, normalize_tier

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")

_SKILL_DEF = SkillDefinition(
    version="1",
    environment="test",
    operations=[
        OperationClassification(tool="get_pods", classification="safe"),
        OperationClassification(tool="delete_pod", classification="destructive"),
    ],
)


# ---------------------------------------------------------------------------
# Tier normalization + enforcement
# ---------------------------------------------------------------------------


def test_normalize_tier_remaps_three_to_two():
    assert normalize_tier(3) == 2
    assert normalize_tier(2) == 2
    assert normalize_tier(1) == 1
    assert normalize_tier(0) == 0
    # Out-of-range clamps to the safest (advisory), never more permissive.
    assert normalize_tier(5) == 2
    assert normalize_tier(-1) == 2


def test_tier_2_advisory_blocks_all_remediation():
    # Safe, caution, and destructive are all blocked under advisory Tier 2.
    assert check("get_pods", 2, _SKILL_DEF).permitted is False
    assert check("delete_pod", 2, _SKILL_DEF).permitted is False
    assert "advisory" in check("get_pods", 2, _SKILL_DEF).reason


def test_tier_0_and_1_permit_safe_execution():
    assert check("get_pods", 0, _SKILL_DEF).permitted is True
    assert check("get_pods", 1, _SKILL_DEF).permitted is True


def test_legacy_tier_3_behaves_as_advisory():
    r = check("get_pods", 3, _SKILL_DEF)
    assert r.tier == 2
    assert r.permitted is False


def test_unknown_action_never_silently_allowed():
    for tier in (0, 1, 2, 3):
        assert check("mystery_tool", tier, _SKILL_DEF).permitted is False


def test_template_parses_and_classifies():
    sd_loaded = __import__("backend.skills.parser", fromlist=["loads"]).loads(
        build_skill_template()
    )
    assert sd_loaded.classify("get_pods") == "safe"
    assert sd_loaded.classify("restart_service") == "caution"
    # The template advertises the 3-tier sections.
    md = build_skill_template()
    assert "Tier 0 — Autonomous" in md
    assert "Tier 1 — Approval Required" in md
    assert "Tier 2 — Advisory Only" in md
    assert "No actions allowed. Advisory mode only." in md


# ---------------------------------------------------------------------------
# Skill assignment precedence (repo level)
# ---------------------------------------------------------------------------


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _server(factory, name="srv") -> uuid.UUID:
    async with factory() as db:
        s = await MCPServerRepo.create(
            db, TEST_ORG_ID, name=name, transport="stdio", command="x", args=[]
        )
        await db.commit()
        return s.id


async def test_unassigned_skill_not_injected(factory):
    async with factory() as db:
        await SkillRepo.create(
            db, TEST_ORG_ID, name="draft", content_md=build_skill_template(),
            assignment="unassigned",
        )
        await db.commit()
    async with factory() as db:
        # No global, only an unassigned draft -> nothing resolves for a session.
        eff = await SkillRepo.get_for_mcp_server(db, TEST_ORG_ID, None)
        assert eff is None


async def test_global_fallback_applies_when_no_server_skill(factory):
    srv = await _server(factory)
    async with factory() as db:
        await SkillRepo.create(
            db, TEST_ORG_ID, name="global", content_md=build_skill_template(),
            assignment="global",
        )
        await db.commit()
    async with factory() as db:
        eff = await SkillRepo.get_for_mcp_server(db, TEST_ORG_ID, srv)
        assert eff is not None
        assert eff.name == "global"


async def test_server_specific_overrides_global(factory):
    srv = await _server(factory)
    async with factory() as db:
        await SkillRepo.create(
            db, TEST_ORG_ID, name="global", content_md=build_skill_template(),
            assignment="global",
        )
        await SkillRepo.create(
            db, TEST_ORG_ID, name="specific", content_md=build_skill_template(),
            mcp_server_id=srv, assignment="server",
        )
        await db.commit()
    async with factory() as db:
        eff = await SkillRepo.get_for_mcp_server(db, TEST_ORG_ID, srv)
        assert eff is not None
        assert eff.name == "specific"


async def test_unassigned_create_clears_server_binding(factory):
    srv = await _server(factory)
    async with factory() as db:
        skill = await SkillRepo.create(
            db, TEST_ORG_ID, name="draft", content_md=build_skill_template(),
            mcp_server_id=srv, assignment="unassigned",
        )
        await db.commit()
        # Unassigned drafts never carry a server binding.
        assert skill.assignment == "unassigned"
        assert skill.mcp_server_id is None
