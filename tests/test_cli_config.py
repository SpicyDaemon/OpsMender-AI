"""Tests for the ``aim config`` CLI command."""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cli.aim import main, _parse_args
from backend.db.models import Base
from backend.db.repos import ModelConfigRepo


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

    def test_config_model_list_subcommand(self):
        args = _parse_args(["config", "model", "list", "--provider", "openai"])
        assert args.command == "config"
        assert args.config_command == "model"
        assert args.model_command == "list"
        assert args.provider == "openai"

    def test_config_model_set_subcommand(self):
        args = _parse_args(
            [
                "config",
                "model",
                "set",
                "--provider",
                "ollama",
                "--model-id",
                "llama3.2",
            ]
        )
        assert args.command == "config"
        assert args.config_command == "model"
        assert args.model_command == "set"
        assert args.provider == "ollama"
        assert args.model_id == "llama3.2"


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


def _create_sqlite_schema(database_url: str) -> None:
    async def _run() -> None:
        engine = create_async_engine(database_url, echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_run())


class TestConfigModel:
    def test_model_list_json_output(self, tmp_path, capsys, monkeypatch):
        cfg_path = _write_cfg(tmp_path)

        def _discover_models(self, **kwargs):
            assert kwargs["provider"] == "openai"
            return [
                {
                    "provider": "openai",
                    "label": "OpenAI",
                    "default_model_id": "gpt-4o",
                    "default_api_key_env_var": "OPENAI_API_KEY",
                    "requires_api_key": True,
                    "requires_base_url": False,
                    "requires_api_version": False,
                    "available": True,
                    "models": ["gpt-4o", "gpt-4o-mini"],
                    "error": None,
                }
            ]

        monkeypatch.setattr("cli.aim.ProviderRegistry.discover_models", _discover_models)

        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "--config",
                    cfg_path,
                    "config",
                    "model",
                    "list",
                    "--provider",
                    "openai",
                    "--json",
                ]
            )
        assert exc_info.value.code == 0
        data = json.loads(capsys.readouterr().out)
        assert data["total"] == 1
        assert data["items"][0]["provider"] == "openai"
        assert data["items"][0]["models"] == ["gpt-4o", "gpt-4o-mini"]

    def test_model_set_persists_default_config(self, tmp_path, capsys, monkeypatch):
        cfg_path = _write_cfg(tmp_path)
        db_path = tmp_path / "aim.db"
        database_url = f"sqlite+aiosqlite:///{db_path}"
        _create_sqlite_schema(database_url)
        monkeypatch.setenv("AIM_DATABASE_URL", database_url)
        monkeypatch.setattr(
            "cli.aim.ProviderRegistry.validate_model_config",
            lambda self, **kwargs: None,
        )

        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "--config",
                    cfg_path,
                    "config",
                    "model",
                    "set",
                    "--provider",
                    "ollama",
                    "--model-id",
                    "llama3.2",
                    "--base-url",
                    "http://localhost:11434",
                    "--json",
                ]
            )
        assert exc_info.value.code == 0
        data = json.loads(capsys.readouterr().out)
        assert data["provider"] == "ollama"
        assert data["model_id"] == "llama3.2"
        assert data["is_default"] is True

        async def _verify() -> None:
            engine = create_async_engine(database_url, echo=False)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                default = await ModelConfigRepo.get_default(session)
                assert default is not None
                assert default.provider == "ollama"
                assert default.model_id == "llama3.2"
                assert default.base_url == "http://localhost:11434"
            await engine.dispose()

        asyncio.run(_verify())

    def test_model_set_validation_error_returns_nonzero(
        self, tmp_path, capsys, monkeypatch
    ):
        cfg_path = _write_cfg(tmp_path)
        monkeypatch.setattr(
            "cli.aim.ProviderRegistry.validate_model_config",
            lambda self, **kwargs: (_ for _ in ()).throw(ValueError("bad model")),
        )

        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "--config",
                    cfg_path,
                    "config",
                    "model",
                    "set",
                    "--provider",
                    "openai",
                    "--model-id",
                    "bad-model",
                ]
            )
        assert exc_info.value.code == 1
        assert "bad model" in capsys.readouterr().err
