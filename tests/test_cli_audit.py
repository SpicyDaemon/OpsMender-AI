"""Tests for the ``opsmender audit`` CLI subcommand."""

from __future__ import annotations

import json
import pathlib

import pytest

from backend.audit.logger import AuditLogger
from cli.opsmender import main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(tmp_path: pathlib.Path, audit_path: pathlib.Path) -> pathlib.Path:
    """Write a minimal .env file that points to *audit_path*."""
    cfg = tmp_path / ".env"
    cfg.write_text(f"OPSMENDER_TIER=2\nOPSMENDER_LOG_LEVEL=INFO\nOPSMENDER_AUDIT_LOG={audit_path}\n")
    return cfg


def _seed_log(audit_path: pathlib.Path) -> AuditLogger:
    """Create an audit log with a handful of entries and return the logger."""
    logger = AuditLogger(audit_path)
    logger.log_session_start("sess-aaa", tier=2)
    logger.log_tool_call_start(
        "sess-aaa", tier=2, tool_name="get_pods", tool_parameters={"ns": "default"}
    )
    logger.log_tool_call_end(
        "sess-aaa", tier=2, tool_name="get_pods", result={"pods": 3}, duration_ms=42
    )
    logger.log_tool_call_start(
        "sess-aaa", tier=2, tool_name="delete_pod", tool_parameters={"pod": "x"}
    )
    logger.log_tool_call_blocked(
        "sess-aaa",
        tier=2,
        tool_name="delete_pod",
        tool_parameters={"pod": "x"},
        block_reason="destructive at tier 2",
    )
    logger.log_session_end("sess-aaa", tier=2)

    # Second session
    logger.log_session_start("sess-bbb", tier=3)
    logger.log_tool_call_start("sess-bbb", tier=3, tool_name="get_logs")
    logger.log_tool_call_end(
        "sess-bbb", tier=3, tool_name="get_logs", result={"lines": 50}, duration_ms=10
    )
    logger.log_session_end("sess-bbb", tier=3)
    return logger


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAuditNoLog:
    """When no audit log file exists yet."""

    def test_missing_log_prints_not_found(self, tmp_path, capsys):
        audit_path = tmp_path / "nope.jsonl"
        cfg = _write_config(tmp_path, audit_path)
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", str(cfg), "audit"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "No audit entries recorded yet" in out


class TestAuditShowAll:
    """Default invocation shows all entries."""

    def test_shows_all_entries(self, tmp_path, capsys):
        audit_path = tmp_path / "audit.jsonl"
        _seed_log(audit_path)
        cfg = _write_config(tmp_path, audit_path)
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", str(cfg), "audit"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "10 entries" in out

    def test_shows_tool_names(self, tmp_path, capsys):
        audit_path = tmp_path / "audit.jsonl"
        _seed_log(audit_path)
        cfg = _write_config(tmp_path, audit_path)
        with pytest.raises(SystemExit):
            main(["--config", str(cfg), "audit"])
        out = capsys.readouterr().out
        assert "get_pods" in out
        assert "delete_pod" in out

    def test_blocked_entry_shows_reason(self, tmp_path, capsys):
        audit_path = tmp_path / "audit.jsonl"
        _seed_log(audit_path)
        cfg = _write_config(tmp_path, audit_path)
        with pytest.raises(SystemExit):
            main(["--config", str(cfg), "audit"])
        out = capsys.readouterr().out
        assert "destructive" in out

    def test_blocked_entry_shows_cross_mark(self, tmp_path, capsys):
        audit_path = tmp_path / "audit.jsonl"
        _seed_log(audit_path)
        cfg = _write_config(tmp_path, audit_path)
        with pytest.raises(SystemExit):
            main(["--config", str(cfg), "audit"])
        out = capsys.readouterr().out
        assert "FAIL" in out  # blocked entries show FAIL marker


class TestAuditLastN:
    """--last N shows only the most recent entries."""

    def test_last_3(self, tmp_path, capsys):
        audit_path = tmp_path / "audit.jsonl"
        _seed_log(audit_path)
        cfg = _write_config(tmp_path, audit_path)
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", str(cfg), "audit", "--last", "3"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "3 entries" in out
        # Should contain the last session entries (sess-bbb)
        assert "sess-bbb" in out


class TestAuditSessionFilter:
    """--session filters to a single session."""

    def test_filter_session_aaa(self, tmp_path, capsys):
        audit_path = tmp_path / "audit.jsonl"
        _seed_log(audit_path)
        cfg = _write_config(tmp_path, audit_path)
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", str(cfg), "audit", "--session", "sess-aaa"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "6 entries" in out
        assert "sess-bbb" not in out

    def test_filter_nonexistent_session(self, tmp_path, capsys):
        audit_path = tmp_path / "audit.jsonl"
        _seed_log(audit_path)
        cfg = _write_config(tmp_path, audit_path)
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", str(cfg), "audit", "--session", "sess-zzz"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "No audit entries found" in out


class TestAuditJsonOutput:
    """--json outputs raw JSONL."""

    def test_json_lines(self, tmp_path, capsys):
        audit_path = tmp_path / "audit.jsonl"
        _seed_log(audit_path)
        cfg = _write_config(tmp_path, audit_path)
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", str(cfg), "audit", "--json"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        lines = [line for line in out.strip().split("\n") if line]
        assert len(lines) == 10
        # Each line should be valid JSON
        for line in lines:
            parsed = json.loads(line)
            assert "entry_id" in parsed
            assert "entry_type" in parsed

    def test_json_combined_with_last(self, tmp_path, capsys):
        audit_path = tmp_path / "audit.jsonl"
        _seed_log(audit_path)
        cfg = _write_config(tmp_path, audit_path)
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", str(cfg), "audit", "--json", "--last", "2"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        lines = [line for line in out.strip().split("\n") if line]
        assert len(lines) == 2
