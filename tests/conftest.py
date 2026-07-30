"""Shared pytest configuration and fixtures."""

import os

import pytest


# Sprint 56: the API now refuses anonymous /auth/register in production
# mode once any user exists. Test fixtures rely on register-based user
# bootstrap, so default the suite to development mode. Tests that
# specifically exercise production-safety branches monkeypatch the env
# var themselves (see tests/test_production_safety_guard.py). Set both the
# legacy value and the current topology-aware value so a repository-local
# production .env cannot leak into SQLite-backed tests.
os.environ.setdefault("OPSMENDER_DEPLOYMENT_MODE", "development")
os.environ.setdefault("OPSMENDER_ENVIRONMENT", "development")


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run live integration tests (requires K8s cluster + MCP server)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-integration"):
        return
    skip = pytest.mark.skip(reason="need --run-integration to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
