"""Tests for the ``aim approvals`` CLI commands."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import uuid

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base
from backend.db.repos import ApprovalRequestRepo, SessionRepo
from cli.aim import _parse_args, main


async def _seed_db(db_url: str):
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        session = await SessionRepo.create(db, TEST_ORG_ID, tier=1)
        request = await ApprovalRequestRepo.create(
            db,
            TEST_ORG_ID,
            session_id=session.id,
            action={"tool_name": "delete_pod", "tool_parameters": {"pod": "api"}},
            justification="Testing approval flow",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        await db.commit()
        await db.refresh(request)
        request_id = request.id

    await engine.dispose()
    return request_id


async def _get_request_status(db_url: str, request_id):
    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        request = await ApprovalRequestRepo.get_by_id(db, TEST_ORG_ID, request_id)
        status = None if request is None else request.status
    await engine.dispose()
    return status


class TestApprovalsArgParsing:
    def test_approvals_list_args(self):
        args = _parse_args(["approvals", "list", "--status", "pending"])
        assert args.command == "approvals"
        assert args.approvals_command == "list"
        assert args.status == "pending"

    def test_approvals_approve_args(self):
        args = _parse_args(["approvals", "approve", "1234"])
        assert args.command == "approvals"
        assert args.approvals_command == "approve"
        assert args.request_id == "1234"


class TestApprovalsCLI:
    def test_list_approvals(self, tmp_path, monkeypatch, capsys):
        cfg = tmp_path / ".env"
        cfg.write_text("AIM_TIER=2\n")
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'approvals.db'}"
        request_id = asyncio.run(_seed_db(db_url))
        monkeypatch.setenv("AIM_DATABASE_URL", db_url)

        with pytest.raises(SystemExit) as exc_info:
            main(["--config", str(cfg), "approvals", "list"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert str(request_id)[:12] in out
        assert "pending" in out

    def test_approve_request(self, tmp_path, monkeypatch):
        cfg = tmp_path / ".env"
        cfg.write_text("AIM_TIER=2\n")
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'approvals.db'}"
        request_id = asyncio.run(_seed_db(db_url))
        monkeypatch.setenv("AIM_DATABASE_URL", db_url)

        with pytest.raises(SystemExit) as exc_info:
            main(["--config", str(cfg), "approvals", "approve", str(request_id)])
        assert exc_info.value.code == 0

        assert asyncio.run(_get_request_status(db_url, request_id)) == "approved"
