"""Pydantic request/response schemas for the AIM API.

All schemas live in one file to avoid circular imports and make it easy
to see the full API surface at a glance.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=150)
    email: str = Field(..., max_length=255)  # EmailStr needs email-validator
    password: str = Field(..., min_length=8)
    role: str = Field(default="viewer", pattern="^(admin|operator|viewer)$")


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    email: str
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------

class IncidentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str = Field(..., min_length=1)
    severity: Optional[str] = Field(
        default=None, pattern="^(critical|high|medium|low)$"
    )


class IncidentResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    status: str
    severity: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class IncidentListResponse(BaseModel):
    items: list[IncidentResponse]
    total: int


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

class SessionCreate(BaseModel):
    incident_id: Optional[uuid.UUID] = None
    tier: int = Field(..., ge=0, le=3)
    model_provider: Optional[str] = None
    model_id: Optional[str] = None


class SessionResponse(BaseModel):
    id: uuid.UUID
    incident_id: Optional[uuid.UUID]
    tier: int
    model_provider: Optional[str]
    model_id: Optional[str]
    status: str
    summary: Optional[str]
    started_at: datetime
    ended_at: Optional[datetime]

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

class AuditEntryResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    timestamp: datetime
    tier: int
    entry_type: str
    tool_name: Optional[str]
    tool_parameters: Optional[dict[str, Any]]
    result: Optional[dict[str, Any]]
    permitted: bool
    block_reason: Optional[str]
    duration_ms: Optional[int]

    model_config = {"from_attributes": True}


class AuditListResponse(BaseModel):
    items: list[AuditEntryResponse]
    total: int


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------

class ApprovalRequestResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    action: dict[str, Any]
    justification: Optional[str]
    status: str
    requested_at: datetime
    resolved_at: Optional[datetime]
    resolved_by: Optional[uuid.UUID]
    expires_at: datetime

    model_config = {"from_attributes": True}


class ApprovalListResponse(BaseModel):
    items: list[ApprovalRequestResponse]
    total: int


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class ConfigResponse(BaseModel):
    tier: int
    mcp_servers: list[dict[str, Any]]
    audit_output: str
    logging_level: str


class ConfigUpdate(BaseModel):
    tier: Optional[int] = Field(default=None, ge=0, le=3)
    logging_level: Optional[str] = Field(
        default=None, pattern="^(DEBUG|INFO|WARNING|ERROR)$"
    )


# ---------------------------------------------------------------------------
# WebSocket messages
# ---------------------------------------------------------------------------

class WSMessage(BaseModel):
    """Outbound WebSocket message."""
    type: str  # node_transition | tool_call | approval_requested | approval_resolved | error | session_end
    data: dict[str, Any] = Field(default_factory=dict)
