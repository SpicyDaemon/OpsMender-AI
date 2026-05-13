"""Tests for cli.opsmender."""

import json

import pytest

from cli.opsmender import main


class TestCLI:
    def test_version_flag(self, capsys):
        import importlib.metadata

        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out.strip()
        assert out == importlib.metadata.version("ai-incident-manager")

    def test_default_no_subcommand_prints_help(self, capsys):
        """Running with no args prints help text."""
        main([])
        out = capsys.readouterr().out
        assert "opsmender" in out

    def test_bad_config_path_exits(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", "/tmp/no_such_file.env"])
        assert exc_info.value.code == 1

    def test_custom_config(self, tmp_path, capsys):
        cfg = tmp_path / "custom.env"
        cfg.write_text(
            "OPSMENDER_MCP_SERVERS_JSON="
            + json.dumps(
                [
                    {
                        "name": "test",
                        "transport": "stdio",
                        "command": "echo",
                    }
                ]
            )
            + "\n"
            + "OPSMENDER_TIER=3\n"
            + "OPSMENDER_LOG_LEVEL=WARNING\n"
        )
        main(["--config", str(cfg)])
        out = capsys.readouterr().out
        assert "opsmender" in out
