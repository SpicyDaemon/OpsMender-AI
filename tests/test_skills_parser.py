"""Tests for backend.skills.parser."""

import pytest

from backend.skills.parser import OperationClassification, SkillDefinition, load


@pytest.fixture()
def skill_md(tmp_path):
    """Write a SKILL.md with YAML front-matter and return its path."""
    p = tmp_path / "SKILL.md"
    p.write_text(
        "---\n"
        "version: '1'\n"
        "environment: test\n"
        "operations:\n"
        "  - tool: get_pods\n"
        "    classification: safe\n"
        "  - tool: scale_deployment\n"
        "    classification: caution\n"
        "    notes: changes replicas\n"
        "  - tool: delete_*\n"
        "    classification: destructive\n"
        "---\n"
        "\n"
        "# Test Skill Definition\n"
        "Some documentation here.\n"
    )
    return p


@pytest.fixture()
def skill_yaml(tmp_path):
    """Write a SKILL.yaml and return its path."""
    p = tmp_path / "SKILL.yaml"
    p.write_text(
        "version: '2'\n"
        "environment: staging\n"
        "operations:\n"
        "  - tool: list_*\n"
        "    classification: safe\n"
    )
    return p


class TestLoad:
    def test_load_md_file(self, skill_md):
        sd = load(skill_md)
        assert sd.version == "1"
        assert sd.environment == "test"
        assert len(sd.operations) == 3

    def test_load_yaml_file(self, skill_yaml):
        sd = load(skill_yaml)
        assert sd.version == "2"
        assert sd.environment == "staging"
        assert len(sd.operations) == 1

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load(tmp_path / "nonexistent.md")

    def test_empty_file_returns_empty_ops(self, tmp_path):
        p = tmp_path / "SKILL.md"
        p.write_text("---\n---\n")
        sd = load(p)
        assert sd.operations == []
        assert sd.version == "1"
        assert sd.environment == "default"

    def test_load_reference_template(self):
        """Ensure the shipped examples/SKILL.md parses correctly."""
        sd = load("examples/SKILL.md")
        assert sd.version == "1"
        assert sd.environment == "kubernetes-production"
        assert len(sd.operations) > 10


class TestClassify:
    def test_exact_match(self, skill_md):
        sd = load(skill_md)
        assert sd.classify("get_pods") == "safe"
        assert sd.classify("scale_deployment") == "caution"

    def test_wildcard_match(self, skill_md):
        sd = load(skill_md)
        assert sd.classify("delete_pod") == "destructive"
        assert sd.classify("delete_namespace") == "destructive"

    def test_unknown_tool(self, skill_md):
        sd = load(skill_md)
        assert sd.classify("exec_into_pod") == "unknown"

    def test_exact_match_takes_priority(self, tmp_path):
        """If a tool matches both exact and wildcard, exact wins."""
        p = tmp_path / "SKILL.md"
        p.write_text(
            "---\n"
            "version: '1'\n"
            "environment: test\n"
            "operations:\n"
            "  - tool: delete_configmap\n"
            "    classification: caution\n"
            "  - tool: delete_*\n"
            "    classification: destructive\n"
            "---\n"
        )
        sd = load(p)
        assert sd.classify("delete_configmap") == "caution"
        assert sd.classify("delete_pod") == "destructive"


class TestOperationClassification:
    def test_invalid_classification_raises(self):
        with pytest.raises(ValueError, match="classification must be"):
            OperationClassification(tool="foo", classification="dangerous")

    def test_valid_classifications(self):
        for c in ("safe", "caution", "destructive"):
            op = OperationClassification(tool="x", classification=c)
            assert op.classification == c
