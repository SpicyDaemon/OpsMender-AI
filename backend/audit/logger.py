"""Append-only JSONL audit logger for OpsMender AI.

Records every agent action during an incident response session regardless
of tier.  Each entry is a single JSON object written as one line in the
configured JSONL output file.

Entry types
-----------
- ``tool_call_start``  — logged before a tool call is executed
- ``tool_call_end``    — logged after a tool call completes
- ``tool_call_blocked``— logged when tier enforcement denies a tool call
- ``session_start``    — logged when a session begins
- ``session_end``      — logged when a session ends
"""

from __future__ import annotations

import dataclasses
import datetime
import enum
import json
import pathlib
import threading
import uuid
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Entry type enum
# ---------------------------------------------------------------------------

class AuditEntryType(str, enum.Enum):
    """Discriminator for audit log entries."""

    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    TOOL_CALL_BLOCKED = "tool_call_blocked"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    WORKFLOW_STEP_COMPLETED = "workflow.step.completed"
    WORKFLOW_STEP_BLOCKED = "workflow.step.blocked"
    WORKFLOW_STEP_FAILED = "workflow.step.failed"


# ---------------------------------------------------------------------------
# AuditEntry dataclass
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class AuditEntry:
    """A single audit log entry.

    Matches the ``AuditEntry`` data model defined in PROMPT_CONTEXT.md.
    """

    entry_id: str
    session_id: str
    timestamp: str  # ISO-8601 string
    tier: int
    entry_type: AuditEntryType
    tool_name: Optional[str] = None
    tool_parameters: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    permitted: bool = True
    block_reason: Optional[str] = None
    duration_ms: Optional[int] = None

    def to_dict(self) -> dict:
        """Serialise to a plain dict suitable for JSON encoding."""
        d = dataclasses.asdict(self)
        # enum → string value
        d["entry_type"] = self.entry_type.value
        return d


# ---------------------------------------------------------------------------
# AuditLogger
# ---------------------------------------------------------------------------

class AuditLogger:
    """Append-only JSONL audit logger.

    Parameters
    ----------
    output_path:
        File path for the JSONL log.  Parent directories are created
        automatically if they do not exist.
    """

    def __init__(self, output_path: pathlib.Path | str) -> None:
        self._path = pathlib.Path(output_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @property
    def path(self) -> pathlib.Path:
        return self._path

    # -- low-level -----------------------------------------------------------

    def log(self, entry: AuditEntry) -> None:
        """Append *entry* as a single JSON line to the log file."""
        line = json.dumps(entry.to_dict(), default=str) + "\n"
        with self._lock:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line)

    # -- convenience helpers -------------------------------------------------

    @staticmethod
    def _entry_id() -> str:
        return uuid.uuid4().hex[:12]

    @staticmethod
    def _now() -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    def log_tool_call_start(
        self,
        session_id: str,
        tier: int,
        tool_name: str,
        tool_parameters: dict | None = None,
        classification: str | None = None,
    ) -> str:
        """Log a pre-execution entry.  Returns the ``entry_id``.

        ``classification`` is accepted for parity with the WS-emitting audit
        logger (it surfaces the safety class on the live event stream); the
        file-backed log does not persist it.
        """
        entry_id = self._entry_id()
        self.log(
            AuditEntry(
                entry_id=entry_id,
                session_id=session_id,
                timestamp=self._now(),
                tier=tier,
                entry_type=AuditEntryType.TOOL_CALL_START,
                tool_name=tool_name,
                tool_parameters=tool_parameters,
                permitted=True,
            )
        )
        return entry_id

    def log_tool_call_end(
        self,
        session_id: str,
        tier: int,
        tool_name: str,
        result: dict | None = None,
        duration_ms: int | None = None,
        classification: str | None = None,
    ) -> str:
        """Log a post-execution entry.  Returns the ``entry_id``."""
        entry_id = self._entry_id()
        self.log(
            AuditEntry(
                entry_id=entry_id,
                session_id=session_id,
                timestamp=self._now(),
                tier=tier,
                entry_type=AuditEntryType.TOOL_CALL_END,
                tool_name=tool_name,
                result=result,
                permitted=True,
                duration_ms=duration_ms,
            )
        )
        return entry_id

    def log_tool_call_blocked(
        self,
        session_id: str,
        tier: int,
        tool_name: str,
        tool_parameters: dict | None = None,
        block_reason: str | None = None,
        classification: str | None = None,
    ) -> str:
        """Log a blocked/denied tool call.  Returns the ``entry_id``."""
        entry_id = self._entry_id()
        self.log(
            AuditEntry(
                entry_id=entry_id,
                session_id=session_id,
                timestamp=self._now(),
                tier=tier,
                entry_type=AuditEntryType.TOOL_CALL_BLOCKED,
                tool_name=tool_name,
                tool_parameters=tool_parameters,
                permitted=False,
                block_reason=block_reason,
            )
        )
        return entry_id

    def log_session_start(self, session_id: str, tier: int) -> str:
        """Log a session-start entry.  Returns the ``entry_id``."""
        entry_id = self._entry_id()
        self.log(
            AuditEntry(
                entry_id=entry_id,
                session_id=session_id,
                timestamp=self._now(),
                tier=tier,
                entry_type=AuditEntryType.SESSION_START,
                permitted=True,
            )
        )
        return entry_id

    def log_session_end(self, session_id: str, tier: int) -> str:
        """Log a session-end entry.  Returns the ``entry_id``."""
        entry_id = self._entry_id()
        self.log(
            AuditEntry(
                entry_id=entry_id,
                session_id=session_id,
                timestamp=self._now(),
                tier=tier,
                entry_type=AuditEntryType.SESSION_END,
                permitted=True,
            )
        )
        return entry_id

    def log_workflow_step(
        self,
        session_id: str,
        tier: int,
        entry_type: AuditEntryType,
        tool_name: str,
        *,
        tool_parameters: dict | None = None,
        result: dict | None = None,
        permitted: bool = True,
        block_reason: str | None = None,
        classification: str | None = None,
    ) -> str:
        """Log a terminal Skill workflow-step outcome."""
        entry_id = self._entry_id()
        self.log(
            AuditEntry(
                entry_id=entry_id,
                session_id=session_id,
                timestamp=self._now(),
                tier=tier,
                entry_type=entry_type,
                tool_name=tool_name,
                tool_parameters=tool_parameters,
                result=result,
                permitted=permitted,
                block_reason=block_reason,
            )
        )
        return entry_id

    # -- reading / querying --------------------------------------------------

    def read_all(self) -> list[AuditEntry]:
        """Read every entry from the log file.

        Returns an empty list if the file does not exist.
        """
        if not self._path.is_file():
            return []
        entries: list[AuditEntry] = []
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                d["entry_type"] = AuditEntryType(d["entry_type"])
                entries.append(AuditEntry(**d))
        return entries

    def read_last(self, n: int) -> list[AuditEntry]:
        """Return the last *n* entries (most recent last)."""
        all_entries = self.read_all()
        return all_entries[-n:] if n < len(all_entries) else all_entries

    def read_by_session(self, session_id: str) -> list[AuditEntry]:
        """Return all entries for a given *session_id*."""
        return [e for e in self.read_all() if e.session_id == session_id]
