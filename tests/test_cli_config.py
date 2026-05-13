"""Tests for the ``opsmender config`` CLI command."""

from __future__ import annotations

import asyncio
import json

import pytest
import uuid

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from cli.opsmender import main, _parse_args
from backend.db.models import Base
from backend.db.repos import ModelConfigRepo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_CFG = "OPSMENDER_TIER=2\nOPSMENDER_LOG_LEVEL=INFO\nOPSMENDER_AUDIT_LOG=./logs/audit.jsonl\n"

CFG_WITH_SERVER = (
    "OPSMENDER_MCP_SERVERS_JSON="
    + json.dumps(
        [
            {
                "name": "k8s",
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@anthropic/mcp-server-k8s"],
            }
        ]
    )
    + "\n"
    + "OPSMENDER_TIER=2\n"
    + "OPSMENDER_LOG_LEVEL=DEBUG\n"
    + "OPSMENDER_AUDIT_LOG=./logs/audit.jsonl\n"
)


def _write_cfg(tmp_path, content=MINIMAL_CFG):
    cfg = tmp_path / ".env"
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
        bad_cfg = "OPSMENDER_TIER=9\nOPSMENDER_LOG_LEVEL=INFO\nOPSMENDER_AUDIT_LOG=./logs/audit.jsonl\n"
        cfg_path = _write_cfg(tmp_path, bad_cfg)
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", cfg_path, "config", "--validate"])
        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "tiers.default must be 0-3" in out

    def test_missing_tier_uses_default(self, tmp_path, capsys):
        cfg_path = _write_cfg(tmp_path, "OPSMENDER_LOG_LEVEL=INFO\n")
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", cfg_path, "config", "--validate"])
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "Validation OK" in out

    def test_validate_with_valid_skill_file(self, tmp_path, capsys):
        cfg_path = _write_cfg(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "--config",
                    cfg_path,
                    "config",
                    "--validate",
                    "--skill-file",
                    "examples/SKILL.md",
                ]
            )
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "Validation OK" in out
        assert "operations" in out

    def test_validate_with_missing_skill_file(self, tmp_path, capsys):
        cfg_path = _write_cfg(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "--config",
                    cfg_path,
                    "config",
                    "--validate",
                    "--skill-file",
                    "/tmp/nonexistent.md",
                ]
            )
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

        monkeypatch.setattr(
            "cli.opsmender.ProviderRegistry.discover_models", _discover_models
        )

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
        db_path = tmp_path / "opsmender.db"
        database_url = f"sqlite+aiosqlite:///{db_path}"
        _create_sqlite_schema(database_url)
        monkeypatch.setenv("OPSMENDER_DATABASE_URL", database_url)
        monkeypatch.setattr(
            "cli.opsmender.ProviderRegistry.validate_model_config",
            lambda self, **kwargs: type(
                "_Validation",
                (),
                {"warnings": []},
            )(),
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
        assert data["config"]["provider"] == "ollama"
        assert data["config"]["model_id"] == "llama3.2"
        assert data["config"]["is_default"] is True
        assert data["warnings"] == []

        async def _verify() -> None:
            engine = create_async_engine(database_url, echo=False)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                default = await ModelConfigRepo.get_default(session, TEST_ORG_ID)
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
            "cli.opsmender.ProviderRegistry.validate_model_config",
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

    def test_model_bootstrap_prompts_and_prints_warnings(
        self, tmp_path, capsys, monkeypatch
    ):
        cfg_path = _write_cfg(tmp_path)
        db_path = tmp_path / "opsmender.db"
        database_url = f"sqlite+aiosqlite:///{db_path}"
        _create_sqlite_schema(database_url)
        monkeypatch.setenv("OPSMENDER_DATABASE_URL", database_url)
        answers = iter(
            [
                "openai",
                "gpt-5-custom",
                "OPENAI_API_KEY",
            ]
        )
        monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
        monkeypatch.setattr(
            "cli.opsmender.ProviderRegistry.validate_model_config",
            lambda self, **kwargs: type(
                "_Validation",
                (),
                {
                    "warnings": [
                        type(
                            "_Warning",
                            (),
                            {
                                "code": "model_not_reported",
                                "message": "Manual model ID saved with warning.",
                            },
                        )()
                    ]
                },
            )(),
        )

        with pytest.raises(SystemExit) as exc_info:
            main(["--config", cfg_path, "config", "model", "bootstrap"])

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "provider=openai" in captured.out
        assert "Manual model ID saved with warning." in captured.err

    def test_model_bootstrap_with_flags_skips_prompts(
        self, tmp_path, capsys, monkeypatch
    ):
        cfg_path = _write_cfg(tmp_path)
        db_path = tmp_path / "opsmender.db"
        database_url = f"sqlite+aiosqlite:///{db_path}"
        _create_sqlite_schema(database_url)
        monkeypatch.setenv("OPSMENDER_DATABASE_URL", database_url)
        captured_kwargs: dict[str, object] = {}

        def _validate(self, **kwargs):
            captured_kwargs.update(kwargs)
            return type("_Validation", (), {"warnings": []})()

        monkeypatch.setattr(
            "cli.opsmender.ProviderRegistry.validate_model_config",
            _validate,
        )

        def _boom(prompt=""):
            raise AssertionError(
                f"bootstrap should not prompt when all flags are supplied; got prompt={prompt!r}"
            )

        monkeypatch.setattr("builtins.input", _boom)

        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "--config",
                    cfg_path,
                    "config",
                    "model",
                    "bootstrap",
                    "--provider",
                    "ollama",
                    "--model-id",
                    "llama3.2",
                    "--base-url",
                    "http://localhost:11434",
                ]
            )

        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "provider=ollama" in out
        assert captured_kwargs["provider"] == "ollama"
        assert captured_kwargs["model_id"] == "llama3.2"
        assert captured_kwargs["base_url"] == "http://localhost:11434"
        assert captured_kwargs["allow_unverified"] is True

        async def _verify() -> None:
            engine = create_async_engine(database_url, echo=False)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                default = await ModelConfigRepo.get_default(session, TEST_ORG_ID)
                assert default is not None
                assert default.provider == "ollama"
                assert default.model_id == "llama3.2"
                assert default.name == "ollama:llama3.2"
                assert default.is_default is True
            await engine.dispose()

        asyncio.run(_verify())

    def test_model_bootstrap_azure_prompts_base_url_and_api_version(
        self, tmp_path, capsys, monkeypatch
    ):
        cfg_path = _write_cfg(tmp_path)
        db_path = tmp_path / "opsmender.db"
        database_url = f"sqlite+aiosqlite:///{db_path}"
        _create_sqlite_schema(database_url)
        monkeypatch.setenv("OPSMENDER_DATABASE_URL", database_url)
        captured_kwargs: dict[str, object] = {}

        def _validate(self, **kwargs):
            captured_kwargs.update(kwargs)
            return type("_Validation", (), {"warnings": []})()

        monkeypatch.setattr(
            "cli.opsmender.ProviderRegistry.validate_model_config",
            _validate,
        )
        prompts_seen: list[str] = []
        answers = iter(
            [
                "https://example-resource.openai.azure.com/",
                "2024-10-21",
            ]
        )

        def _fake_input(prompt=""):
            prompts_seen.append(prompt)
            return next(answers)

        monkeypatch.setattr("builtins.input", _fake_input)

        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "--config",
                    cfg_path,
                    "config",
                    "model",
                    "bootstrap",
                    "--provider",
                    "azure_openai",
                    "--model-id",
                    "deploy-gpt4",
                    "--api-key-env-var",
                    "AZURE_OPENAI_API_KEY",
                ]
            )

        assert exc_info.value.code == 0
        assert captured_kwargs["provider"] == "azure_openai"
        assert (
            captured_kwargs["base_url"] == "https://example-resource.openai.azure.com/"
        )
        assert captured_kwargs["api_version"] == "2024-10-21"
        prompt_blob = " ".join(prompts_seen)
        assert "Base URL" in prompt_blob
        assert "API version" in prompt_blob

    def test_model_bootstrap_json_output(self, tmp_path, capsys, monkeypatch):
        cfg_path = _write_cfg(tmp_path)
        db_path = tmp_path / "opsmender.db"
        database_url = f"sqlite+aiosqlite:///{db_path}"
        _create_sqlite_schema(database_url)
        monkeypatch.setenv("OPSMENDER_DATABASE_URL", database_url)
        monkeypatch.setattr(
            "cli.opsmender.ProviderRegistry.validate_model_config",
            lambda self, **kwargs: type(
                "_Validation",
                (),
                {
                    "warnings": [
                        type(
                            "_Warning",
                            (),
                            {
                                "code": "provider_unverified",
                                "message": "Could not verify provider connectivity.",
                            },
                        )()
                    ]
                },
            )(),
        )
        monkeypatch.setattr(
            "builtins.input",
            lambda prompt="": (_ for _ in ()).throw(
                AssertionError("flag-driven bootstrap should not prompt")
            ),
        )

        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "--config",
                    cfg_path,
                    "config",
                    "model",
                    "bootstrap",
                    "--provider",
                    "openai",
                    "--model-id",
                    "gpt-5-custom",
                    "--api-key-env-var",
                    "OPENAI_API_KEY",
                    "--name",
                    "primary-manual",
                    "--json",
                ]
            )

        assert exc_info.value.code == 0
        data = json.loads(capsys.readouterr().out)
        assert data["config"]["name"] == "primary-manual"
        assert data["config"]["provider"] == "openai"
        assert data["config"]["model_id"] == "gpt-5-custom"
        assert data["config"]["is_default"] is True
        assert data["warnings"][0]["code"] == "provider_unverified"
