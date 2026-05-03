"""Tests for Sprint 12 Feature 3 — skill manager.

Covers:
- SkillRepo CRUD + mcp_server fallback chain
- Auto-import scan of the ``skills/`` directory
- Parser ``loads()`` helper
- REST endpoints (list/get/create/update/delete/clone/import)
- Skill enforcement helper ``load_skill_for_mcp_server`` pulling from DB
"""

from __future__ import annotations

import io
import json
import uuid

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_session_factory
from backend.config_loader import set_env_path
from backend.db.models import Base
from backend.db.repos import MCPServerRepo, SkillRepo
from backend.skills.importer import auto_import
from backend.skills.parser import loads as parse_skill_content
from backend.tiers.enforcement import load_skill_for_mcp_server


SAMPLE_SKILL = """---
version: "1"
environment: sample
operations:
  - tool: get_pods
    classification: safe
  - tool: scale_deployment
    classification: caution
  - tool: delete_*
    classification: destructive
---

# Sample skill
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        from backend.db.models import Organization
        org = Organization(id=TEST_ORG_ID, name="Test Org", slug="test-org")
        session.add(org)
        await session.commit()
    yield factory
    await engine.dispose()


@pytest.fixture
async def db(db_factory):
    async with db_factory() as session:
        yield session


@pytest.fixture
async def app(tmp_path, db_factory):
    set_session_factory(db_factory)

    tmp_env = tmp_path / ".env"
    tmp_env.write_text(
        "AIM_TIER=2\n"
        "AIM_JWT_SECRET=test-secret\n"
        "AIM_DATABASE_URL=sqlite+aiosqlite://\n"
        f"AIM_MCP_SERVERS_JSON={json.dumps([])}\n"
    )
    set_env_path(tmp_env)

    application = create_app()
    application.state.session_factory = db_factory

    async def _override_get_db():
        async with db_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    application.dependency_overrides[get_db] = _override_get_db

    yield application

    set_env_path(None)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    await client.post(
        "/auth/register",
        json={
            "username": "skilladmin",
            "email": "skilladmin@test.com",
            "password": "securepass123",
        },
    )
    # The user is automatically linked to TEST_ORG_ID by /auth/register in the test environment
    # because it's the first organization created in the seeded DB.

    resp = await client.post(
        "/auth/login",
        json={"username": "skilladmin", "password": "securepass123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def viewer_headers(client: AsyncClient, auth_headers) -> dict[str, str]:
    await client.post(
        "/auth/register",
        json={
            "username": "skillviewer",
            "email": "skillviewer@test.com",
            "password": "viewerpass123",
            "role": "viewer",
        },
    )
    # The user is automatically linked to TEST_ORG_ID by /auth/register

    resp = await client.post(
        "/auth/login",
        json={"username": "skillviewer", "password": "viewerpass123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class TestParserLoads:
    def test_parses_front_matter(self):
        skill = parse_skill_content(SAMPLE_SKILL)
        assert skill.environment == "sample"
        assert skill.classify("get_pods") == "safe"
        assert skill.classify("delete_namespace") == "destructive"
        assert skill.classify("unknown_tool") == "unknown"

    def test_yaml_mode(self):
        yaml_text = "version: '1'\nenvironment: raw\noperations:\n  - tool: get_pods\n    classification: safe\n"
        skill = parse_skill_content(yaml_text, fmt="yaml")
        assert skill.environment == "raw"
        assert skill.classify("get_pods") == "safe"


# ---------------------------------------------------------------------------
# SkillRepo
# ---------------------------------------------------------------------------


class TestSkillRepo:
    async def test_create_and_get(self, db: AsyncSession):
        skill = await SkillRepo.create(
            db,
            TEST_ORG_ID,
            name="prod",
            content_md=SAMPLE_SKILL,
            description="Production",
        )
        await db.flush()

        fetched = await SkillRepo.get_by_id(db, TEST_ORG_ID, skill.id)
        assert fetched is not None
        assert fetched.name == "prod"
        assert fetched.description == "Production"
        assert fetched.content_md.startswith("---")

    async def test_get_by_name(self, db: AsyncSession):
        await SkillRepo.create(db, TEST_ORG_ID, name="stage", content_md=SAMPLE_SKILL)
        await db.flush()
        assert (await SkillRepo.get_by_name(db, TEST_ORG_ID, "stage")) is not None
        assert (await SkillRepo.get_by_name(db, TEST_ORG_ID, "missing")) is None

    async def test_list_for_mcp_server(self, db: AsyncSession):
        server = await MCPServerRepo.create(
            db, TEST_ORG_ID, name="k8s-prod", transport="stdio", command="echo"
        )
        await db.flush()

        await SkillRepo.create(
            db,
            TEST_ORG_ID,
            name="bound-1",
            content_md=SAMPLE_SKILL,
            mcp_server_id=server.id,
        )
        await SkillRepo.create(
            db,
            TEST_ORG_ID,
            name="bound-2",
            content_md=SAMPLE_SKILL,
            mcp_server_id=server.id,
        )
        await SkillRepo.create(db, TEST_ORG_ID, name="global", content_md=SAMPLE_SKILL)
        await db.flush()

        bound = await SkillRepo.list_for_mcp_server(db, TEST_ORG_ID, server.id)
        assert {s.name for s in bound} == {"bound-1", "bound-2"}

    async def test_get_for_mcp_server_falls_back_to_global(self, db: AsyncSession):
        server = await MCPServerRepo.create(
            db, TEST_ORG_ID, name="k8s-prod", transport="stdio", command="echo"
        )
        await db.flush()

        await SkillRepo.create(
            db, TEST_ORG_ID, name="global-only", content_md=SAMPLE_SKILL
        )
        await db.flush()

        fallback = await SkillRepo.get_for_mcp_server(db, TEST_ORG_ID, server.id)
        assert fallback is not None
        assert fallback.name == "global-only"

    async def test_get_for_mcp_server_prefers_bound(self, db: AsyncSession):
        server = await MCPServerRepo.create(
            db, TEST_ORG_ID, name="k8s-prod", transport="stdio", command="echo"
        )
        await db.flush()

        await SkillRepo.create(db, TEST_ORG_ID, name="global", content_md=SAMPLE_SKILL)
        await SkillRepo.create(
            db,
            TEST_ORG_ID,
            name="bound",
            content_md=SAMPLE_SKILL,
            mcp_server_id=server.id,
        )
        await db.flush()

        match = await SkillRepo.get_for_mcp_server(db, TEST_ORG_ID, server.id)
        assert match is not None
        assert match.name == "bound"

    async def test_get_for_mcp_server_none_when_empty(self, db: AsyncSession):
        assert await SkillRepo.get_for_mcp_server(db, TEST_ORG_ID, None) is None

    async def test_update(self, db: AsyncSession):
        skill = await SkillRepo.create(
            db, TEST_ORG_ID, name="v1", content_md=SAMPLE_SKILL
        )
        await db.flush()

        updated = await SkillRepo.update(
            db,
            TEST_ORG_ID,
            skill.id,
            name="v2",
            content_md=SAMPLE_SKILL + "\n# changed",
            description="new",
        )
        assert updated is not None
        assert updated.name == "v2"
        assert updated.description == "new"

    async def test_delete(self, db: AsyncSession):
        skill = await SkillRepo.create(
            db, TEST_ORG_ID, name="gone", content_md=SAMPLE_SKILL
        )
        await db.flush()

        assert await SkillRepo.delete(db, TEST_ORG_ID, skill.id) is True
        assert await SkillRepo.get_by_id(db, TEST_ORG_ID, skill.id) is None
        assert await SkillRepo.delete(db, TEST_ORG_ID, uuid.uuid4()) is False


# ---------------------------------------------------------------------------
# Auto-import
# ---------------------------------------------------------------------------


class TestAutoImport:
    async def test_imports_new_files(self, db_factory, tmp_path):
        (tmp_path / "production").mkdir()
        (tmp_path / "production" / "SKILL.md").write_text(SAMPLE_SKILL)
        (tmp_path / "sandbox.md").write_text(SAMPLE_SKILL)

        result = await auto_import(db_factory, skills_dir=tmp_path)

        assert set(result.imported) == {"production", "sandbox"}
        assert result.skipped == []
        assert result.failed == []

        async with db_factory() as db:
            names = [s.name for s in await SkillRepo.list_all(db, TEST_ORG_ID)]
        assert set(names) == {"production", "sandbox"}

    async def test_skips_existing(self, db_factory, tmp_path):
        (tmp_path / "SKILL.md").write_text(SAMPLE_SKILL)

        async with db_factory() as db:
            await SkillRepo.create(
                db, TEST_ORG_ID, name="SKILL", content_md="pre-existing"
            )
            await db.commit()

        result = await auto_import(db_factory, skills_dir=tmp_path)
        assert result.imported == []
        assert result.skipped == ["SKILL"]

    async def test_missing_directory_is_noop(self, db_factory, tmp_path):
        missing = tmp_path / "does-not-exist"
        result = await auto_import(db_factory, skills_dir=missing)
        assert result.imported == result.skipped == []
        assert result.failed == []

    async def test_invalid_file_goes_to_failed(self, db_factory, tmp_path):
        (tmp_path / "bad.md").write_text("---\n: not valid yaml ::\n---\n")
        result = await auto_import(db_factory, skills_dir=tmp_path)
        assert result.imported == []
        assert len(result.failed) == 1

    async def test_examples_are_not_imported(self, db_factory, tmp_path):
        # Simulate the real layout: skills/ empty, examples/ present.
        examples = tmp_path / "examples"
        examples.mkdir()
        (examples / "SKILL.md").write_text(SAMPLE_SKILL)
        skills = tmp_path / "skills"
        skills.mkdir()

        result = await auto_import(db_factory, skills_dir=skills)
        assert result.imported == []


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------


class TestSkillsAPI:
    async def test_empty_list(self, client: AsyncClient, auth_headers):
        resp = await client.get("/skills", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    async def test_create_and_list(self, client: AsyncClient, auth_headers):
        resp = await client.post(
            "/skills",
            json={
                "name": "prod",
                "content_md": SAMPLE_SKILL,
                "description": "Production",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["name"] == "prod"
        assert resp.json()["description"] == "Production"

        resp = await client.get("/skills", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    async def test_viewer_cannot_create(self, client: AsyncClient, viewer_headers):
        resp = await client.post(
            "/skills",
            json={"name": "nope", "content_md": SAMPLE_SKILL},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    async def test_viewer_can_read(self, client: AsyncClient, app, viewer_headers):
        async with app.state.session_factory() as db:
            await SkillRepo.create(
                db, TEST_ORG_ID, name="readable", content_md=SAMPLE_SKILL
            )
            await db.commit()

        resp = await client.get("/skills", headers=viewer_headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    async def test_create_invalid_content_rejected(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.post(
            "/skills",
            json={"name": "broken", "content_md": "---\n: bad yaml ::\n---\n"},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_create_duplicate_name_conflict(
        self, client: AsyncClient, auth_headers
    ):
        payload = {"name": "dup", "content_md": SAMPLE_SKILL}
        first = await client.post("/skills", json=payload, headers=auth_headers)
        assert first.status_code == 201
        second = await client.post("/skills", json=payload, headers=auth_headers)
        assert second.status_code == 409

    async def test_filter_by_mcp_server(self, client: AsyncClient, app, auth_headers):
        async with app.state.session_factory() as db:
            server = await MCPServerRepo.create(
                db, TEST_ORG_ID, name="k8s-prod", transport="stdio", command="echo"
            )
            await db.commit()
            await db.refresh(server)
            server_id = server.id

            await SkillRepo.create(
                db,
                TEST_ORG_ID,
                name="bound",
                content_md=SAMPLE_SKILL,
                mcp_server_id=server_id,
            )
            await SkillRepo.create(
                db, TEST_ORG_ID, name="global", content_md=SAMPLE_SKILL
            )
            await db.commit()

        resp = await client.get(
            f"/skills?mcp_server_id={server_id}", headers=auth_headers
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["name"] == "bound"

    async def test_update_skill(self, client: AsyncClient, app, auth_headers):
        async with app.state.session_factory() as db:
            skill = await SkillRepo.create(
                db, TEST_ORG_ID, name="v1", content_md=SAMPLE_SKILL
            )
            await db.commit()
            await db.refresh(skill)
            skill_id = skill.id

        resp = await client.put(
            f"/skills/{skill_id}",
            json={
                "name": "v2",
                "content_md": SAMPLE_SKILL,
                "description": "Renamed",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "v2"
        assert resp.json()["description"] == "Renamed"

    async def test_delete_skill(self, client: AsyncClient, app, auth_headers):
        async with app.state.session_factory() as db:
            skill = await SkillRepo.create(
                db, TEST_ORG_ID, name="gone", content_md=SAMPLE_SKILL
            )
            await db.commit()
            await db.refresh(skill)
            skill_id = skill.id

        resp = await client.delete(f"/skills/{skill_id}", headers=auth_headers)
        assert resp.status_code == 204

        async with app.state.session_factory() as db:
            assert await SkillRepo.get_by_id(db, TEST_ORG_ID, skill_id) is None

    async def test_clone_skill(self, client: AsyncClient, app, auth_headers):
        async with app.state.session_factory() as db:
            source = await SkillRepo.create(
                db,
                TEST_ORG_ID,
                name="src",
                content_md=SAMPLE_SKILL,
                description="original",
            )
            server = await MCPServerRepo.create(
                db, TEST_ORG_ID, name="k8s-prod", transport="stdio", command="echo"
            )
            await db.commit()
            await db.refresh(source)
            await db.refresh(server)
            source_id = source.id
            server_id = server.id

        resp = await client.post(
            f"/skills/{source_id}/clone",
            json={"name": "src-copy", "mcp_server_id": str(server_id)},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "src-copy"
        assert body["mcp_server_id"] == str(server_id)
        assert body["content_md"] == SAMPLE_SKILL

    async def test_import_skill_upload(self, client: AsyncClient, auth_headers):
        files = {"file": ("production.md", SAMPLE_SKILL, "text/markdown")}
        resp = await client.post(
            "/skills/import",
            files=files,
            headers={k: v for k, v in auth_headers.items()},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "production"
        assert body["content_md"] == SAMPLE_SKILL

    async def test_import_empty_file_rejected(self, client: AsyncClient, auth_headers):
        files = {"file": ("empty.md", "", "text/markdown")}
        resp = await client.post(
            "/skills/import",
            files=files,
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_import_invalid_content_rejected(
        self, client: AsyncClient, auth_headers
    ):
        files = {
            "file": ("bad.md", "---\n: nope ::\n---\n", "text/markdown"),
        }
        resp = await client.post(
            "/skills/import",
            files=files,
            headers=auth_headers,
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Enforcement-from-DB helper
# ---------------------------------------------------------------------------


class TestEnforcementFromDB:
    async def test_loads_bound_skill(self, db: AsyncSession):
        server = await MCPServerRepo.create(
            db, TEST_ORG_ID, name="k8s-prod", transport="stdio", command="echo"
        )
        await db.flush()

        await SkillRepo.create(
            db,
            TEST_ORG_ID,
            name="bound",
            content_md=SAMPLE_SKILL,
            mcp_server_id=server.id,
        )
        await db.flush()

        skill_def = await load_skill_for_mcp_server(db, TEST_ORG_ID, server.id)
        assert skill_def is not None
        assert skill_def.classify("get_pods") == "safe"
        assert skill_def.classify("delete_namespace") == "destructive"

    async def test_falls_back_to_global(self, db: AsyncSession):
        server = await MCPServerRepo.create(
            db, TEST_ORG_ID, name="k8s-prod", transport="stdio", command="echo"
        )
        await db.flush()
        await SkillRepo.create(db, TEST_ORG_ID, name="global", content_md=SAMPLE_SKILL)
        await db.flush()

        skill_def = await load_skill_for_mcp_server(db, TEST_ORG_ID, server.id)
        assert skill_def is not None
        assert skill_def.classify("get_pods") == "safe"

    async def test_returns_none_when_empty(self, db: AsyncSession):
        assert await load_skill_for_mcp_server(db, TEST_ORG_ID, None) is None
