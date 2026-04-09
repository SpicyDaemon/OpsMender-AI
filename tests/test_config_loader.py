"""Tests for backend.config_loader."""

import pytest

from backend.config_loader import Config


@pytest.fixture()
def valid_yaml(tmp_path):
    """Write a minimal valid config.yaml and return its path."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mcp:\n"
        "  url: http://localhost:8000\n"
        "  token: secret\n"
        "tiers:\n"
        "  default: 2\n"
        "logging:\n"
        "  level: DEBUG\n"
    )
    return cfg


class TestConfigLoad:
    def test_loads_valid_yaml(self, valid_yaml):
        cfg = Config.load(valid_yaml)
        assert cfg.mcp["url"] == "http://localhost:8000"
        assert cfg.mcp["token"] == "secret"
        assert cfg.tiers["default"] == 2
        assert cfg.logging["level"] == "DEBUG"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            Config.load(tmp_path / "nonexistent.yaml")

    def test_missing_keys_default_to_empty_dict(self, tmp_path):
        cfg_file = tmp_path / "empty.yaml"
        cfg_file.write_text("# empty config\n")
        cfg = Config.load(cfg_file)
        assert cfg.mcp == {}
        assert cfg.tiers == {}
        assert cfg.logging == {}

    def test_partial_keys(self, tmp_path):
        cfg_file = tmp_path / "partial.yaml"
        cfg_file.write_text("mcp:\n  url: http://example.com\n")
        cfg = Config.load(cfg_file)
        assert cfg.mcp["url"] == "http://example.com"
        assert cfg.tiers == {}
        assert cfg.logging == {}
