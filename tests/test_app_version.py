"""The version the API reports is the installed package's version, the same one
the CLI's --version prints, so a release bump reaches every surface."""

import importlib.metadata

from backend.config_loader import AppConfig, AppSettings, _package_version


def test_the_app_settings_default_to_the_package_version():
    expected = importlib.metadata.version("opsmender")
    assert _package_version() == expected
    assert AppSettings().version == expected
    assert AppConfig.load().app.version == expected


def test_the_release_version_is_the_packaged_one():
    assert importlib.metadata.version("opsmender") == "1.1.1"
