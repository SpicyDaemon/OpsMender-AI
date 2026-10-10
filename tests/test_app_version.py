"""The version the API reports is the release version: the installed package's,
the same one the CLI's --version prints, or pyproject.toml's when the code runs
from source as the container image does. A release bump reaches every surface."""

import importlib.metadata
import pathlib
import tomllib

from backend import config_loader
from backend.config_loader import AppConfig, AppSettings, _package_version

PYPROJECT_VERSION = tomllib.loads(
    (pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(
        encoding="utf-8"
    )
)["project"]["version"]


def test_the_app_settings_default_to_the_package_version():
    expected = importlib.metadata.version("opsmender")
    assert _package_version() == expected
    assert AppSettings().version == expected
    assert AppConfig.load().app.version == expected


def test_without_installed_metadata_the_pyproject_version_is_used(monkeypatch):
    def missing(_name):
        raise importlib.metadata.PackageNotFoundError("opsmender")

    monkeypatch.setattr(config_loader.importlib.metadata, "version", missing)
    assert _package_version() == PYPROJECT_VERSION


def test_the_release_version_is_the_packaged_one():
    assert importlib.metadata.version("opsmender") == PYPROJECT_VERSION == "1.1.1"
