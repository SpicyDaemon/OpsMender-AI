"""Generic command-execution tool detection (v1 safety guardrail).

Generic execution tools run *arbitrary* commands, so the tool **name** alone
does not bound what they can do (e.g. ``run_command`` could read a log or delete
a cluster). Classifying them by name is therefore unsafe. OpsMender treats them
conservatively at the tier gate:

  - Tier 2 (Advisory)        → blocked
  - Tier 1 (Approval Req.)   → requires operator approval
  - Tier 0 (Autonomous)      → blocked (no command-pattern allowlisting yet)

The gate checks this guard after the deny-list and before the tool's own tier
policy, so at Tier 1 a caught tool asks for approval even when its Skill entry
blocks it or makes it advisory, or no entry lists it. A name added here
therefore moves such a tool from refused to approval-required at Tier 1. A
``deny: true`` entry still blocks it at every tier, and an exact entry with
``allow_generic: true`` puts it back under its own policies.

An operator who has genuinely scoped a wrapper tool can opt a specific tool out
of this guardrail by listing it in the MCP Skill with ``allow_generic: true``;
normal tier/classification rules then apply. This is the documented escape
hatch - use it only for narrowly-scoped tools.

Detection is intentionally conservative (a curated set + a few prefixes and
suffixes) to avoid false positives on normal tools like ``get_pods`` or
``scale_deployment``. A name matches as a whole or by a prefix or suffix, never
by a word in the middle, so ``refresh`` and ``dashboard`` are not shells. A few
affixes also catch bounded tools: ``docker_logs`` and ``console_url`` match the
``docker_`` and ``console_`` prefixes and ``get_console`` and ``aws_console``
the ``_console`` suffix, so a Skill that trusts such a tool lists it by its
exact name with ``allow_generic: true``.
"""

from __future__ import annotations

import re

# Exact tool names (case-insensitive) that execute arbitrary commands.
_GENERIC_EXACT: frozenset[str] = frozenset(
    {
        # shells / generic runners
        "shell",
        "bash",
        "sh",
        "zsh",
        "fish",
        "powershell",
        "pwsh",
        "cmd",
        "shellexec",
        "bash_tool",
        "shell_tool",
        "run",
        "run_command",
        "runcommand",
        "run_cmd",
        "exec",
        "execute",
        "command",
        "eval",
        "evaluate",
        "spawn",
        "system",
        # terminals, process spawning and privilege escalation
        "terminal",
        "console",
        "tty",
        "pty",
        "subprocess",
        "popen",
        "os_system",
        "sudo",
        "su",
        # container runtimes / host services
        "docker",
        "podman",
        "nerdctl",
        "crictl",
        "systemctl",
        # cloud / infra CLIs
        "kubectl",
        "oc",
        "helm",
        "aws",
        "aws_cli",
        "awscli",
        "gcloud",
        "az",
        "azure_cli",
        "azurecli",
        "terraform",
        "tf",
        "pulumi",
        "ansible",
        "ansible_playbook",
        "salt",
        "chef",
        "puppet",
        # database / query runners
        "sql",
        "psql",
        "mysql",
        "sqlcmd",
        "mongo",
        "redis_cli",
        "query",
        "run_query",
        "execute_sql",
        "exec_sql",
        "raw_query",
        # language runtimes
        "python",
        "python3",
        "node",
        "nodejs",
        "ruby",
        "perl",
        "php",
        "groovy",
        "jshell",
        "repl",
        "python_repl",
        "code_interpreter",
        "interpreter",
        # network / transfer (can exfiltrate or fetch+run)
        "curl",
        "wget",
        "ssh",
        "scp",
        "sftp",
        "telnet",
        "nc",
        "netcat",
        # build / package
        "make",
        "npm",
        "npx",
        "pip",
        "apt",
        "yum",
        "apk",
    }
)

# Affixes that signal an arbitrary-command tool. ``docker_``, ``console_`` and
# ``_console`` also catch bounded tools such as ``docker_logs`` and
# ``get_console`` (see the module docstring).
_GENERIC_PREFIXES: tuple[str, ...] = (
    "exec_",
    "run_",
    "shell_",
    "cmd_",
    "eval_",
    "bash_",
    "sh_",
    "terminal_",
    "console_",
    "sudo_",
    "docker_",
    "subprocess_",
)
_GENERIC_SUFFIXES: tuple[str, ...] = (
    "_exec",
    "_shell",
    "_command",
    "_cmd",
    "_cli",
    "_eval",
    "_query",
    "_run",
    "_script",
    "_repl",
    "_interpreter",
    "_terminal",
    "_console",
    "_bash",
    "_subprocess",
)


def _fold(name: str) -> str:
    return re.sub(r"[\s.\-/:]+", "_", name.strip()).strip("_").lower()


def _spellings(tool_name: str) -> set[str]:
    """The name with case and separators folded, as written and with camel case
    split: ``KubeCtl`` stays ``kubectl``, ``ExecuteCommand`` also reads
    ``execute_command``, and ``Run Command`` and ``run-command`` read
    ``run_command`` (R-12)."""
    return {_fold(tool_name), _fold(re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", tool_name))}


def is_generic_execution_tool(tool_name: str) -> bool:
    """Return True when *tool_name* looks like an arbitrary-command runner.

    Conservative: matches a curated exact-name set plus a small set of
    prefixes/suffixes. Normal infra tools (``get_pods``, ``scale_deployment``,
    ``describe_node``) are not flagged.
    """
    if not tool_name:
        return False
    for name in _spellings(tool_name):
        if name in _GENERIC_EXACT:
            return True
        if any(name.startswith(p) for p in _GENERIC_PREFIXES):
            return True
        if any(name.endswith(s) for s in _GENERIC_SUFFIXES):
            return True
    return False
