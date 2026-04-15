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


class ModelConfigResponse(BaseModel):
    id: uuid.UUID
    name: str
    provider: str
    model_id: str
    api_key_env_var: Optional[str]
    base_url: Optional[str]
    api_version: Optional[str]
    max_tokens: int
    temperature: float
    is_default: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ModelConfigListResponse(BaseModel):
    items: list[ModelConfigResponse]
    total: int


class ModelConfigUpdate(BaseModel):
    name: Optional[str] = None
    provider: str = Field(pattern="^(anthropic|openai|azure_openai|ollama)$")
    model_id: str = Field(..., min_length=1, max_length=200)
    api_key_env_var: Optional[str] = Field(default=None, max_length=100)
    base_url: Optional[str] = Field(default=None, max_length=500)
    api_version: Optional[str] = Field(default=None, max_length=50)
    max_tokens: int = Field(default=4096, ge=1, le=200000)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)


class MCPServerResponse(BaseModel):
    id: uuid.UUID
    name: str
    transport: str
    command: Optional[str]
    args: Optional[list[str]]
    url: Optional[str]
    env_vars: Optional[dict[str, str]]
    is_active: bool
    created_at: datetime
    has_token: bool


class MCPServerListResponse(BaseModel):
    items: list[MCPServerResponse]
    total: int


class MCPServerUpsert(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    transport: str = Field(pattern="^(stdio|sse|http)$")
    command: Optional[str] = Field(default=None, max_length=500)
    args: Optional[list[str]] = None
    url: Optional[str] = Field(default=None, max_length=1000)
    token: Optional[str] = None
    clear_token: bool = False
    env_vars: Optional[dict[str, str]] = None
    is_active: bool = True


class MCPServerTestResponse(BaseModel):
    success: bool
    detail: str
    tool_count: int = 0
    tool_names: list[str] = Field(default_factory=list)


class ProviderModelsResponse(BaseModel):
    provider: str
    label: str
    default_model_id: str
    default_api_key_env_var: Optional[str]
    requires_api_key: bool
    requires_base_url: bool
    requires_api_version: bool
    available: bool
    models: list[str]
    error: Optional[str]


class ProviderModelsListResponse(BaseModel):
    items: list[ProviderModelsResponse]
    total: int


# ---------------------------------------------------------------------------
# WebSocket messages
# ---------------------------------------------------------------------------

class WSMessage(BaseModel):
    """Outbound WebSocket message."""
    type: str  # node_transition | tool_call | approval_requested | approval_resolved | error | session_end
    data: dict[str, Any] = Field(default_factory=dict)
