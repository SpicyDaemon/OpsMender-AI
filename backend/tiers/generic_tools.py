"""Generic command-execution tool detection (v1 safety guardrail).

Generic execution tools run *arbitrary* commands, so the tool **name** alone
does not bound what they can do (e.g. ``run_command`` could read a log or delete
a cluster). Classifying them by name is therefore unsafe. OpsMender treats them
conservatively at the tier gate:

  - Tier 2 (Advisory)        → blocked
  - Tier 1 (Approval Req.)   → requires operator approval
  - Tier 0 (Autonomous)      → blocked (no command-pattern allowlisting yet)

An operator who has genuinely scoped a wrapper tool can opt a specific tool out
of this guardrail by listing it in the MCP Skill with ``allow_generic: true``;
normal tier/classification rules then apply. This is the documented escape
hatch — use it only for narrowly-scoped tools.

Detection is intentionally conservative (a curated set + a few unambiguous
prefixes/suffixes) to avoid false positives on normal tools like ``get_pods``
or ``scale_deployment``.
"""

from __future__ import annotations

# Exact tool names (case-insensitive) that execute arbitrary commands.
_GENERIC_EXACT: frozenset[str] = frozenset(
    {
        # shells / generic runners
        "shell", "bash", "sh", "zsh", "fish", "powershell", "pwsh", "cmd",
        "run", "run_command", "runcommand", "run_cmd", "exec", "execute",
        "command", "eval", "evaluate", "spawn", "system",
        # cloud / infra CLIs
        "kubectl", "oc", "helm", "aws", "aws_cli", "awscli", "gcloud",
        "az", "azure_cli", "azurecli", "terraform", "tf", "pulumi",
        "ansible", "ansible_playbook", "salt", "chef", "puppet",
        # database / query runners
        "sql", "psql", "mysql", "sqlcmd", "mongo", "redis_cli", "query",
        "run_query", "execute_sql", "exec_sql", "raw_query",
        # language runtimes
        "python", "python3", "node", "nodejs", "ruby", "perl", "php",
        "groovy", "jshell",
        # network / transfer (can exfiltrate or fetch+run)
        "curl", "wget", "ssh", "scp", "sftp", "telnet", "nc", "netcat",
        # build / package
        "make", "npm", "npx", "pip", "apt", "yum", "apk",
    }
)

# Unambiguous affixes that signal an arbitrary-command tool.
_GENERIC_PREFIXES: tuple[str, ...] = ("exec_", "run_", "shell_", "cmd_", "eval_")
_GENERIC_SUFFIXES: tuple[str, ...] = (
    "_exec", "_shell", "_command", "_cmd", "_cli", "_eval", "_query",
    "_run", "_script",
)


def is_generic_execution_tool(tool_name: str) -> bool:
    """Return True when *tool_name* looks like an arbitrary-command runner.

    Conservative: matches a curated exact-name set plus a small set of
    unambiguous prefixes/suffixes. Normal infra tools (``get_pods``,
    ``scale_deployment``, ``describe_node``) are not flagged.
    """
    if not tool_name:
        return False
    name = tool_name.strip().lower()
    if name in _GENERIC_EXACT:
        return True
    if any(name.startswith(p) for p in _GENERIC_PREFIXES):
        return True
    if any(name.endswith(s) for s in _GENERIC_SUFFIXES):
        return True
    return False
