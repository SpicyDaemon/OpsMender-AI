"""M1-44 (R-12): when several globs in a skill match a tool, the strictest
wins whatever order they are written in, and the generic-runner guard folds
case and separators. The frozen enforcement module is unchanged."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from backend.skills.parser import (
    OperationClassification,
    OperationTierPolicy,
    SkillDefinition,
)
from backend.tiers import enforcement
from backend.tiers.generic_tools import is_generic_execution_tool

FROZEN = "bfe03d3f3a62dfbf1f8ea85f6ba4471f2252ad68b7cb904a695a4986bf7afd65"


def _op(tool: str, classification: str, mode: str, **kwargs) -> OperationClassification:
    enabled = mode != "blocked"
    return OperationClassification(
        tool=tool,
        classification=classification,
        tiers={tier: OperationTierPolicy(enabled, mode) for tier in (0, 1, 2)},
        **kwargs,
    )


BROAD = _op("delete_*", "caution", "approval")
NARROW = _op("delete_prod_*", "destructive", "blocked")


def _skill(*operations) -> SkillDefinition:
    return SkillDefinition(version="1", environment="test", operations=list(operations))


@pytest.mark.parametrize("order", ["broad-first", "narrow-first"])
def test_the_strictest_matching_glob_wins_in_either_order(order):
    skill = _skill(BROAD, NARROW) if order == "broad-first" else _skill(NARROW, BROAD)

    assert skill.operation_for("delete_prod_db") is NARROW
    assert skill.operation_for("delete_cache") is BROAD
    for tier in (1, 2):
        assert enforcement.check("delete_prod_db", tier, skill).permitted is False
    assert enforcement.check("delete_cache", 1, skill).permitted is True


def test_an_exact_entry_still_decides_for_its_own_tool():
    exact = _op("delete_prod_scratch", "safe", "approval", reversible=True)
    skill = _skill(NARROW, BROAD, exact)

    assert skill.operation_for("delete_prod_scratch") is exact


def test_ties_keep_the_stricter_classification_then_reversibility():
    loose = _op("restart_*", "caution", "approval", reversible=True)
    strict = _op("restart_db_*", "caution", "approval", reversible=False)

    assert _skill(loose, strict).operation_for("restart_db_main") is strict
    assert _skill(strict, loose).operation_for("restart_db_main") is strict


@pytest.mark.parametrize(
    "name",
    [
        "run-command",
        "kubectl-exec",
        "ExecuteCommand",
        "python_repl",
        "code_interpreter",
        "RunCommand",
        "Run Command",
        "shell.exec",
        "PYTHON",
    ],
)
def test_runner_names_are_caught_whatever_their_case_or_separators(name):
    assert is_generic_execution_tool(name) is True


@pytest.mark.parametrize(
    "name", ["get_pods", "getPods", "scale_deployment", "list-nodes", "describe_node"]
)
def test_ordinary_tools_are_not_runners(name):
    assert is_generic_execution_tool(name) is False


def test_the_frozen_enforcement_module_is_unchanged():
    path = Path(enforcement.__file__)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == FROZEN
