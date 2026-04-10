"""Tests for the ``aim config`` CLI command."""

from __future__ import annotations

import json

import pytest

from cli.aim import main, _parse_args


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_CFG = (
    "mcp_servers: []\n"
    "tiers:\n  default: 2\n"
    "logging:\n  level: INFO\n"
    "audit:\n  output: ./logs/audit.jsonl\n"
)

CFG_WITH_SERVER = (
    "mcp_servers:\n"
    "  - name: k8s\n"
    "    transport: stdio\n"
    "    command: npx\n"
    "    args: ['-y', '@anthropic/mcp-server-k8s']\n"
    "tiers:\n  default: 2\n"
    "logging:\n  level: DEBUG\n"
    "audit:\n  output: ./logs/audit.jsonl\n"
)


def _write_cfg(tmp_path, content=MINIMAL_CFG):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(content)
    return str(cfg)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


class TestConfigArgParsing:
    def test_config_subcommand(self):
        args = _parse_args(["config"])
        assert args.command == "config"
        assert args.json_output is False
        assert args.validate is False

    def test_config_json_flag(self):
        args = _parse_args(["config", "--json"])
        assert args.json_output is True

    def test_config_validate_flag(self):
        args = _parse_args(["config", "--validate"])
        assert args.validate is True

    def test_config_validate_with_skill(self):
        args = _parse_args(["config", "--validate", "--skill-file", "foo.md"])
        assert args.skill_file == "foo.md"


# ---------------------------------------------------------------------------
# Default display
# ---------------------------------------------------------------------------


class TestConfigDisplay:
    def test_shows_summary(self, tmp_path, capsys):
        cfg_path = _write_cfg(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", cfg_path, "config"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "Tier:" in out
        assert "2" in out
        assert "Audit log:" in out
        assert "(none configured)" in out

    def test_shows_mcp_servers(self, tmp_path, capsys):
        cfg_path = _write_cfg(tmp_path, CFG_WITH_SERVER)
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", cfg_path, "config"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "k8s" in out
        assert "stdio" in out


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


class TestConfigJSON:
    def test_json_output(self, tmp_path, capsys):
        cfg_path = _write_cfg(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", cfg_path, "config", "--json"])
        assert exc_info.value.code == 0
        data = json.loads(capsys.readouterr().out)
        assert data["tiers"]["default"] == 2
        assert data["audit"]["output"] == "./logs/audit.jsonl"
        assert data["mcp_servers"] == []

    def test_json_output_with_servers(self, tmp_path, capsys):
        cfg_path = _write_cfg(tmp_path, CFG_WITH_SERVER)
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", cfg_path, "config", "--json"])
        assert exc_info.value.code == 0
        data = json.loads(capsys.readouterr().out)
        assert len(data["mcp_servers"]) == 1
        assert data["mcp_servers"][0]["name"] == "k8s"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestConfigValidate:
    def test_valid_config_passes(self, tmp_path, capsys):
        cfg_path = _write_cfg(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", cfg_path, "config", "--validate"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "Validation OK" in out

    def test_invalid_tier_fails(self, tmp_path, capsys):
        bad_cfg = (
            "mcp_servers: []\n"
            "tiers:\n  default: 9\n"
            "logging:\n  level: INFO\n"
            "audit:\n  output: ./logs/audit.jsonl\n"
        )
        cfg_path = _write_cfg(tmp_path, bad_cfg)
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", cfg_path, "config", "--validate"])
        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "tiers.default must be 0-3" in out

    def test_missing_tier_fails(self, tmp_path, capsys):
        no_tier_cfg = (
            "mcp_servers: []\n"
            "tiers: {}\n"
            "logging:\n  level: INFO\n"
            "audit:\n  output: ./logs/audit.jsonl\n"
        )
        cfg_path = _write_cfg(tmp_path, no_tier_cfg)
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", cfg_path, "config", "--validate"])
        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "tiers.default is not set" in out

    def test_validate_with_valid_skill_file(self, tmp_path, capsys):
        cfg_path = _write_cfg(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--config", cfg_path,
                "config", "--validate",
                "--skill-file", "examples/SKILL.md",
            ])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "Validation OK" in out
        assert "operations" in out

    def test_validate_with_missing_skill_file(self, tmp_path, capsys):
        cfg_path = _write_cfg(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--config", cfg_path,
                "config", "--validate",
                "--skill-file", "/tmp/nonexistent.md",
            ])
        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "Skill file not found" in out
