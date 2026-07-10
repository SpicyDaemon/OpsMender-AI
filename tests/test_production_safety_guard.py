"""Production startup validation and warning coverage."""

from __future__ import annotations

import logging

import pytest

from backend.config_loader import (
    AppConfig,
    AuthConfig,
    CorsConfig,
    DatabaseConfig,
    DeploymentConfig,
    InsecureProductionConfigError,
    PeopleConfig,
    check_production_safety,
)


def _production_config(tmp_path) -> AppConfig:
    env_file = tmp_path / ".env"
    env_file.write_text("")
    config = AppConfig.load(env_file)
    config.deployment = DeploymentConfig(environment="production")
    config.auth = AuthConfig(jwt_secret="release-hardening-secret")
    config.db = DatabaseConfig(
        url="postgresql+asyncpg://opsmender:secret@db:5432/opsmender"
    )
    config.people = PeopleConfig(
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="StrongBootstrapPassword2026",
        public_base_url="https://opsmender.example.com",
    )
    config.cors = CorsConfig(origins=["https://opsmender.example.com"])
    config.app.tier = 2
    config.app.api_docs_enabled = False
    return config


class TestProductionSafetyGuard:
    def test_well_formed_production_config_passes(self, tmp_path, caplog):
        config = _production_config(tmp_path)

        with caplog.at_level(logging.WARNING, logger="backend.config_loader"):
            check_production_safety(config)

        assert caplog.records == []

    def test_development_mode_keeps_local_defaults(self, tmp_path, caplog):
        env_file = tmp_path / ".env"
        env_file.write_text("")
        config = AppConfig.load(env_file)
        config.deployment = DeploymentConfig(environment="development")

        with caplog.at_level(logging.WARNING, logger="backend.config_loader"):
            check_production_safety(config)

        assert caplog.records == []

    @pytest.mark.parametrize(
        "secret", ["change-me-in-production", "dev-secret-change-in-production"]
    )
    def test_default_jwt_secret_fails(self, tmp_path, secret):
        config = _production_config(tmp_path)
        config.auth.jwt_secret = f"  {secret}  "

        with pytest.raises(InsecureProductionConfigError, match="JWT_SECRET"):
            check_production_safety(config)

    @pytest.mark.parametrize(
        "database_url",
        [None, "", "sqlite+aiosqlite:///opsmender.db", "postgresql://db/opsmender"],
    )
    def test_non_async_postgres_database_fails(self, tmp_path, database_url):
        config = _production_config(tmp_path)
        config.db.url = database_url

        with pytest.raises(
            InsecureProductionConfigError, match="Production requires PostgreSQL"
        ):
            check_production_safety(config)

    @pytest.mark.parametrize(
        "password",
        [
            "admin",
            "ADMIN123",
            "password",
            "changeme",
            "opsmender",
            "OpsMender123",
        ],
    )
    def test_weak_bootstrap_password_fails(self, tmp_path, password):
        config = _production_config(tmp_path)
        config.people.bootstrap_admin_password = f" {password} "

        with pytest.raises(InsecureProductionConfigError, match="known weak default"):
            check_production_safety(config)

    @pytest.mark.parametrize("tier", [-1, 3, 99])
    def test_invalid_tier_fails(self, tmp_path, tier):
        config = _production_config(tmp_path)
        config.app.tier = tier

        with pytest.raises(InsecureProductionConfigError, match="must be 0, 1, or 2"):
            check_production_safety(config)

    def test_wildcard_cors_warns(self, tmp_path, caplog):
        config = _production_config(tmp_path)
        config.cors.origins = ["https://opsmender.example.com", "*"]

        with caplog.at_level(logging.WARNING, logger="backend.config_loader"):
            check_production_safety(config)

        assert "OPSMENDER_CORS_ORIGINS" in caplog.text

    def test_missing_public_base_url_warns(self, tmp_path, caplog):
        config = _production_config(tmp_path)
        config.people.public_base_url = None

        with caplog.at_level(logging.WARNING, logger="backend.config_loader"):
            check_production_safety(config)

        assert "OPSMENDER_PUBLIC_BASE_URL" in caplog.text

    def test_enabled_api_docs_warns(self, tmp_path, caplog):
        config = _production_config(tmp_path)
        config.app.api_docs_enabled = True

        with caplog.at_level(logging.WARNING, logger="backend.config_loader"):
            check_production_safety(config)

        assert "OPSMENDER_ENABLE_API_DOCS" in caplog.text
