"""Sprint 56 Step 4 — invite routes.

Two routers exported from this module:

- ``admin_router``: mounted under ``/organizations`` so the admin
  endpoints land at ``/organizations/{org_id}/invites/...``. Requires
  admin role.
- ``public_router``: mounted under ``/invites``. Public — the recipient
  doesn't have a session when they click the URL.

Invite tokens follow the same opaque-token pattern as password resets
(see ``backend/people/tokens.py``): mint a 256-bit URL-safe token,
return the raw value to the admin exactly once, persist only the
sha256 hash. TTL is 7 days (versus 24 hours for password resets) so
invites survive email delays.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import create_access_token, hash_password, require_role
from backend.api.deps import get_db
from backend.api.schemas import (
    InviteAcceptRequest,
    InviteCreatedResponse,
    InviteCreateRequest,
    InviteListResponse,
    InvitePublicResponse,
    InviteResponse,
    TokenResponse,
)
from backend.config_loader import AppConfig
from backend.db.models import OrgInvite, User
from backend.db.repos import (
    OrganizationRepo,
    OrgInviteRepo,
    UserRepo,
)
from backend.reports.email import build_email_channel, resolve_email_settings
from backend.people import tokens as people_tokens


admin_router = APIRouter(prefix="/organizations", tags=["invites"])
public_router = APIRouter(prefix="/invites", tags=["invites"])


INVITE_TTL = timedelta(days=7)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_public_base_url(request: Request) -> str:
    """Same precedence as `backend/api/routes/auth.py`: explicit env wins."""

    cfg: AppConfig = request.app.state.config
    explicit = cfg.people.public_base_url
    if explicit:
        return explicit.rstrip("/")
    fwd_proto = request.headers.get("x-forwarded-proto")
    fwd_host = request.headers.get("x-forwarded-host")
    scheme = fwd_proto or request.url.scheme
    host = fwd_host or request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}"


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _derive_status(invite: OrgInvite) -> str:
    """Compute the operator-facing status the UI groups by."""

    if invite.revoked_at is not None:
        return "revoked"
    if invite.accepted_at is not None:
        return "accepted"
    now = datetime.now(timezone.utc)
    if _ensure_aware(invite.expires_at) < now:
        return "expired"
    return "pending"


def _to_response(invite: OrgInvite) -> InviteResponse:
    return InviteResponse(
        id=invite.id,
        org_id=invite.org_id,
        email=invite.email,
        role=invite.role,
        invited_by_user_id=invite.invited_by_user_id,
        expires_at=invite.expires_at,
        accepted_at=invite.accepted_at,
        revoked_at=invite.revoked_at,
        created_at=invite.created_at,
        status=_derive_status(invite),
    )


def _build_invite_url(request: Request, raw_token: str) -> str:
    # The invite-accept page is the Next.js static export served at `/invite`,
    # which reads the token from `?token=` — a path segment like
    # `/invite/<token>` has no statically-generated route and 404s. Keep this in
    # sync with frontend/app/invite/page.tsx.
    base = _resolve_public_base_url(request)
    return f"{base}/invite?token={raw_token}"


def _invite_email_body(*, org_name: str, role: str, url: str) -> str:
    return (
        f"Hi,\n\n"
        f"You've been invited to join {org_name} on OpsMender as a "
        f"{role}. Click the link below within 7 days to accept "
        f"the invite and set up your account:\n\n"
        f"{url}\n\n"
        f"If you didn't expect this invite, you can safely ignore "
        f"this email.\n"
    )


async def _send_invite_email(
    request: Request,
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    to: str,
    org_name: str,
    role: str,
    url: str,
) -> tuple[bool, str | None]:
    settings = await resolve_email_settings(
        db, org_id, config=request.app.state.config
    )
    if settings is None:
        return False, None
    attempt = await build_email_channel(settings).send(
        recipient=to,
        subject=f"You're invited to {org_name} on OpsMender",
        body=_invite_email_body(org_name=org_name, role=role, url=url),
    )
    return attempt.status == "sent", attempt.error


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------


@admin_router.post(
    "/{org_id}/invites",
    response_model=InviteCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin"))],
    summary="Mint a new invite to join the organization (admin only)",
)
async def create_invite(
    org_id: uuid.UUID,
    body: InviteCreateRequest,
    request: Request,
    actor: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    org = await OrganizationRepo.get_by_id(db, org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found"
        )

    # If the email already maps to an active user in this org, refuse:
    # there's nothing to invite them to.
    email_lc = body.email.lower().strip()
    existing = await UserRepo.get_by_email(db, email_lc)
    if (
        existing is not None
        and existing.deleted_at is None
        and await UserRepo.is_member(db, existing.id, org_id)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{body.email} is already a member of this organization.",
        )

    raw, token_hash = people_tokens.mint()
    expires_at = datetime.now(timezone.utc) + INVITE_TTL
    invite = await OrgInviteRepo.create(
        db,
        org_id=org_id,
        email=email_lc,
        role=body.role,
        token_hash=token_hash,
        expires_at=expires_at,
        invited_by_user_id=actor.id,
        first_name=(body.first_name or "").strip() or None,
        last_name=(body.last_name or "").strip() or None,
    )
    await db.commit()

    url = _build_invite_url(request, raw)
    email_sent, email_error = await _send_invite_email(
        request, db, org_id, to=email_lc, org_name=org.name, role=body.role, url=url
    )

    return InviteCreatedResponse(
        invite=_to_response(invite),
        url=url,
        email_sent=email_sent,
        email_error=email_error,
    )


@admin_router.post(
    "/{org_id}/invites/{invite_id}/resend",
    response_model=InviteCreatedResponse,
    dependencies=[Depends(require_role("admin"))],
    summary="Resend a pending invite with a fresh token (admin only)",
)
async def resend_invite(
    org_id: uuid.UUID,
    invite_id: uuid.UUID,
    request: Request,
    actor: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    org = await OrganizationRepo.get_by_id(db, org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found"
        )

    invite = await OrgInviteRepo.get_by_id(db, invite_id)
    if invite is None or invite.org_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found"
        )
    if _derive_status(invite) != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only pending invites can be resent.",
        )

    await OrgInviteRepo.mark_revoked(db, invite_id)

    raw, token_hash = people_tokens.mint()
    expires_at = datetime.now(timezone.utc) + INVITE_TTL
    replacement = await OrgInviteRepo.create(
        db,
        org_id=org_id,
        email=invite.email,
        role=invite.role,
        token_hash=token_hash,
        expires_at=expires_at,
        invited_by_user_id=actor.id,
        first_name=invite.first_name,
        last_name=invite.last_name,
    )
    await db.commit()

    url = _build_invite_url(request, raw)
    email_sent, email_error = await _send_invite_email(
        request,
        db,
        org_id,
        to=invite.email,
        org_name=org.name,
        role=invite.role,
        url=url,
    )
    return InviteCreatedResponse(
        invite=_to_response(replacement),
        url=url,
        email_sent=email_sent,
        email_error=email_error,
    )


@admin_router.get(
    "/{org_id}/invites",
    response_model=InviteListResponse,
    dependencies=[Depends(require_role("admin"))],
    summary="List invites for the organization (admin only)",
)
async def list_invites(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    org = await OrganizationRepo.get_by_id(db, org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found"
        )
    rows = await OrgInviteRepo.list_for_org(db, org_id)
    items = [_to_response(r) for r in rows]
    return InviteListResponse(items=items, total=len(items))


@admin_router.post(
    "/{org_id}/invites/{invite_id}/revoke",
    response_model=InviteResponse,
    dependencies=[Depends(require_role("admin"))],
    summary="Revoke a pending invite (admin only)",
)
async def revoke_invite(
    org_id: uuid.UUID,
    invite_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    invite = await OrgInviteRepo.get_by_id(db, invite_id)
    if invite is None or invite.org_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found"
        )
    if invite.accepted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Invite has already been accepted.",
        )
    if invite.revoked_at is not None:
        # Idempotent — return the existing state.
        return _to_response(invite)
    await OrgInviteRepo.mark_revoked(db, invite_id)
    await db.commit()
    refreshed = await OrgInviteRepo.get_by_id(db, invite_id)
    return _to_response(refreshed)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------


def _consumable(invite: OrgInvite) -> bool:
    if invite.accepted_at is not None or invite.revoked_at is not None:
        return False
    return _ensure_aware(invite.expires_at) >= datetime.now(timezone.utc)


@public_router.get(
    "/{token}",
    response_model=InvitePublicResponse,
    summary="Validate an invite token (public)",
)
async def get_invite(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """Return the invite's safe-to-display fields so the accept page can
    render. Returns 400 for invalid/expired/used tokens — the page
    shows a generic 'this invite is no longer valid' message."""

    invite = await OrgInviteRepo.get_by_hash(db, people_tokens.hash_token(token))
    if invite is None or not _consumable(invite):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invite is invalid or no longer active.",
        )
    org = await OrganizationRepo.get_by_id(db, invite.org_id)
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invite is invalid or no longer active.",
        )
    return InvitePublicResponse(
        email=invite.email,
        role=invite.role,
        org_name=org.name,
        expires_at=invite.expires_at,
        first_name=invite.first_name,
        last_name=invite.last_name,
    )


@public_router.post(
    "/{token}/accept",
    response_model=TokenResponse,
    summary="Accept an invite and create the account (public)",
)
async def accept_invite(
    token: str,
    body: InviteAcceptRequest,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint. Creates the user, binds them to the inviting
    org with the role recorded on the invite, marks the invite
    accepted, and returns a freshly-minted JWT so the recipient can
    proceed directly into the dashboard."""

    invite = await OrgInviteRepo.get_by_hash(db, people_tokens.hash_token(token))
    if invite is None or not _consumable(invite):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invite is invalid or no longer active.",
        )

    # Username conflict check.
    if await UserRepo.get_by_username(db, body.username):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{body.username}' is taken.",
        )
    # Email conflict check — if a user with this email exists and isn't
    # soft-deleted, refuse. Soft-deleted addresses are scrubbed to a
    # sentinel so they don't collide with real emails.
    existing = await UserRepo.get_by_email(db, invite.email)
    if existing is not None and existing.deleted_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A user with this email already exists. Ask the admin to "
                "add you to the org directly."
            ),
        )

    user = await UserRepo.create(
        db,
        username=body.username,
        email=invite.email,
        password_hash=hash_password(body.password),
        role=invite.role,
        primary_org_id=invite.org_id,
        first_name=(body.first_name or invite.first_name or "").strip() or None,
        last_name=(body.last_name or invite.last_name or "").strip() or None,
    )
    await UserRepo.add_to_organization(
        db, user_id=user.id, org_id=invite.org_id, role=invite.role
    )
    await OrgInviteRepo.mark_accepted(
        db, invite.id, accepted_by_user_id=user.id
    )
    await db.commit()

    token_value = create_access_token(user.id, user.role)
    return TokenResponse(access_token=token_value)
