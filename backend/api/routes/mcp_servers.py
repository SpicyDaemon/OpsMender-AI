"""MCP server management endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
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
from backend.db.repos import MCPServerOAuthTokenRepo, MCPServerRepo
from backend.mcp.client import connect, list_tools
from backend.mcp.oauth import (
    ClientRegistration,
    MCPOAuthError,
    build_authorize_url,
    canonical_resource_uri,
    discover_protected_resource_metadata,
    exchange_code,
    fetch_authz_server_metadata,
    generate_pkce_pair,
    register_client_dynamically,
    sign_state,
    verify_redirect_issuer,
    verify_state,
)

router = APIRouter(prefix="/mcp-servers", tags=["mcp-servers"])


def _public_base_url(request: Request) -> str:
    fwd_proto = request.headers.get("x-forwarded-proto")
    fwd_host = request.headers.get("x-forwarded-host")
    scheme = fwd_proto or request.url.scheme
    host = fwd_host or request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}"


def _oauth_redirect_uri(request: Request) -> str:
    return f"{_public_base_url(request)}/mcp-servers/oauth/callback"


def _redirect_with(request: Request, params: dict[str, str]) -> RedirectResponse:
    target = f"{_public_base_url(request)}/dashboard/config?{urlencode(params)}"
    return RedirectResponse(target, status_code=status.HTTP_302_FOUND)


def _scopes_from_server(server: MCPServer) -> list[str]:
    raw = (server.env_vars or {}).get("OPSMENDER_MCP_OAUTH_SCOPES", "")
    return [part for part in raw.replace(",", " ").split() if part]


def _env_client_registration(server: MCPServer) -> ClientRegistration | None:
    env = server.env_vars or {}
    client_id = env.get("OPSMENDER_MCP_OAUTH_CLIENT_ID")
    client_secret = env.get("OPSMENDER_MCP_OAUTH_CLIENT_SECRET")
    if not client_id:
        return None
    return ClientRegistration(client_id=client_id, client_secret=client_secret)


def _expires_at(expires_in: int | None) -> datetime | None:
    if expires_in is None:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=expires_in)


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
    "/oauth/start",
    summary="Begin OAuth authorization for an HTTP MCP server",
)
async def start_mcp_oauth(
    request: Request,
    id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    """Return an authorization URL for the frontend to navigate to."""

    server = await MCPServerRepo.get_by_id(db, org_id, id)
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP server not found",
        )
    if server.transport not in {"http", "sse"} or not server.url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth is only available for URL-based MCP servers.",
        )

    redirect_uri = _oauth_redirect_uri(request)
    try:
        prm = await discover_protected_resource_metadata(server.url)
        metadata = None
        last_error: Exception | None = None
        for issuer in prm.authorization_servers:
            try:
                metadata = await fetch_authz_server_metadata(issuer)
                break
            except MCPOAuthError as exc:
                last_error = exc
        if metadata is None:
            raise MCPOAuthError(
                f"No usable authorization server metadata found: {last_error}"
            )

        client_registration = _env_client_registration(server)
        if client_registration is None:
            client_registration = await register_client_dynamically(
                metadata,
                redirect_uris=[redirect_uri],
            )

        resource = prm.resource or canonical_resource_uri(server.url)
        pkce = generate_pkce_pair()
        state = sign_state(
            server_id=str(server.id),
            issuer=metadata.issuer,
            code_verifier=pkce.code_verifier,
            resource=resource,
            org_id=str(org_id),
            client_id=client_registration.client_id,
            client_secret=client_registration.client_secret,
        )
        authorize_url = build_authorize_url(
            metadata,
            client_id=client_registration.client_id,
            redirect_uri=redirect_uri,
            resource=resource,
            scopes=_scopes_from_server(server),
            state=state,
            code_challenge=pkce.code_challenge,
        )
        return {"authorize_url": authorize_url}
    except MCPOAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get(
    "/oauth/callback",
    summary="OAuth callback for HTTP MCP server authorization",
)
async def mcp_oauth_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Public callback. The signed state JWT is the authentication boundary."""

    error = request.query_params.get("error")
    code = request.query_params.get("code")
    state_raw = request.query_params.get("state")
    received_iss = request.query_params.get("iss")

    if error:
        return _redirect_with(request, {"mcp_oauth": "error", "detail": error})
    if not code or not state_raw:
        return _redirect_with(
            request,
            {"mcp_oauth": "error", "detail": "Missing OAuth code or state."},
        )

    try:
        state_claims = verify_state(state_raw)
        server_id = uuid.UUID(str(state_claims["sub"]))
        org_id = uuid.UUID(str(state_claims["org"]))
        issuer = str(state_claims["asiss"])
        code_verifier = str(state_claims["cv"])
        resource = str(state_claims["res"])
        client_id = str(state_claims["cid"])
        client_secret = state_claims.get("csec")
        verify_redirect_issuer(received_iss, issuer)
    except (KeyError, ValueError, MCPOAuthError) as exc:
        return _redirect_with(
            request,
            {"mcp_oauth": "error", "detail": f"Invalid OAuth callback: {exc}"},
        )

    server = await MCPServerRepo.get_by_id(db, org_id, server_id)
    if server is None:
        return _redirect_with(
            request,
            {"mcp_oauth": "error", "detail": "MCP server no longer exists."},
        )

    try:
        metadata = await fetch_authz_server_metadata(issuer)
        token = await exchange_code(
            metadata,
            code=code,
            code_verifier=code_verifier,
            redirect_uri=_oauth_redirect_uri(request),
            resource=resource,
            client_registration=ClientRegistration(
                client_id=client_id,
                client_secret=(
                    str(client_secret)
                    if isinstance(client_secret, str) and client_secret
                    else None
                ),
            ),
        )
        await MCPServerOAuthTokenRepo.upsert(
            db,
            org_id,
            mcp_server_id=server_id,
            access_token=token.access_token,
            refresh_token=token.refresh_token,
            expires_at=_expires_at(token.expires_in),
            scopes=token.scope,
            issuer=issuer,
            token_type=token.token_type,
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001 — provider failures vary widely
        await db.rollback()
        return _redirect_with(
            request,
            {"mcp_oauth": "error", "detail": f"Code exchange failed: {exc}"},
        )

    return _redirect_with(
        request,
        {
            "mcp_oauth": "ok",
            "server_id": str(server_id),
            "detail": f"OAuth connected for MCP server {server.name}.",
        },
    )


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
