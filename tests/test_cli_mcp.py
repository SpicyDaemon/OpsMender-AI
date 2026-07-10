"""Tests for the ``opsmender mcp`` CLI subcommand (Sprint 42 step 7)."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, Organization
from backend.db.repos import MCPServerRepo
from cli.opsmender import _parse_args, main


TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000ab")


def _make_cfg(tmp_path, db_url: str) -> str:
    env = tmp_path / ".env"
    env.write_text(
        f"OPSMENDER_DATABASE_URL={db_url}\n"
        "OPSMENDER_TIER=2\n"
        "OPSMENDER_LOG_LEVEL=INFO\n"
        "OPSMENDER_AUDIT_LOG=./logs/audit.jsonl\n"
    )
    return str(env)


@pytest.fixture
async def seeded_db(tmp_path):
    db_path = tmp_path / "mcp.db"
    db_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Organization(id=TEST_ORG_ID, name="Test", slug="test"))
        await session.commit()
    async with factory() as session:
        await MCPServerRepo.create(
            session,
            TEST_ORG_ID,
            name="kube",
            transport="stdio",
            command="echo",
            is_active=True,
        )
        await session.commit()
    await engine.dispose()
    yield db_url


class TestArgParsing:
    def test_export_subcommand(self):
        args = _parse_args(["mcp", "export"])
        assert args.command == "mcp"
        assert args.mcp_command == "export"

    def test_reload_flags(self):
        args = _parse_args(["mcp", "reload", "--apply", "--prune"])
        assert args.command == "mcp"
        assert args.mcp_command == "reload"
        assert args.apply is True
        assert args.prune is True


class TestExport:
    def test_export_writes_file(self, tmp_path, seeded_db, capsys):
        out_path = tmp_path / "mcp.json"
        cfg = _make_cfg(tmp_path, seeded_db)
        with pytest.raises(SystemExit) as exc:
            main(
                [
                    "--config",
                    cfg,
                    "mcp",
                    "export",
                    "--path",
                    str(out_path),
                    "--org-id",
                    str(TEST_ORG_ID),
                ]
            )
        assert exc.value.code == 0
        assert out_path.exists()
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert "kube" in data["mcpServers"]
        assert data["mcpServers"]["kube"]["command"] == "echo"


class TestReload:
    def test_reload_dry_run_does_not_commit(self, tmp_path, seeded_db, capsys):
        path = tmp_path / "mcp.json"
        path.write_text(
            json.dumps(
                {"mcpServers": {"newbie": {"type": "stdio", "command": "uptime"}}}
            )
        )
        cfg = _make_cfg(tmp_path, seeded_db)
        with pytest.raises(SystemExit) as exc:
            main(
                [
                    "--config",
                    cfg,
                    "mcp",
                    "reload",
                    "--path",
                    str(path),
                    "--org-id",
                    str(TEST_ORG_ID),
                ]
            )
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "Dry run" in out
        assert "+ newbie" in out

    def test_reload_apply_commits(self, tmp_path, seeded_db, capsys):
        path = tmp_path / "mcp.json"
        path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "kube": {"type": "stdio", "command": "ENTIRELY_NEW"},
                    }
                }
            )
        )
        cfg = _make_cfg(tmp_path, seeded_db)
        with pytest.raises(SystemExit) as exc:
            main(
                [
                    "--config",
                    cfg,
                    "mcp",
                    "reload",
                    "--apply",
                    "--path",
                    str(path),
                    "--org-id",
                    str(TEST_ORG_ID),
                ]
            )
        assert exc.value.code == 0
        # Verify DB
        engine = create_async_engine(seeded_db, echo=False)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        import asyncio

        async def _check():
            async with factory() as session:
                row = await MCPServerRepo.get_by_name(session, TEST_ORG_ID, "kube")
                assert row.command == "ENTIRELY_NEW"
            await engine.dispose()

        asyncio.run(_check())

    def test_reload_bad_org_id_exits_nonzero(self, tmp_path, seeded_db, capsys):
        cfg = _make_cfg(tmp_path, seeded_db)
        with pytest.raises(SystemExit) as exc:
            main(
                [
                    "--config",
                    cfg,
                    "mcp",
                    "reload",
                    "--org-id",
                    "not-a-uuid",
                ]
            )
        assert exc.value.code == 2
