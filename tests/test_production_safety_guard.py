"""Tests for the production default-secret startup guard (Sprint 43 P0 #4)."""

from __future__ import annotations

import pytest

from backend.config_loader import (
    AppConfig,
    AuthConfig,
    InsecureProductionConfigError,
    check_production_safety,
)


def _config_with_secret(secret: str) -> AppConfig:
    """Build a minimal AppConfig carrying just the auth secret under test."""
    cfg = AppConfig.__new__(AppConfig)
    cfg.auth = AuthConfig(jwt_secret=secret)
    return cfg


class TestProductionSafetyGuard:
    def test_strong_secret_in_prod_passes(self, monkeypatch):
        monkeypatch.delenv("OPSMENDER_DEPLOYMENT_MODE", raising=False)
        check_production_safety(_config_with_secret("a-real-secret-from-openssl-rand"))

    def test_strong_secret_in_dev_passes(self, monkeypatch):
        monkeypatch.setenv("OPSMENDER_DEPLOYMENT_MODE", "development")
        check_production_safety(_config_with_secret("anything"))

    def test_env_example_default_refused_in_prod(self, monkeypatch):
        monkeypatch.delenv("OPSMENDER_DEPLOYMENT_MODE", raising=False)
        with pytest.raises(InsecureProductionConfigError, match="change-me-in-production"):
            check_production_safety(_config_with_secret("change-me-in-production"))

    def test_code_default_refused_in_prod(self, monkeypatch):
        monkeypatch.delenv("OPSMENDER_DEPLOYMENT_MODE", raising=False)
        with pytest.raises(InsecureProductionConfigError):
            check_production_safety(
                _config_with_secret("dev-secret-change-in-production")
            )

    def test_default_secret_in_dev_passes(self, monkeypatch):
        monkeypatch.setenv("OPSMENDER_DEPLOYMENT_MODE", "development")
        # Same value that would refuse in prod, but development opts out.
        check_production_safety(_config_with_secret("change-me-in-production"))

    def test_explicit_production_value_refused(self, monkeypatch):
        monkeypatch.setenv("OPSMENDER_DEPLOYMENT_MODE", "production")
        with pytest.raises(InsecureProductionConfigError):
            check_production_safety(_config_with_secret("change-me-in-production"))

    def test_whitespace_around_secret_does_not_bypass(self, monkeypatch):
        monkeypatch.delenv("OPSMENDER_DEPLOYMENT_MODE", raising=False)
        with pytest.raises(InsecureProductionConfigError):
            check_production_safety(_config_with_secret("  change-me-in-production  "))

    def test_mode_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("OPSMENDER_DEPLOYMENT_MODE", "DEVELOPMENT")
        check_production_safety(_config_with_secret("change-me-in-production"))
