"""Tests for the audit logger (backend/audit/logger.py)."""

from __future__ import annotations

import json
import pathlib
import threading

import pytest

from backend.audit.logger import AuditEntry, AuditEntryType, AuditLogger


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SESSION = "test-session-001"
TIER = 2


@pytest.fixture()
def audit_log(tmp_path: pathlib.Path) -> AuditLogger:
    """Return an AuditLogger that writes to a temp directory."""
    return AuditLogger(tmp_path / "audit.jsonl")


# ---------------------------------------------------------------------------
# AuditEntry
# ---------------------------------------------------------------------------


class TestAuditEntry:
    def test_to_dict_contains_all_fields(self):
        entry = AuditEntry(
            entry_id="abc123",
            session_id=SESSION,
            timestamp="2026-04-09T00:00:00+00:00",
            tier=TIER,
            entry_type=AuditEntryType.TOOL_CALL_START,
            tool_name="get_pods",
            tool_parameters={"namespace": "default"},
        )
        d = entry.to_dict()
        assert d["entry_id"] == "abc123"
        assert d["session_id"] == SESSION
        assert d["tier"] == TIER
        assert d["entry_type"] == "tool_call_start"
        assert d["tool_name"] == "get_pods"
        assert d["tool_parameters"] == {"namespace": "default"}
        assert d["permitted"] is True
        assert d["block_reason"] is None
        assert d["duration_ms"] is None

    def test_to_dict_entry_type_is_string(self):
        entry = AuditEntry(
            entry_id="x",
            session_id="s",
            timestamp="t",
            tier=0,
            entry_type=AuditEntryType.SESSION_START,
        )
        assert isinstance(entry.to_dict()["entry_type"], str)

    def test_blocked_entry_fields(self):
        entry = AuditEntry(
            entry_id="blk1",
            session_id=SESSION,
            timestamp="t",
            tier=2,
            entry_type=AuditEntryType.TOOL_CALL_BLOCKED,
            tool_name="delete_pod",
            permitted=False,
            block_reason="Tier 2 denies destructive operations",
        )
        d = entry.to_dict()
        assert d["permitted"] is False
        assert d["block_reason"] == "Tier 2 denies destructive operations"


# ---------------------------------------------------------------------------
# AuditEntryType
# ---------------------------------------------------------------------------


class TestAuditEntryType:
    def test_all_expected_types_exist(self):
        expected = {
            "tool_call_start",
            "tool_call_end",
            "tool_call_blocked",
            "session_start",
            "session_end",
        }
        actual = {e.value for e in AuditEntryType}
        assert actual == expected

    def test_is_str_enum(self):
        assert isinstance(AuditEntryType.TOOL_CALL_START, str)
        assert AuditEntryType.TOOL_CALL_START == "tool_call_start"


# ---------------------------------------------------------------------------
# AuditLogger — basic file operations
# ---------------------------------------------------------------------------


class TestAuditLoggerFileOps:
    def test_creates_parent_directories(self, tmp_path: pathlib.Path):
        nested = tmp_path / "a" / "b" / "c" / "audit.jsonl"
        logger = AuditLogger(nested)
        assert nested.parent.is_dir()
        assert logger.path == nested

    def test_log_creates_file_on_first_write(self, audit_log: AuditLogger):
        assert not audit_log.path.exists()
        audit_log.log_session_start(SESSION, TIER)
        assert audit_log.path.is_file()

    def test_log_appends_lines(self, audit_log: AuditLogger):
        audit_log.log_session_start(SESSION, TIER)
        audit_log.log_session_end(SESSION, TIER)
        lines = audit_log.path.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_each_line_is_valid_json(self, audit_log: AuditLogger):
        audit_log.log_session_start(SESSION, TIER)
        audit_log.log_tool_call_start(SESSION, TIER, "get_pods")
        for line in audit_log.path.read_text().strip().splitlines():
            parsed = json.loads(line)
            assert "entry_id" in parsed
            assert "entry_type" in parsed

    def test_log_is_append_only(self, audit_log: AuditLogger):
        """Subsequent writes must never overwrite earlier entries."""
        audit_log.log_session_start(SESSION, TIER)
        first_line = audit_log.path.read_text().strip()
        audit_log.log_session_end(SESSION, TIER)
        content = audit_log.path.read_text().strip()
        assert content.startswith(first_line)
        assert content.count("\n") == 1  # two lines, one newline between


# ---------------------------------------------------------------------------
# AuditLogger — convenience helpers
# ---------------------------------------------------------------------------


class TestAuditLoggerHelpers:
    def test_log_tool_call_start(self, audit_log: AuditLogger):
        eid = audit_log.log_tool_call_start(
            SESSION, TIER, "get_pods", {"namespace": "default"}
        )
        assert isinstance(eid, str) and len(eid) == 12
        entries = audit_log.read_all()
        assert len(entries) == 1
        e = entries[0]
        assert e.entry_type == AuditEntryType.TOOL_CALL_START
        assert e.tool_name == "get_pods"
        assert e.tool_parameters == {"namespace": "default"}
        assert e.permitted is True

    def test_log_tool_call_end(self, audit_log: AuditLogger):
        eid = audit_log.log_tool_call_end(
            SESSION, TIER, "get_pods", result={"count": 5}, duration_ms=312
        )
        assert isinstance(eid, str)
        entries = audit_log.read_all()
        assert entries[0].entry_type == AuditEntryType.TOOL_CALL_END
        assert entries[0].result == {"count": 5}
        assert entries[0].duration_ms == 312

    def test_log_tool_call_blocked(self, audit_log: AuditLogger):
        eid = audit_log.log_tool_call_blocked(
            SESSION,
            TIER,
            "delete_pod",
            tool_parameters={"pod": "api-server"},
            block_reason="Tier 2 denies destructive operations",
        )
        assert isinstance(eid, str)
        entries = audit_log.read_all()
        e = entries[0]
        assert e.entry_type == AuditEntryType.TOOL_CALL_BLOCKED
        assert e.permitted is False
        assert e.block_reason == "Tier 2 denies destructive operations"
        assert e.tool_name == "delete_pod"

    def test_log_session_start(self, audit_log: AuditLogger):
        eid = audit_log.log_session_start(SESSION, TIER)
        assert isinstance(eid, str)
        entries = audit_log.read_all()
        assert entries[0].entry_type == AuditEntryType.SESSION_START
        assert entries[0].session_id == SESSION
        assert entries[0].tier == TIER

    def test_log_session_end(self, audit_log: AuditLogger):
        eid = audit_log.log_session_end(SESSION, TIER)
        assert isinstance(eid, str)
        entries = audit_log.read_all()
        assert entries[0].entry_type == AuditEntryType.SESSION_END

    def test_entry_ids_are_unique(self, audit_log: AuditLogger):
        ids = set()
        for _ in range(20):
            ids.add(audit_log.log_session_start(SESSION, TIER))
        assert len(ids) == 20

    def test_timestamps_are_iso_format(self, audit_log: AuditLogger):
        audit_log.log_session_start(SESSION, TIER)
        entries = audit_log.read_all()
        ts = entries[0].timestamp
        # Should contain 'T' and timezone info ('+' or 'Z')
        assert "T" in ts


# ---------------------------------------------------------------------------
# AuditLogger — reading / querying
# ---------------------------------------------------------------------------


class TestAuditLoggerReading:
    def test_read_all_empty_file(self, audit_log: AuditLogger):
        assert audit_log.read_all() == []

    def test_read_all_returns_audit_entries(self, audit_log: AuditLogger):
        audit_log.log_session_start(SESSION, TIER)
        audit_log.log_tool_call_start(SESSION, TIER, "get_pods")
        audit_log.log_session_end(SESSION, TIER)
        entries = audit_log.read_all()
        assert len(entries) == 3
        assert all(isinstance(e, AuditEntry) for e in entries)

    def test_read_last(self, audit_log: AuditLogger):
        for i in range(10):
            audit_log.log_tool_call_start(SESSION, TIER, f"tool_{i}")
        last3 = audit_log.read_last(3)
        assert len(last3) == 3
        assert last3[-1].tool_name == "tool_9"
        assert last3[0].tool_name == "tool_7"

    def test_read_last_more_than_total(self, audit_log: AuditLogger):
        audit_log.log_session_start(SESSION, TIER)
        result = audit_log.read_last(100)
        assert len(result) == 1

    def test_read_by_session(self, audit_log: AuditLogger):
        audit_log.log_session_start("sess-A", TIER)
        audit_log.log_tool_call_start("sess-A", TIER, "get_pods")
        audit_log.log_session_start("sess-B", TIER)
        audit_log.log_tool_call_start("sess-B", TIER, "get_nodes")
        audit_log.log_session_end("sess-A", TIER)

        a_entries = audit_log.read_by_session("sess-A")
        b_entries = audit_log.read_by_session("sess-B")
        assert len(a_entries) == 3
        assert len(b_entries) == 2
        assert all(e.session_id == "sess-A" for e in a_entries)

    def test_read_by_session_not_found(self, audit_log: AuditLogger):
        audit_log.log_session_start(SESSION, TIER)
        assert audit_log.read_by_session("nonexistent") == []


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestAuditLoggerThreadSafety:
    def test_concurrent_writes(self, audit_log: AuditLogger):
        """Multiple threads writing simultaneously must not lose entries."""
        n_threads = 8
        writes_per_thread = 25

        def writer(thread_id: int) -> None:
            for i in range(writes_per_thread):
                audit_log.log_tool_call_start(
                    f"sess-{thread_id}", TIER, f"tool_{thread_id}_{i}"
                )

        threads = [
            threading.Thread(target=writer, args=(t,)) for t in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        entries = audit_log.read_all()
        assert len(entries) == n_threads * writes_per_thread

        # Every line must be valid JSON
        for line in audit_log.path.read_text().strip().splitlines():
            json.loads(line)
