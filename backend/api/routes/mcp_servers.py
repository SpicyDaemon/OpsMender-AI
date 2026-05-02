"""MCP server management endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, get_current_user, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    MCPServerListResponse,
    MCPServerResponse,
    MCPServerTestResponse,
    MCPServerUpsert,
)
from backend.config_loader import MCPServerConfig
from backend.db.models import MCPServer, User
from backend.db.repos import MCPServerRepo
from backend.mcp.client import connect, list_tools

router = APIRouter(prefix="/mcp-servers", tags=["mcp-servers"])


def _to_runtime_config(
    body: MCPServerUpsert,
    *,
    token: str | None,
) -> MCPServerConfig:
    return MCPServerConfig(
        name=body.name,
        transport=body.transport,
        command=body.command,
        args=body.args,
        env=body.env_vars,
        url=body.url,
        token=token,
    )


def _to_response(server: MCPServer) -> MCPServerResponse:
    return MCPServerResponse(
        id=server.id,
        name=server.name,
        transport=server.transport,
        command=server.command,
        args=server.args,
        url=server.url,
        env_vars=server.env_vars,
        is_active=server.is_active,
        created_at=server.created_at,
        has_token=bool(server.token),
    )


def _resolve_token(
    body: MCPServerUpsert, existing: MCPServer | None = None
) -> str | None:
    if body.clear_token:
        return None
    if body.token is None:
        return None if existing is None else existing.token
    if body.token == "":
        return None
    return body.token


@router.get(
    "",
    response_model=MCPServerListResponse,
    summary="List saved MCP servers",
)
async def list_mcp_servers(
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    items = await MCPServerRepo.list_all(db, org_id)
    return MCPServerListResponse(
        items=[_to_response(item) for item in items],
        total=len(items),
    )


@router.post(
    "",
    response_model=MCPServerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a saved MCP server",
)
async def create_mcp_server(
    body: MCPServerUpsert,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    token = _resolve_token(body)
    try:
        _to_runtime_config(body, token=token)
        server = await MCPServerRepo.create(
            db,
            org_id,
            name=body.name,
            transport=body.transport,
            command=body.command,
            args=body.args,
            url=body.url,
            token=token,
            env_vars=body.env_vars,
            is_active=body.is_active,
        )
        await db.commit()
        await db.refresh(server)
        return _to_response(server)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MCP server name already exists",
        ) from exc


@router.put(
    "/{server_id}",
    response_model=MCPServerResponse,
    summary="Update a saved MCP server",
)
async def update_mcp_server(
    server_id: uuid.UUID,
    body: MCPServerUpsert,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    existing = await MCPServerRepo.get_by_id(db, org_id, server_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP server not found",
        )

    token = _resolve_token(body, existing)
    try:
        _to_runtime_config(body, token=token)
        updated = await MCPServerRepo.update(
            db,
            org_id,
            server_id,
            name=body.name,
            transport=body.transport,
            command=body.command,
            args=body.args,
            url=body.url,
            token=token,
            env_vars=body.env_vars,
            is_active=body.is_active,
        )
        await db.commit()
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="MCP server not found",
            )
        await db.refresh(updated)
        return _to_response(updated)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MCP server name already exists",
        ) from exc


@router.delete(
    "/{server_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a saved MCP server",
)
async def delete_mcp_server(
    server_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    deleted = await MCPServerRepo.delete(db, org_id, server_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP server not found",
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{server_id}/test",
    response_model=MCPServerTestResponse,
    summary="Test live connectivity to a saved MCP server",
)
async def test_mcp_server(
    server_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    server = await MCPServerRepo.get_by_id(db, org_id, server_id)
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP server not found",
        )

    runtime_config = MCPServerConfig(
        name=server.name,
        transport=server.transport,
        command=server.command,
        args=server.args,
        env=server.env_vars,
        url=server.url,
        token=server.token,
    )
    try:
        async with connect(runtime_config) as session:
            tools = await list_tools(session)
        tool_names = [tool.name for tool in tools]
        return MCPServerTestResponse(
            success=True,
            detail="Connection successful",
            tool_count=len(tool_names),
            tool_names=tool_names,
        )
    except Exception as exc:
        return MCPServerTestResponse(
            success=False,
            detail=str(exc),
        )
