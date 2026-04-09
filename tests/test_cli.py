"""Tests for cli.aim."""

import pytest

from cli.aim import main


class TestCLI:
    def test_version_flag(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out.strip()
        assert out == "0.1.0"

    def test_default_config_loads(self, capsys):
        """Running with no args loads the repo-root config.yaml."""
        main([])
        out = capsys.readouterr().out
        assert "Configuration loaded:" in out

    def test_bad_config_path_exits(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", "/tmp/no_such_file.yaml"])
        assert exc_info.value.code == 1

    def test_custom_config(self, tmp_path, capsys):
        cfg = tmp_path / "custom.yaml"
        cfg.write_text(
            "mcp_servers:\n"
            "  - name: test\n"
            "    transport: stdio\n"
            "    command: echo\n"
            "tiers:\n"
            "  default: 3\n"
            "logging:\n"
            "  level: WARN\n"
        )
        main(["--config", str(cfg)])
        out = capsys.readouterr().out
        assert "Configuration loaded:" in out
