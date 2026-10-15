"""M1-44 (R-12): when several globs in a skill match a tool, the strictest
wins whatever order they are written in, and the generic-runner guard folds
case and separators. The frozen enforcement module is unchanged.

M1-52 (N-06): common runner names (terminals, process spawners, sudo and the
container tools) are generic runners in every spelling, and a bounded tool that
one of the new prefixes or suffixes catches follows its own policy again once
its exact entry sets allow_generic. The frozen gate checks the runner guard
before a tool's own tier policy, so at Tier 1 a newly caught tool asks for
approval even where its Skill blocks it; a deny-list entry refuses it at every
tier."""

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

# The module with its line endings normalized to LF: the repository blob is
# the identity, and a Windows checkout may carry CRLF.
FROZEN = "0aad2e9b526f1ff9f92c3d13a2601c834d48ee6b234e7792ea4d810f99d3ebe0"


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
        "KubeCtl",
    ],
)
def test_runner_names_are_caught_whatever_their_case_or_separators(name):
    assert is_generic_execution_tool(name) is True


# Each runner word or affix the card names (M1-52) with other spellings a tool
# source may use.
RUNNER_SPELLINGS = {
    # exact names; powershell, pwsh, cmd and shell_tool were caught before M1-52
    "powershell": ("PowerShell", "POWERSHELL", "Powershell"),
    "pwsh": ("Pwsh", "PWSH"),
    "cmd": ("Cmd", "CMD"),
    "terminal": ("Terminal", "TERMINAL"),
    "console": ("Console",),
    "tty": ("TTY",),
    "pty": ("Pty",),
    "shellexec": ("ShellExec", "SHELLEXEC"),
    "bash_tool": ("BashTool", "bash-tool", "bash.tool", "Bash Tool"),
    "shell_tool": ("ShellTool", "shell-tool", "shell.tool"),
    "subprocess": ("Subprocess", "SubProcess"),
    "popen": ("Popen",),
    "os_system": ("OsSystem", "os-system", "os.system"),
    "sudo": ("Sudo",),
    "su": ("SU",),
    "docker": ("Docker",),
    "podman": ("Podman",),
    "nerdctl": ("NerdCtl",),
    "crictl": ("CriCtl",),
    "systemctl": ("SystemCtl",),
    # prefixes
    "bash_session": ("BashSession", "bash-session", "bash.session"),
    "sh_session": ("ShSession", "sh-session", "sh.session"),
    "terminal_session": ("TerminalSession", "terminal-session", "terminal.session"),
    "console_session": ("ConsoleSession", "console-session", "console.session"),
    "sudo_edit": ("SudoEdit", "sudo-edit", "sudo.edit"),
    "docker_compose": ("DockerCompose", "docker-compose", "docker.compose"),
    "subprocess_check_output": (
        "SubprocessCheckOutput",
        "subprocess-check-output",
        "subprocess.check_output",
    ),
    # suffixes
    "open_terminal": ("OpenTerminal", "open-terminal", "open.terminal"),
    "web_console": ("WebConsole", "web-console", "web.console"),
    "invoke_bash": ("InvokeBash", "invoke-bash", "invoke.bash"),
    "spawn_subprocess": ("SpawnSubprocess", "spawn-subprocess", "spawn.subprocess"),
}


@pytest.mark.parametrize(
    "name",
    [
        spelling
        for word, others in RUNNER_SPELLINGS.items()
        for spelling in (word, *others)
    ],
)
def test_common_runner_names_are_caught_in_every_spelling(name):
    assert is_generic_execution_tool(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "get_pods",
        "getPods",
        "scale_deployment",
        "list-nodes",
        "describe_node",
        "refresh",
        "dashboard",
        "describe_pod",
        "list_containers",
        "get_console_output",
    ],
)
def test_ordinary_tools_are_not_runners(name):
    assert is_generic_execution_tool(name) is False


@pytest.mark.parametrize(
    "tool", ["docker_logs", "console_url", "get_console", "aws_console"]
)
def test_a_bounded_tool_a_new_affix_catches_runs_with_allow_generic(tool):
    guarded = _skill(_op(tool, "safe", "autonomous"))
    trusted = _skill(_op(tool, "safe", "autonomous", allow_generic=True))
    held_back = _skill(_op(tool, "safe", "blocked", allow_generic=True))

    assert is_generic_execution_tool(tool) is True
    blocked = enforcement.check(tool, 0, guarded)
    assert blocked.permitted is False
    assert blocked.classification == "generic_execution"
    assert enforcement.check(tool, 1, guarded).requires_approval is True
    allowed = enforcement.check(tool, 0, trusted)
    assert allowed.permitted is True
    assert allowed.classification == "safe"
    # allow_generic puts the tool back under its own policies, a block included.
    own_block = enforcement.check(tool, 1, held_back)
    assert own_block.permitted is False
    assert own_block.reason == "explicit T1 skill policy: blocked"


@pytest.mark.parametrize("t1", ["blocked", "advisory", None])
def test_a_new_runner_name_asks_for_approval_at_tier_1_over_its_own_policy(t1):
    """The frozen gate checks the runner guard before a tool's own tier
    policy, so at Tier 1 ``sudo`` asks for approval even where its entry
    blocks it or makes it advisory, or no entry lists it; each was refused
    before M1-52. Tier 0 and Tier 2 refuse it, and a deny-list entry refuses
    it at every tier."""
    listed = [_op("sudo", "destructive", t1)] if t1 else []
    skill = _skill(*listed, _op("get_pods", "safe", "autonomous"))
    denied = _skill(_op("sudo", "destructive", "blocked", deny=True))

    approval = enforcement.check("sudo", 1, skill)
    assert approval.permitted is True
    assert approval.requires_approval is True
    assert approval.classification == "generic_execution"
    for tier in (0, 2):
        refused = enforcement.check("sudo", tier, skill)
        assert refused.permitted is False
        assert refused.classification == "generic_execution"
    for tier in (0, 1, 2):
        refused = enforcement.check("sudo", tier, denied)
        assert refused.permitted is False
        assert refused.requires_approval is False
        assert refused.reason == "deny-list policy match: blocked at every tier"


def test_the_frozen_enforcement_module_is_unchanged():
    path = Path(enforcement.__file__)
    data = path.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(data).hexdigest() == FROZEN
