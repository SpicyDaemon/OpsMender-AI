"""Tests for the process-global log-level configuration.

Before ``backend/logging_config.py`` existed, ``OPSMENDER_LOG_LEVEL`` was read
into config but never applied to Python logging or uvicorn, so the level was
always effectively INFO. These tests lock in the apply behavior.
"""

from __future__ import annotations

import logging

import pytest

from backend.logging_config import configure_logging, normalize_level


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("debug", "DEBUG"),
        ("INFO", "INFO"),
        (" warning ", "WARNING"),
        ("Error", "ERROR"),
        ("critical", "CRITICAL"),
        ("", "INFO"),
        (None, "INFO"),
        ("verbose", "INFO"),  # unknown → safe default
    ],
)
def test_normalize_level(raw, expected):
    assert normalize_level(raw) == expected


def test_configure_logging_sets_root_and_uvicorn_levels():
    try:
        applied = configure_logging("CRITICAL")
        assert applied == "CRITICAL"
        assert logging.getLogger().level == logging.CRITICAL
        # The uvicorn access logger is what emits the "GET / 200 OK" lines;
        # the setting must govern it too, not just OpsMender's own loggers.
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            assert logging.getLogger(name).level == logging.CRITICAL
    finally:
        # Restore a sane default so other tests aren't silenced.
        configure_logging("INFO")


def test_configure_logging_unknown_falls_back_to_info():
    try:
        assert configure_logging("nonsense") == "INFO"
        assert logging.getLogger().level == logging.INFO
    finally:
        configure_logging("INFO")
