"""Explicit skill-tier conversion, validation, and migration guarantees."""

from __future__ import annotations

import importlib
import logging
import uuid

import pytest
import sqlalchemy as sa
import yaml

from backend.skills.convert import convert_legacy_skill_content
from backend.skills.parser import loads
from backend.tiers.enforcement import check


# Frozen pre-change outcomes: (permitted, requires_approval, decision).
LEGACY_EXPECTATIONS = {
    ("safe", False, False, 0): (False, False, "deny"),
    ("safe", False, False, 1): (True, True, "approval"),
    ("safe", False, False, 2): (False, False, "advisory"),
    ("safe", False, True, 0): (False, False, "deny"),
    ("safe", False, True, 1): (True, True, "approval"),
    ("safe", False, True, 2): (False, False, "advisory"),
    ("safe", True, False, 0): (True, False, "autonomous"),
    ("safe", True, False, 1): (True, True, "approval"),
    ("safe", True, False, 2): (False, False, "advisory"),
    ("safe", True, True, 0): (True, False, "autonomous"),
    ("safe", True, True, 1): (True, True, "approval"),
    ("safe", True, True, 2): (False, False, "advisory"),
    ("caution", False, False, 0): (False, False, "deny"),
    ("caution", False, False, 1): (True, True, "approval"),
    ("caution", False, False, 2): (False, False, "advisory"),
    ("caution", False, True, 0): (False, False, "deny"),
    ("caution", False, True, 1): (True, True, "approval"),
    ("caution", False, True, 2): (False, False, "advisory"),
    ("caution", True, False, 0): (False, False, "deny"),
    ("caution", True, False, 1): (True, True, "approval"),
    ("caution", True, False, 2): (False, False, "advisory"),
    ("caution", True, True, 0): (True, False, "autonomous"),
    ("caution", True, True, 1): (True, True, "approval"),
    ("caution", True, True, 2): (False, False, "advisory"),
    ("destructive", False, False, 0): (False, False, "deny"),
    ("destructive", False, False, 1): (True, True, "approval"),
    ("destructive", False, False, 2): (False, False, "advisory"),
    ("destructive", False, True, 0): (False, False, "deny"),
    ("destructive", False, True, 1): (True, True, "approval"),
    ("destructive", False, True, 2): (False, False, "advisory"),
    ("destructive", True, False, 0): (False, False, "deny"),
    ("destructive", True, False, 1): (True, True, "approval"),
    ("destructive", True, False, 2): (False, False, "advisory"),
    ("destructive", True, True, 0): (True, False, "autonomous"),
    ("destructive", True, True, 1): (True, True, "approval"),
    ("destructive", True, True, 2): (False, False, "advisory"),
}


@pytest.mark.parametrize(("case", "expected"), LEGACY_EXPECTATIONS.items())
def test_legacy_conversion_preserves_every_enforcement_outcome(case, expected):
    classification, reversible, has_inverse, tier = case
    operation = {
        "tool": "target_operation",
        "classification": classification,
        "reversible": reversible,
    }
    if has_inverse:
        operation["compensating_inverse"] = "undo_target_operation"
    raw = yaml.safe_dump(
        {"version": "1", "environment": "test", "operations": [operation]},
        sort_keys=False,
    )

    converted = convert_legacy_skill_content(raw, fmt="yaml")
    result = check("target_operation", tier, loads(converted.content, fmt="yaml"))

    assert (result.permitted, result.requires_approval, result.decision) == expected


def test_converter_is_idempotent_and_preserves_markdown_body():
    raw = """---
version: "1"
operations:
  - tool: get_status
    classification: safe
---

# Operator notes

Keep this body exactly.
"""

    first = convert_legacy_skill_content(raw)
    second = convert_legacy_skill_content(first.content)

    assert first.changed is True
    assert first.converted_operations == ("get_status",)
    assert first.content.endswith("# Operator notes\n\nKeep this body exactly.\n")
    assert second.changed is False
    assert second.content == first.content


def test_parser_requires_complete_tiers_for_executable_operations():
    with pytest.raises(ValueError, match="get_status.*tiers are required"):
        loads(
            """---
operations:
  - tool: get_status
    classification: safe
---
"""
        )

    with pytest.raises(ValueError, match="get_status.*missing tier policies: T2"):
        loads(
            """---
operations:
  - tool: get_status
    classification: safe
    tiers:
      T0: {enabled: true, mode: autonomous}
      T1: {enabled: true, mode: autonomous}
---
"""
        )


@pytest.mark.parametrize("mode", ["autonomous", "approval"])
def test_parser_rejects_executable_t2_modes(mode):
    raw = f"""---
operations:
  - tool: get_status
    classification: safe
    tiers:
      T0: {{enabled: true, mode: autonomous}}
      T1: {{enabled: true, mode: autonomous}}
      T2: {{enabled: true, mode: {mode}}}
---
"""
    with pytest.raises(ValueError, match="T2 mode must be advisory or blocked"):
        loads(raw)


def test_parser_rejects_incoherent_enabled_and_mode_pairs():
    disabled_autonomous = """---
operations:
  - tool: get_status
    classification: safe
    tiers:
      T0: {enabled: false, mode: autonomous}
      T1: {enabled: true, mode: autonomous}
      T2: {enabled: true, mode: advisory}
---
"""
    with pytest.raises(ValueError, match="enabled false requires"):
        loads(disabled_autonomous)

    enabled_blocked = disabled_autonomous.replace(
        "enabled: false, mode: autonomous", "enabled: true, mode: blocked"
    )
    with pytest.raises(ValueError, match="enabled true cannot use blocked"):
        loads(enabled_blocked)


def test_deny_entry_ignores_tiers():
    skill = loads(
        """---
operations:
  - tool: drop_database
    deny: true
    tiers: this value is ignored
---
"""
    )
    for tier in (0, 1, 2):
        assert check("drop_database", tier, skill).permitted is False


def test_data_migration_is_idempotent_and_fail_safe(monkeypatch, caplog):
    migration = importlib.import_module(
        "backend.db.migrations.versions.o2p3q4r5s6t7_explicit_skill_tiers"
    )
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    skills = sa.Table(
        "skills",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("content_md", sa.Text(), nullable=False),
    )
    metadata.create_all(engine)

    legacy_id = uuid.uuid4()
    malformed_id = uuid.uuid4()
    explicit_id = uuid.uuid4()
    legacy = """---
operations:
  - tool: get_status
    classification: safe
---
# Body stays
"""
    malformed = "---\n- not a mapping\n---\n"
    explicit = convert_legacy_skill_content(legacy).content

    with engine.begin() as connection:
        connection.execute(
            skills.insert(),
            [
                {"id": legacy_id, "content_md": legacy},
                {"id": malformed_id, "content_md": malformed},
                {"id": explicit_id, "content_md": explicit},
            ],
        )
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
        with caplog.at_level(logging.WARNING):
            migration.upgrade()
        first = dict(connection.execute(sa.select(skills)).all())
        migration.upgrade()
        second = dict(connection.execute(sa.select(skills)).all())

    assert loads(first[legacy_id]).tier_policy("get_status", 1).mode == "approval"
    assert first[legacy_id].endswith("# Body stays\n")
    assert first[malformed_id] == malformed
    assert first[explicit_id] == explicit
    assert second == first
    assert str(malformed_id) in caplog.text
