"""Auth routes — register and login.

POST /auth/register — create a new user
POST /auth/login    — authenticate and receive a JWT
GET  /auth/me       — return the current user profile
"""

from __future__ import annotations

import re
import uuid

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.auth import (
    create_access_token,
    create_mfa_token,
    get_current_user,
    hash_password,
    require_role,
    verify_password,
)
from backend.api.deps import get_db
from backend.api.schemas import (
    LoginRequest,
    LoginResponse,
    MyOrganizationListResponse,
    MyOrganizationResponse,
    PasswordResetConsumeRequest,
    PasswordResetMintResponse,
    RegisterRequest,
    SSOHintRequest,
    SSOHintResponse,
    SoftDeletePreconditions,
    TokenResponse,
    MePasswordChangeRequest,
    MeUpdateRequest,
    TemporaryPasswordResponse,
    UserCreateRequest,
    UserListResponse,
    UserResponse,
    UserUpdateRequest,
)
from backend.config_loader import AppConfig, is_development_environment
from backend.db.models import OrgSAMLConfig, OrgSSOConfig, User
from backend.db.repos import (
    OrganizationDomainRepo,
    OrganizationRepo,
    OrgSAMLConfigRepo,
    OrgSSOConfigRepo,
    PasswordResetTokenRepo,
    UserMFARepo,
    UserRepo,
)
from backend.notifications import CATEGORY_ACCOUNT, emit_notification
from backend.reports.email import build_email_channel, resolve_email_settings
from backend.people import tokens as people_tokens

router = APIRouter(prefix="/auth", tags=["auth"])
_USERNAME_RE = re.compile(r"[^a-z0-9_-]+")


def _is_development_mode() -> bool:
    """Sprint 56: self-registration is gated on deployment mode.

    Same env var the production safety guard uses
    (``backend/config_loader.py::check_production_safety``). Test
    fixtures + the ``scripts/dev_server.py`` launcher both set this to
    ``development`` so existing register-based bootstrap keeps working
    without changes.
    """

    return is_development_environment()


async def _self_registration_open(db: AsyncSession) -> bool:
    """Return True iff ``POST /auth/register`` will accept an anonymous caller.

    Open when:
    - deployment mode is ``development`` (covers tests + local dev), OR
    - no users exist (covers a fresh production install that hasn't
      run the bootstrap-admin env vars).
    """

    if _is_development_mode():
        return True
    existing = await UserRepo.list_all(db, limit=1)
    return not existing


async def _available_username(db: AsyncSession, email: str) -> str:
    local = email.split("@", 1)[0].lower()
    base = (_USERNAME_RE.sub("-", local).strip("-") or "user")[:140]
    candidate = base
    suffix = 2
    while await UserRepo.get_by_username(db, candidate):
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


class RegistrationOpenResponse(BaseModel):
    open: bool


@router.post(
    "/sso-hint",
    response_model=SSOHintResponse,
    summary="Resolve the preferred sign-in method for an email domain",
)
async def sso_hint(
    body: SSOHintRequest,
    db: AsyncSession = Depends(get_db),
):
    email = body.email.strip().lower()
    if "@" not in email:
        return SSOHintResponse(provider="local", label="Password")
    domain = email.rsplit("@", 1)[1]
    match = await OrganizationDomainRepo.find_by_host(db, domain)
    org = (
        await OrganizationRepo.get_by_id(db, match.org_id)
        if match is not None
        else None
    )
    if org is None:
        oidc_rows = (
            (
                await db.execute(
                    select(OrgSSOConfig).where(OrgSSOConfig.is_active.is_(True))
                )
            )
            .scalars()
            .all()
        )
        for config in oidc_rows:
            allowed = {
                item.strip().lower()
                for item in (config.allowed_email_domains or "").split(",")
                if item.strip()
            }
            if domain in allowed:
                org = await OrganizationRepo.get_by_id(db, config.org_id)
                break
    if org is None:
        saml_rows = (
            (
                await db.execute(
                    select(OrgSAMLConfig).where(OrgSAMLConfig.is_active.is_(True))
                )
            )
            .scalars()
            .all()
        )
        for config in saml_rows:
            allowed = {
                item.strip().lower()
                for item in (config.allowed_email_domains or "").split(",")
                if item.strip()
            }
            if domain in allowed:
                org = await OrganizationRepo.get_by_id(db, config.org_id)
                break
    if org is None:
        return SSOHintResponse(provider="local", label="Password")
    oidc = await OrgSSOConfigRepo.get_for_org(db, org.id)
    if oidc is not None and oidc.is_active:
        return SSOHintResponse(
            provider="oidc",
            label=f"Continue with {org.name} SSO",
            login_path=f"/auth/sso/{org.slug}/login",
            org_slug=org.slug,
        )
    saml = await OrgSAMLConfigRepo.get_for_org(db, org.id)
    if saml is not None and saml.is_active:
        return SSOHintResponse(
            provider="saml",
            label=f"Continue with {org.name} SAML",
            login_path=f"/auth/saml/{org.slug}/login",
            org_slug=org.slug,
        )
    return SSOHintResponse(
        provider="local",
        label="Password",
        org_slug=org.slug,
    )


@router.get(
    "/registration-open",
    response_model=RegistrationOpenResponse,
    summary="Whether anonymous /auth/register will succeed",
)
async def registration_open(
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint so the login page can hide the register link
    when self-signup is closed.

    Sprint 56: in production with at least one user present,
    self-registration returns 403 — admins must use the Admin → People
    invite flow instead.
    """

    return RegistrationOpenResponse(open=await _self_registration_open(db))


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    # Sprint 56: self-registration is closed in production once any
    # user exists. The bootstrap-admin env vars + admin invite flow
    # are the supported paths.
    if not await _self_registration_open(db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Self-registration is closed. Ask an admin to send you an "
                "invite from the People page."
            ),
        )

    email = body.email.strip().lower()
    username = body.username.strip() if body.username is not None else ""
    if not username:
        username = await _available_username(db, email)

    # Check for existing username / email
    if await UserRepo.get_by_username(db, username):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{username}' already taken",
        )
    if await UserRepo.get_by_email(db, email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Email '{email}' already registered",
        )

    # First user in the system gets admin role automatically
    existing_users = await UserRepo.list_all(db)
    role = "admin" if len(existing_users) == 0 else body.role

    # Multi-tenancy: Ensure at least one organization exists
    from backend.db.repos import OrganizationRepo
    orgs = await OrganizationRepo.list_all(db)
    if not orgs:
        # Create default organization if none exists
        org = await OrganizationRepo.create(db, name="Main", slug="main")
    else:
        org = orgs[0]

    user = await UserRepo.create(
        db,
        username=username,
        email=email,
        password_hash=hash_password(body.password),
        role=role,
        primary_org_id=org.id,
    )
    
    # Link user to the organization
    await UserRepo.add_to_organization(db, user_id=user.id, org_id=org.id, role=role)
    
    await db.commit()
    await db.refresh(user)
    return user


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Log in and receive a JWT",
)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    # Accept either the username OR the email address in the "username"
    # field. Most users reflexively type their email into a credentials
    # form — failing in that case is a real onboarding cliff for no
    # security benefit (username uniqueness is enforced separately, and
    # the same 401 is returned for any miss either way).
    identifier = (body.username or "").strip()
    user = await UserRepo.get_by_username(db, identifier)
    if user is None and "@" in identifier:
        user = await UserRepo.get_by_email(db, identifier)
        if user is None and identifier != identifier.lower():
            user = await UserRepo.get_by_email(db, identifier.lower())
    # Sprint 56: soft-deleted users have a scrubbed (empty) password_hash;
    # short-circuit before verify_password to avoid the bcrypt empty-hash
    # exception, and to return the same generic 401 to avoid enumeration.
    if (
        user is None
        or user.deleted_at is not None
        or not user.password_hash
        or not verify_password(body.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    mfa = await UserMFARepo.get(db, user.id)
    if user.auth_source == "local" and mfa is not None and mfa.enabled_at is not None:
        return LoginResponse(
            mfa_required=True,
            mfa_token=create_mfa_token(user.id, user.role),
        )

    enrollment_required = False
    if user.auth_source == "local" and user.primary_org_id is not None:
        org = await OrganizationRepo.get_by_id(db, user.primary_org_id)
        enrollment_required = bool(org and org.mfa_required)

    token = create_access_token(user.id, user.role)
    return LoginResponse(
        access_token=token,
        mfa_enrollment_required=enrollment_required,
    )


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user profile",
)
async def me(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    mfa = await UserMFARepo.get(db, user.id)
    org = (
        await OrganizationRepo.get_by_id(db, user.primary_org_id)
        if user.primary_org_id is not None
        else None
    )
    return UserResponse.model_validate(user).model_copy(
        update={
            "mfa_enabled": bool(mfa and mfa.enabled_at),
            "mfa_enrollment_required": bool(
                user.auth_source == "local"
                and org
                and org.mfa_required
                and not (mfa and mfa.enabled_at)
            ),
        }
    )


@router.patch(
    "/me",
    response_model=UserResponse,
    summary="Update the current user's own profile",
)
async def update_me(
    body: MeUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Self-service profile edit — username, email, first/last name, avatar
    color. Username + email uniqueness is enforced."""
    username = body.username.strip() if body.username is not None else None
    email = body.email.lower().strip() if body.email is not None else None

    if username is not None and username != user.username:
        clash = await UserRepo.get_by_username(db, username)
        if clash is not None and clash.id != user.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Username '{username}' is taken.",
            )
    if email is not None and email != user.email:
        clash = await UserRepo.get_by_email(db, email)
        if clash is not None and clash.id != user.id and clash.deleted_at is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Email '{email}' is already in use.",
            )

    updated = await UserRepo.update_fields(
        db,
        user.id,
        username=username,
        email=email,
        first_name=body.first_name,
        last_name=body.last_name,
        avatar_color=body.avatar_color,
    )
    await db.commit()
    return updated


@router.post(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change the current user's own password",
)
async def change_my_password(
    body: MePasswordChangeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Self-service password change — the current password must verify."""
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )
    target = await UserRepo.get_by_id(db, user.id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    target.password_hash = hash_password(body.new_password)
    target.must_change_password = False
    target.password_changed_at = datetime.now(timezone.utc)
    await db.commit()


@router.get(
    "/users",
    response_model=UserListResponse,
    dependencies=[Depends(require_role("admin", "operator"))],
    summary="List all users (admin/operator only)",
)
async def list_users(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    users = await UserRepo.list_all(db, limit=limit, offset=offset)
    return UserListResponse(items=list(users), total=len(users))


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin"))],
    summary="Create a local user directly (admin only) — no invite link needed",
)
async def create_user(
    body: UserCreateRequest,
    actor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin-driven direct user creation. The new user gets a temporary
    password and can log in immediately (and change it later), bound to the
    admin's active organization with the chosen role."""
    username = body.username.strip()
    email = body.email.lower().strip()

    if await UserRepo.get_by_username(db, username):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{username}' is taken.",
        )
    existing = await UserRepo.get_by_email(db, email)
    if existing is not None and existing.deleted_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A user with email '{email}' already exists.",
        )

    user = await UserRepo.create(
        db,
        username=username,
        email=email,
        password_hash=hash_password(body.password),
        role=body.role,
        primary_org_id=actor.primary_org_id,
        first_name=(body.first_name or "").strip() or None,
        last_name=(body.last_name or "").strip() or None,
    )
    # Temporary password: force a change on first login unless opted out.
    user.must_change_password = body.require_password_change
    if not body.is_active:
        await UserRepo.update_fields(db, user.id, is_active=False)
    if actor.primary_org_id is not None:
        await UserRepo.add_to_organization(
            db, user_id=user.id, org_id=actor.primary_org_id, role=body.role
        )
    await db.commit()
    refreshed = await UserRepo.get_by_id(db, user.id)
    return refreshed


# ---------------------------------------------------------------------------
# Sprint 56 — admin People-surface routes
# ---------------------------------------------------------------------------


PASSWORD_RESET_TTL = timedelta(hours=24)


def _resolve_public_base_url(request: Request) -> str:
    """Prefer the explicit `OPSMENDER_PUBLIC_BASE_URL` env when set so
    invite + reset links are correct under any proxy chain. Falls back
    to the request's derived base URL otherwise."""

    cfg: AppConfig = request.app.state.config
    explicit = cfg.people.public_base_url
    if explicit:
        return explicit.rstrip("/")
    fwd_proto = request.headers.get("x-forwarded-proto")
    fwd_host = request.headers.get("x-forwarded-host")
    scheme = fwd_proto or request.url.scheme
    host = fwd_host or request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}"


@router.get(
    "/users/{user_id}",
    response_model=UserResponse,
    dependencies=[Depends(require_role("admin", "operator"))],
    summary="Get a single user (admin/operator only)",
)
async def get_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    target = await UserRepo.get_by_id(db, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    return target


@router.patch(
    "/users/{user_id}",
    response_model=UserResponse,
    dependencies=[Depends(require_role("admin"))],
    summary="Update a user's role or active state (admin only)",
)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    target = await UserRepo.get_by_id(db, user_id)
    if target is None or target.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if (
        body.role is None
        and body.is_active is None
        and body.first_name is None
        and body.last_name is None
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one of: role, is_active, first_name, last_name",
        )
    prev_role = target.role
    updated = await UserRepo.update_fields(
        db,
        user_id,
        role=body.role,
        is_active=body.is_active,
        first_name=body.first_name,
        last_name=body.last_name,
    )
    # Tell the user when an admin changes their access level.
    if (
        body.role is not None
        and body.role != prev_role
        and target.primary_org_id is not None
    ):
        await emit_notification(
            db,
            target.primary_org_id,
            user_id,
            event_type="account.role_changed",
            category=CATEGORY_ACCOUNT,
            title=f"Your role is now “{body.role}”",
            body="An administrator updated your access level.",
            link="/dashboard",
        )
    # Deactivation removes the user from on-call rosters: they stop paging and
    # no longer block deletion with stale roster references (Part 5).
    if body.is_active is False:
        from backend.db.repos import RosterRepo

        await RosterRepo.remove_user_everywhere(db, user_id)
    await db.commit()
    return updated


@router.get(
    "/users/{user_id}/delete-preconditions",
    response_model=SoftDeletePreconditions,
    dependencies=[Depends(require_role("admin"))],
    summary="Check whether a user can be soft-deleted",
)
async def get_delete_preconditions(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    target = await UserRepo.get_by_id(db, user_id)
    if target is None or target.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    rosters = await UserRepo.count_roster_memberships(db, user_id)
    return SoftDeletePreconditions(
        is_active=target.is_active,
        roster_memberships=rosters,
        can_delete=(not target.is_active) and rosters == 0,
    )


@router.post(
    "/users/{user_id}/soft-delete",
    response_model=UserResponse,
    dependencies=[Depends(require_role("admin"))],
    summary="Soft-delete a user (admin only)",
)
async def soft_delete_user(
    user_id: uuid.UUID,
    actor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    target = await UserRepo.get_by_id(db, user_id)
    if target is None or target.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if target.id == actor.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account.",
        )
    if target.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Deactivate the user first (set is_active=false).",
        )
    rosters = await UserRepo.count_roster_memberships(db, user_id)
    if rosters > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Remove the user from {rosters} roster"
                f"{'s' if rosters != 1 else ''} before deletion."
            ),
        )
    updated = await UserRepo.soft_delete(db, user_id)
    await db.commit()
    return updated


@router.post(
    "/users/{user_id}/reset-password",
    response_model=PasswordResetMintResponse,
    dependencies=[Depends(require_role("admin"))],
    summary="Mint a one-time password reset URL (admin only)",
)
async def mint_password_reset(
    user_id: uuid.UUID,
    request: Request,
    actor: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    target = await UserRepo.get_by_id(db, user_id)
    if target is None or target.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    raw, token_hash = people_tokens.mint()
    expires_at = datetime.now(timezone.utc) + PASSWORD_RESET_TTL
    await PasswordResetTokenRepo.create(
        db,
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
        issued_by_user_id=actor.id,
    )
    await db.commit()

    base = _resolve_public_base_url(request)
    # Query-param form so the Next.js static export serves it at `/password-reset`
    # (a `/password-reset/<token>` path has no generated route and would 404).
    # Keep in sync with frontend/app/password-reset/page.tsx.
    url = f"{base}/password-reset?token={raw}"

    # Best-effort SMTP delivery alongside the copy-paste URL.
    email_sent = False
    email_error: str | None = None
    email_org_id = target.primary_org_id or actor.primary_org_id
    settings = (
        await resolve_email_settings(
            db, email_org_id, config=request.app.state.config
        )
        if email_org_id is not None
        else None
    )
    if settings is not None:
        body = (
            f"Hi {target.username},\n\n"
            f"An OpsMender administrator initiated a password reset for your "
            f"account. Click the link below within 24 hours to set a new "
            f"password:\n\n"
            f"{url}\n\n"
            f"If you didn't expect this, ignore this email — your existing "
            f"password remains active until the token is used.\n"
        )
        attempt = await build_email_channel(settings).send(
            recipient=target.email,
            subject="OpsMender password reset",
            body=body,
        )
        email_sent = attempt.status == "sent"
        email_error = attempt.error

    return PasswordResetMintResponse(
        url=url,
        expires_at=expires_at,
        email_sent=email_sent,
        email_error=email_error,
    )


@router.post(
    "/users/{user_id}/set-temporary-password",
    response_model=TemporaryPasswordResponse,
    dependencies=[Depends(require_role("admin"))],
    summary="Set a one-time temporary password (admin only) — Option B reset",
)
async def set_temporary_password(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Manual (no-email) password reset: generate a temporary password, force a
    change on next login, and return the password once for the admin to relay."""
    import secrets

    target = await UserRepo.get_by_id(db, user_id)
    if target is None or target.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    temp = secrets.token_urlsafe(9)
    target.password_hash = hash_password(temp)
    target.must_change_password = True
    target.password_changed_at = datetime.now(timezone.utc)
    await db.commit()
    return TemporaryPasswordResponse(
        user_id=user_id, temporary_password=temp, must_change_password=True
    )


@router.post(
    "/password-reset/{token}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Consume a password reset token (public)",
)
async def consume_password_reset(
    token: str,
    body: PasswordResetConsumeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint — the recipient of the one-time URL POSTs their
    new password here. The token is consumed on first successful use.

    Note: this route intentionally does not return user information
    (no enumeration) and is rate-limit-friendly (token lookup is by
    sha256 hash, constant work)."""

    token_hash = people_tokens.hash_token(token)
    row = await PasswordResetTokenRepo.get_by_hash(db, token_hash)
    now = datetime.now(timezone.utc)
    if (
        row is None
        or row.used_at is not None
        or _ensure_aware(row.expires_at) < now
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired token.",
        )

    user = await UserRepo.get_by_id(db, row.user_id)
    if user is None or user.deleted_at is not None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account is unavailable.",
        )

    user.password_hash = hash_password(body.password)
    user.must_change_password = False
    user.password_changed_at = datetime.now(timezone.utc)
    await PasswordResetTokenRepo.mark_used(db, row.id)
    await db.commit()


def _ensure_aware(dt: datetime) -> datetime:
    """SQLite drops tzinfo on persisted aware datetimes. Treat naive
    values as UTC so comparisons against ``datetime.now(timezone.utc)``
    are correct."""

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@router.get(
    "/me/organizations",
    response_model=MyOrganizationListResponse,
    summary="List organizations the current user belongs to",
)
async def my_organizations(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await UserRepo.list_organizations(db, user.id)
    items = [
        MyOrganizationResponse(
            id=r["id"],
            name=r["name"],
            slug=r["slug"],
            branding=r["branding"],
            role=r["role"],
            is_primary=(user.primary_org_id == r["id"]),
        )
        for r in rows
    ]
    return MyOrganizationListResponse(items=items, total=len(items))


@router.put(
    "/me/primary-org/{org_id}",
    response_model=UserResponse,
    summary="Set the current user's primary (active) organization",
)
async def set_my_primary_org(
    org_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not await UserRepo.is_member(db, user.id, org_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not a member of this organization.",
        )
    await UserRepo.set_primary_org(db, user.id, org_id)
    await db.commit()
    await db.refresh(user)
    return user
