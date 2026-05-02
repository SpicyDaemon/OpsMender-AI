"""Tests for the ``aim run`` CLI command."""

from __future__ import annotations

import json
import pathlib

import pytest

from cli.aim import main, _parse_args


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


class TestRunArgParsing:
    def test_run_requires_incident(self):
        with pytest.raises(SystemExit):
            _parse_args(["run"])

    def test_run_with_incident(self):
        args = _parse_args(["run", "--incident", "pods crashing"])
        assert args.command == "run"
        assert args.incident == "pods crashing"
        assert args.tier is None
        assert args.dry_run is False
        assert args.skill_file == "examples/SKILL.md"
        assert args.output is None

    def test_run_with_all_options(self):
        args = _parse_args(
            [
                "run",
                "--incident",
                "high latency",
                "--tier",
                "3",
                "--skill-file",
                "my/SKILL.md",
                "--model",
                "claude-haiku-4-5-20251001",
                "--dry-run",
                "--output",
                "out.json",
            ]
        )
        assert args.tier == 3
        assert args.skill_file == "my/SKILL.md"
        assert args.model == "claude-haiku-4-5-20251001"
        assert args.dry_run is True
        assert args.output == "out.json"


# ---------------------------------------------------------------------------
# Dry-run end-to-end (stub LLM, no MCP, no API key needed)
# ---------------------------------------------------------------------------


class TestRunDryRun:
    """End-to-end tests using --dry-run (stub LLM, no MCP)."""

    def test_dry_run_succeeds(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "run",
                    "--incident",
                    "test incident",
                    "--dry-run",
                ]
            )
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "INCIDENT RESPONSE COMPLETE" in out
        assert "Session:" in out
        assert "Dry-run mode" in out

    def test_dry_run_with_tier_override(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "run",
                    "--incident",
                    "pod OOMKilled",
                    "--tier",
                    "3",
                    "--dry-run",
                ]
            )
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "Tier:     3" in out

    def test_dry_run_writes_output_file(self, tmp_path, capsys):
        out_file = tmp_path / "result.json"
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "run",
                    "--incident",
                    "disk full",
                    "--dry-run",
                    "--output",
                    str(out_file),
                ]
            )
        assert exc_info.value.code == 0
        assert out_file.is_file()
        data = json.loads(out_file.read_text())
        assert data["status"] == "completed"
        assert data["incident_description"] == "disk full"
        assert "session_id" in data

    def test_dry_run_creates_audit_entries(self, tmp_path, capsys):
        cfg_file = tmp_path / ".env"
        audit_file = tmp_path / "audit.jsonl"
        cfg_file.write_text(
            f"AIM_TIER=2\nAIM_LOG_LEVEL=INFO\nAIM_AUDIT_LOG={audit_file}\n"
        )
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "--config",
                    str(cfg_file),
                    "run",
                    "--incident",
                    "test",
                    "--dry-run",
                ]
            )
        assert exc_info.value.code == 0
        assert audit_file.is_file()
        lines = audit_file.read_text().strip().splitlines()
        assert len(lines) >= 2  # at least session_start + session_end
        first = json.loads(lines[0])
        last = json.loads(lines[-1])
        assert first["entry_type"] == "session_start"
        assert last["entry_type"] == "session_end"

    def test_invalid_tier_fails(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "run",
                    "--incident",
                    "test",
                    "--tier",
                    "5",
                    "--dry-run",
                ]
            )
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Invalid tier" in err

    def test_missing_skill_file_fails(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "run",
                    "--incident",
                    "test",
                    "--skill-file",
                    "/tmp/nonexistent_skill.md",
                    "--dry-run",
                ]
            )
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Skill file not found" in err


# ---------------------------------------------------------------------------
# AnthropicLLM unit tests (no API calls)
# ---------------------------------------------------------------------------


class TestAnthropicLLM:
    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from backend.agent.llm import AnthropicLLM

        with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
            AnthropicLLM()

    def test_missing_package_raises(self, monkeypatch):
        """Simulate anthropic not being installed."""
        import sys

        # Save and remove anthropic from sys.modules temporarily
        saved = sys.modules.get("anthropic")
        sys.modules["anthropic"] = None  # type: ignore[assignment]
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        try:
            from backend.agent.llm import AnthropicLLM

            with pytest.raises((ImportError, EnvironmentError)):
                AnthropicLLM()
        finally:
            if saved is not None:
                sys.modules["anthropic"] = saved
            else:
                sys.modules.pop("anthropic", None)
