"""Page text: org line is shown only when the deployment is multi-org."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, Incident, Organization
from backend.paging.page_text import format_page_subject_body, org_name_for_page


def _incident() -> Incident:
    return Incident(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        title="DB pool exhausted",
        description="connections maxed",
        status="open",
        priority="P1",
    )


def test_format_includes_org_line_when_named():
    subject, body = format_page_subject_body(_incident(), org_name="Acme Corp")
    assert subject.startswith("OpsMender: DB pool exhausted")
    lines = body.splitlines()
    assert lines[0] == "Org: Acme Corp"  # org is first, above priority
    assert "Priority: P1" in body
    assert "Status: open" in body
    assert "connections maxed" in body


def test_format_omits_org_line_when_none():
    _subject, body = format_page_subject_body(_incident(), org_name=None)
    assert "Org:" not in body
    assert body.splitlines()[0] == "Priority: P1"


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_org_name_omitted_for_single_org(factory):
    org_id = uuid.uuid4()
    async with factory() as db:
        db.add(Organization(id=org_id, name="Main", slug="main"))
        await db.commit()
        assert await org_name_for_page(db, org_id) is None


async def test_org_name_shown_when_multiple_orgs(factory):
    a, b = uuid.uuid4(), uuid.uuid4()
    async with factory() as db:
        db.add(Organization(id=a, name="Acme", slug="acme"))
        db.add(Organization(id=b, name="Globex", slug="globex"))
        await db.commit()
        assert await org_name_for_page(db, a) == "Acme"
        assert await org_name_for_page(db, b) == "Globex"
