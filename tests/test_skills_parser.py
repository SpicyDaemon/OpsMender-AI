"""Tests for backend.skills.parser."""

import pytest

from backend.skills.convert import convert_legacy_skill_content
from backend.skills.parser import OperationClassification, load, loads


def _explicit(raw: str, *, fmt: str = "md") -> str:
    return convert_legacy_skill_content(raw, fmt=fmt).content


@pytest.fixture()
def skill_md(tmp_path):
    """Write a SKILL.md with YAML front-matter and return its path."""
    p = tmp_path / "SKILL.md"
    p.write_text(
        _explicit(
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
    )
    return p


@pytest.fixture()
def skill_yaml(tmp_path):
    """Write a SKILL.yaml and return its path."""
    p = tmp_path / "SKILL.yaml"
    p.write_text(
        _explicit(
            "version: '2'\n"
            "environment: staging\n"
            "operations:\n"
            "  - tool: list_*\n"
            "    classification: safe\n",
            fmt="yaml",
        )
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

    def test_yaml_root_must_be_mapping(self):
        with pytest.raises(ValueError, match="must be a mapping"):
            loads("---\n- not\n- a mapping\n---\n")
        with pytest.raises(ValueError, match="must be a mapping"):
            loads("- not\n- a mapping\n", fmt="yaml")

    def test_load_reference_template(self):
        """Ensure the shipped examples/SKILL.md parses correctly."""
        sd = load("examples/SKILL.md")
        assert sd.version == "1"
        assert sd.environment == "kubernetes-production"
        assert len(sd.operations) > 10

    def test_focus_areas_parsed_when_present(self, tmp_path):
        from backend.skills.parser import loads

        md = (
            "---\n"
            "version: 1\n"
            "environment: ecs-prod\n"
            "focus_areas:\n"
            "  - tasks stuck in PROVISIONING\n"
            "  - services below desired count\n"
            "operations: []\n"
            "---\n"
        )
        sd = loads(md, fmt="md")
        assert sd.focus_areas == [
            "tasks stuck in PROVISIONING",
            "services below desired count",
        ]

    def test_focus_areas_default_empty(self, skill_md):
        sd = load(skill_md)
        assert sd.focus_areas == []

    def test_focus_areas_comma_string(self):
        md = (
            "---\n"
            "version: 1\n"
            "environment: t\n"
            "focus_areas: a, b , c\n"
            "operations: []\n"
            "---\n"
        )
        sd = loads(md, fmt="md")
        assert sd.focus_areas == ["a", "b", "c"]

    def test_workflow_section_parses_ordered_yaml_steps(self):
        sd = loads(
            _explicit(
                """---
version: "1"
environment: test
operations:
  - tool: find_pod
    classification: safe
  - tool: restart_pod
    classification: caution
---

## Workflow

```yaml
steps:
  - id: find
    description: Find the failing pod
    tool: find_pod
    inputs:
      incident_id: "{{incident.id}}"
    on_failure: abort
  - id: restart
    description: Restart the selected pod
    tool: restart_pod
    inputs:
      pod: "{{steps.find.output.pod}}"
    on_failure: continue
    tier_override: approval
```
"""
            )
        )

        assert [step.id for step in sd.workflow] == ["find", "restart"]
        assert sd.workflow[0].inputs == {"incident_id": "{{incident.id}}"}
        assert sd.workflow[1].on_failure == "continue"
        assert sd.workflow[1].tier_override == "approval"

    def test_workflow_rejects_duplicate_step_ids(self):
        with pytest.raises(ValueError, match="duplicated"):
            loads(
                """---
operations: []
---
## Workflow
```yaml
- id: duplicate
  tool: first
- id: duplicate
  tool: second
```
"""
            )


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
            _explicit(
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


class TestReversibilityAndInverse:
    """Sprint 17 — reversible + compensating_inverse fields."""

    def test_safe_implicitly_reversible(self):
        op = OperationClassification(tool="get_pods", classification="safe")
        assert op.effective_reversible is True

    def test_caution_not_reversible_by_default(self):
        op = OperationClassification(tool="rollout_restart", classification="caution")
        assert op.effective_reversible is False

    def test_destructive_not_reversible_by_default(self):
        op = OperationClassification(tool="delete_pod", classification="destructive")
        assert op.effective_reversible is False

    def test_explicit_reversible_override(self):
        op = OperationClassification(
            tool="cordon_node",
            classification="caution",
            reversible=True,
        )
        assert op.effective_reversible is True

    def test_explicit_reversible_false_on_safe(self):
        """Edge case: operator can declare even a 'safe' op non-reversible."""
        op = OperationClassification(
            tool="heavy_query", classification="safe", reversible=False
        )
        assert op.effective_reversible is False

    def test_parser_reads_reversible_and_inverse(self, tmp_path):
        p = tmp_path / "SKILL.md"
        p.write_text(
            _explicit(
                "---\n"
                "version: '1'\n"
                "environment: test\n"
                "operations:\n"
                "  - tool: cordon_node\n"
                "    classification: caution\n"
                "    reversible: true\n"
                "    compensating_inverse: uncordon_node\n"
                "  - tool: delete_pod\n"
                "    classification: destructive\n"
                "---\n"
            )
        )
        sd = load(p)
        assert sd.is_reversible("cordon_node") is True
        assert sd.inverse_for("cordon_node") == "uncordon_node"
        assert sd.is_reversible("delete_pod") is False
        assert sd.inverse_for("delete_pod") is None

    def test_is_reversible_unknown_tool_is_false(self, skill_md):
        sd = load(skill_md)
        assert sd.is_reversible("never_heard_of_this_tool") is False
        assert sd.inverse_for("never_heard_of_this_tool") is None

    def test_is_reversible_follows_wildcard_match(self, tmp_path):
        p = tmp_path / "SKILL.md"
        p.write_text(
            _explicit(
                "---\n"
                "version: '1'\n"
                "environment: test\n"
                "operations:\n"
                "  - tool: describe_*\n"
                "    classification: safe\n"
                "---\n"
            )
        )
        sd = load(p)
        assert sd.is_reversible("describe_pod") is True

    def test_tier0_violation_requires_inverse_for_side_effecting_op(self, tmp_path):
        p = tmp_path / "SKILL.md"
        p.write_text(
            _explicit(
                "---\n"
                "version: '1'\n"
                "environment: test\n"
                "operations:\n"
                "  - tool: rollout_restart\n"
                "    classification: caution\n"
                "    reversible: true\n"
                "---\n"
            )
        )
        sd = load(p)
        assert sd.is_reversible("rollout_restart") is True
        assert sd.is_tier0_safe("rollout_restart") is False
        assert "compensating_inverse" in (
            sd.tier0_violation_reason("rollout_restart") or ""
        )

    def test_tier0_safe_read_does_not_require_inverse(self, skill_md):
        sd = load(skill_md)
        assert sd.is_tier0_safe("get_pods") is True
