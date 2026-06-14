"""Skill management endpoints."""

from __future__ import annotations

import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from fastapi import Form
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import get_current_org, get_current_user, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    SkillCloneRequest,
    SkillCreate,
    SkillDiscoverRequest,
    SkillDiscoverResponse,
    SkillDiscoveredTool,
    SkillGenerateRequest,
    SkillGenerateResponse,
    SkillListResponse,
    SkillResponse,
    SkillTemplateResponse,
    SkillUpdate,
)
from backend.config_loader import MCPServerConfig
from backend.db.models import Skill, User
from backend.db.repos import MCPServerRepo, SkillRepo
from backend.mcp.client import connect, list_tools
from backend.skills.parser import loads as parse_skill
from backend.skills.suggest import suggest_classification
from backend.skills.template import (
    DEFAULT_TEMPLATE_NAME,
    build_skill_from_tools,
    build_skill_template,
)

router = APIRouter(prefix="/skills", tags=["skills"])


def _extract_focus_areas(content_md: str) -> list[str]:
    """Best-effort parse of focus_areas from a Skill's YAML frontmatter."""

    if not content_md:
        return []
    try:
        from backend.skills.parser import loads as _parse

        return list(_parse(content_md, fmt="md").focus_areas or [])
    except Exception:  # noqa: BLE001
        return []


def _to_response(skill: Skill) -> SkillResponse:
    return SkillResponse(
        id=skill.id,
        name=skill.name,
        description=skill.description,
        mcp_server_id=skill.mcp_server_id,
        assignment=getattr(skill, "assignment", "global") or "global",
        content_md=skill.content_md,
        focus_areas=_extract_focus_areas(skill.content_md),
        created_at=skill.created_at,
        updated_at=skill.updated_at,
    )


async def _validate_content(content_md: str) -> None:
    try:
        parse_skill(content_md)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Skill content could not be parsed: {exc}",
        )


async def _validate_mcp_server(
    db: AsyncSession, org_id: uuid.UUID, mcp_server_id: uuid.UUID | None
) -> None:
    if mcp_server_id is None:
        return
    server = await MCPServerRepo.get_by_id(db, org_id, mcp_server_id)
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"MCP server {mcp_server_id} not found",
        )


@router.get(
    "",
    response_model=SkillListResponse,
    summary="List saved skills",
)
async def list_skills(
    mcp_server_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    if mcp_server_id is not None:
        items = await SkillRepo.list_for_mcp_server(db, org_id, mcp_server_id)
    else:
        items = await SkillRepo.list_all(db, org_id)
    return SkillListResponse(
        items=[_to_response(item) for item in items],
        total=len(items),
    )


@router.get(
    "/template",
    response_model=SkillTemplateResponse,
    summary="Get a fresh 3-tier MCP Skill template (New from Template)",
)
async def get_skill_template(
    user: User = Depends(require_role("admin")),
):
    """Return a structured 3-tier skill policy template (Tier 0 / 1 / 2).

    The caller loads it into the MCP Skill Studio editor, edits, and saves it
    (as Unassigned by default). Not persisted by this call.
    """
    return SkillTemplateResponse(
        name=DEFAULT_TEMPLATE_NAME,
        content_md=build_skill_template(),
    )


@router.post(
    "/discover",
    response_model=SkillDiscoverResponse,
    summary="Discover an MCP server's tools with classification suggestions",
)
async def discover_skill_tools(
    body: SkillDiscoverRequest,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    """Connect to a saved MCP server, list its tools, and suggest a starting
    classification for each (Skill Studio generator step 1–4).

    Suggestions are heuristic and conservative — the operator reviews/overrides
    them, and the backend tier gate remains the execution authority.
    """
    server = await MCPServerRepo.get_by_id(db, org_id, body.mcp_server_id)
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
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not list tools from MCP server: {exc}",
        )

    discovered: list[SkillDiscoveredTool] = []
    for tool in tools:
        suggestion = suggest_classification(tool.name)
        discovered.append(
            SkillDiscoveredTool(
                name=tool.name,
                description=getattr(tool, "description", None),
                suggested_classification=suggestion.classification,
                generic=suggestion.generic,
                suggested_deny=suggestion.deny,
                needs_review=suggestion.needs_review,
                rationale=suggestion.rationale,
            )
        )

    return SkillDiscoverResponse(
        mcp_server_id=server.id,
        mcp_server_name=server.name,
        tools=discovered,
    )


@router.post(
    "/generate",
    response_model=SkillGenerateResponse,
    summary="Generate (but do not save) an MCP Skill draft from classified tools",
)
async def generate_skill(
    body: SkillGenerateRequest,
    user: User = Depends(require_role("admin")),
):
    """Deterministically build a 3-tier MCP Skill Markdown from the operator's
    reviewed tool classifications (Skill Studio generator step 7).

    The result is not persisted — the caller loads it into the editor to review,
    edit, then save (Unassigned by default) or download. The generated YAML
    front-matter is validated by the same parser the tier gate uses.
    """
    content_md = build_skill_from_tools(
        name=body.name,
        environment=body.environment,
        description=body.description or "",
        operations=[op.model_dump() for op in body.operations],
        tier0_instructions=body.tier0_instructions,
        tier1_instructions=body.tier1_instructions,
        tier2_instructions=body.tier2_instructions,
    )
    # Fail-closed: never hand back a draft the tier gate can't parse.
    try:
        parse_skill(content_md)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Generated skill could not be parsed: {exc}",
        )
    return SkillGenerateResponse(name=body.name, content_md=content_md)


@router.get(
    "/{skill_id}",
    response_model=SkillResponse,
    summary="Get a saved skill",
)
async def get_skill(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    skill = await SkillRepo.get_by_id(db, org_id, skill_id)
    if skill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Skill not found",
        )
    return _to_response(skill)


@router.get(
    "/{skill_id}/download",
    summary="Download a skill as a Markdown file (any assignment, incl. unassigned)",
)
async def download_skill(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(get_current_user),
):
    skill = await SkillRepo.get_by_id(db, org_id, skill_id)
    if skill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Skill not found",
        )
    safe_name = "".join(
        c if c.isalnum() or c in ("-", "_") else "-" for c in skill.name
    ).strip("-") or "skill"
    return Response(
        content=skill.content_md,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.md"'
        },
    )


@router.post(
    "",
    response_model=SkillResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a skill",
)
async def create_skill(
    body: SkillCreate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    await _validate_content(body.content_md)
    await _validate_mcp_server(db, org_id, body.mcp_server_id)
    try:
        skill = await SkillRepo.create(
            db,
            org_id,
            name=body.name,
            content_md=body.content_md,
            description=body.description,
            mcp_server_id=body.mcp_server_id,
            assignment=body.assignment,
        )
        await db.commit()
        await db.refresh(skill)
        return _to_response(skill)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Skill name already exists",
        ) from exc


@router.put(
    "/{skill_id}",
    response_model=SkillResponse,
    summary="Update a saved skill",
)
async def update_skill(
    skill_id: uuid.UUID,
    body: SkillUpdate,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    existing = await SkillRepo.get_by_id(db, org_id, skill_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Skill not found",
        )

    await _validate_content(body.content_md)
    await _validate_mcp_server(db, org_id, body.mcp_server_id)
    try:
        updated = await SkillRepo.update(
            db,
            org_id,
            skill_id,
            name=body.name,
            content_md=body.content_md,
            description=body.description,
            mcp_server_id=body.mcp_server_id,
            assignment=body.assignment,
        )
        await db.commit()
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Skill not found",
            )
        await db.refresh(updated)
        return _to_response(updated)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Skill name already exists",
        ) from exc


@router.delete(
    "/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a saved skill",
)
async def delete_skill(
    skill_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    deleted = await SkillRepo.delete(db, org_id, skill_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Skill not found",
        )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{skill_id}/clone",
    response_model=SkillResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Clone a skill (optionally binding it to another MCP server)",
)
async def clone_skill(
    skill_id: uuid.UUID,
    body: SkillCloneRequest,
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    source = await SkillRepo.get_by_id(db, org_id, skill_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Skill not found",
        )

    await _validate_mcp_server(db, org_id, body.mcp_server_id)
    description = body.description or f"Cloned from {source.name}"
    try:
        clone = await SkillRepo.create(
            db,
            org_id,
            name=body.name,
            content_md=source.content_md,
            description=description,
            mcp_server_id=body.mcp_server_id,
            assignment=body.assignment,
        )
        await db.commit()
        await db.refresh(clone)
        return _to_response(clone)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Skill name already exists",
        ) from exc


@router.post(
    "/import",
    response_model=SkillResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Import a .md skill file",
)
async def import_skill(
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    description: str | None = Form(default=None),
    mcp_server_id: uuid.UUID | None = Form(default=None),
    assignment: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    org_id: uuid.UUID = Depends(get_current_org),
    user: User = Depends(require_role("admin")),
):
    raw_bytes = await file.read()
    try:
        content_md = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Skill file must be UTF-8: {exc}",
        )
    if not content_md.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded skill file is empty",
        )

    await _validate_content(content_md)
    await _validate_mcp_server(db, org_id, mcp_server_id)

    if not name:
        filename = file.filename or "imported-skill.md"
        stem = filename.rsplit(".", 1)[0] or filename
        name = stem

    try:
        skill = await SkillRepo.create(
            db,
            org_id,
            name=name,
            content_md=content_md,
            description=description or f"Imported from {file.filename or 'upload'}",
            mcp_server_id=mcp_server_id,
            assignment=assignment,
        )
        await db.commit()
        await db.refresh(skill)
        return _to_response(skill)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Skill name already exists",
        ) from exc
