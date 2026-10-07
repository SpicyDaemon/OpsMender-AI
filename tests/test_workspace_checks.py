"""M1-35 (R-02, O-11): organization, invite and user routes work only on the
caller's own workspace. With a second Organization row present, an admin of
one gets 404 for the other's routes and people, with valid bodies, and nothing
changes there; the product never creates that second row."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from backend.api.auth import hash_password
from backend.db.models import (
    OrgEmailSettings,
    OrgInvite,
    OrgSAMLConfig,
    OrgSSOConfig,
    Organization,
    OrganizationDomain,
    PasswordResetToken,
    User,
)
from backend.db.repos import (
    OrganizationDomainRepo,
    OrganizationRepo,
    OrgEmailSettingsRepo,
    OrgInviteRepo,
    OrgSAMLConfigRepo,
    OrgSSOConfigRepo,
    UserRepo,
)
from tests.test_api_tokens import (
    TEST_ORG_ID,
    admin_headers as _admin_headers_fixture,
    app as _app_fixture,
    client as _client_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
admin_headers = pytest.fixture(_admin_headers_fixture.__wrapped__)

ORG_ROUTES = [
    ("GET", "/organizations/{org}", None),
    ("PUT", "/organizations/{org}", {"name": "Taken over"}),
    ("GET", "/organizations/{org}/notification-settings", None),
    (
        "PUT",
        "/organizations/{org}/notification-settings",
        {"notification_dedup_window_minutes": 30},
    ),
    ("GET", "/organizations/{org}/email-settings", None),
    (
        "PUT",
        "/organizations/{org}/email-settings",
        {"host": "smtp.other.example.test", "from_address": "ops@other.example.test"},
    ),
    ("DELETE", "/organizations/{org}/email-settings", None),
    ("GET", "/organizations/{org}/domains", None),
    ("POST", "/organizations/{org}/domains", {"domain": "taken.example.test"}),
    ("POST", "/organizations/{org}/domains/{domain}/set-primary", None),
    ("DELETE", "/organizations/{org}/domains/{domain}", None),
    ("GET", "/organizations/{org}/sso", None),
    (
        "PUT",
        "/organizations/{org}/sso",
        {
            "discovery_url": "https://idp.other.example.test/.well-known/openid-configuration",
            "client_id": "taken",
            "client_secret": "taken-secret",
        },
    ),
    ("DELETE", "/organizations/{org}/sso", None),
    ("GET", "/organizations/{org}/saml", None),
    (
        "PUT",
        "/organizations/{org}/saml",
        {"idp_metadata_url": "https://idp.other.example.test/metadata"},
    ),
    ("DELETE", "/organizations/{org}/saml", None),
    ("GET", "/organizations/{org}/invites", None),
    (
        "POST",
        "/organizations/{org}/invites",
        {"email": "new@other.example.test", "role": "admin"},
    ),
    ("POST", "/organizations/{org}/invites/{invite}/resend", None),
    ("POST", "/organizations/{org}/invites/{invite}/revoke", None),
]

USER_ROUTES = [
    ("GET", "/auth/users/{user}", None),
    ("PATCH", "/auth/users/{user}", {"role": "admin", "is_active": True}),
    ("GET", "/auth/users/{user}/delete-preconditions", None),
    ("POST", "/auth/users/{user}/soft-delete", None),
    ("POST", "/auth/users/{user}/reset-password", None),
    ("POST", "/auth/users/{user}/set-temporary-password", None),
]


async def _other_workspace(app) -> dict[str, uuid.UUID]:
    """A second Organization row with a member and its own settings. The
    member is inactive and on no Roster, so every user route would succeed
    on them without the workspace check."""
    async with app.state.session_factory() as db:
        org = Organization(name="Other", slug=f"other-{uuid.uuid4().hex[:6]}")
        db.add(org)
        await db.flush()
        member = await UserRepo.create(
            db,
            username="other-member",
            email="member@other.example.test",
            password_hash=hash_password("other-member-pass"),
            role="operator",
            primary_org_id=org.id,
        )
        member.is_active = False
        await UserRepo.add_to_organization(
            db, user_id=member.id, org_id=org.id, role="operator"
        )
        domain = await OrganizationDomainRepo.create(
            db, org_id=org.id, domain="other.example.test", is_primary=False
        )
        await OrgEmailSettingsRepo.upsert(
            db,
            org.id,
            host="smtp.other-own.example.test",
            port=587,
            security="starttls",
            username=None,
            password_encrypted=None,
            from_name="Other",
            from_address="ops@other-own.example.test",
        )
        await OrgSSOConfigRepo.upsert(
            db,
            org_id=org.id,
            provider="oidc",
            discovery_url="https://idp.other-own.example.test/.well-known/openid-configuration",
            client_id="own",
            client_secret_encrypted="own-secret",
        )
        await OrgSAMLConfigRepo.upsert(
            db, org_id=org.id, idp_metadata_url="https://idp.other-own.example.test/m"
        )
        invite = await OrgInviteRepo.create(
            db,
            org_id=org.id,
            email="pending@other.example.test",
            role="operator",
            token_hash=uuid.uuid4().hex,
            expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            invited_by_user_id=None,
        )
        await db.commit()
        return {
            "org": org.id,
            "user": member.id,
            "domain": domain.id,
            "invite": invite.id,
        }


async def _snapshot(app, ids) -> tuple:
    """Everything a route could change in the other workspace."""
    async with app.state.session_factory() as db:

        async def rows(model, *columns):
            result = await db.execute(
                select(*columns).where(model.org_id == ids["org"]).order_by(*columns)
            )
            return [tuple(row) for row in result]

        org = await db.get(Organization, ids["org"], populate_existing=True)
        member = await db.get(User, ids["user"], populate_existing=True)
        resets = await db.scalar(
            select(func.count())
            .select_from(PasswordResetToken)
            .where(PasswordResetToken.user_id == ids["user"])
        )
        return (
            (org.name, org.slug, org.branding, org.notification_dedup_window_minutes),
            await rows(OrgEmailSettings, OrgEmailSettings.host),
            await rows(
                OrganizationDomain,
                OrganizationDomain.domain,
                OrganizationDomain.is_primary,
            ),
            await rows(
                OrgSSOConfig, OrgSSOConfig.discovery_url, OrgSSOConfig.client_id
            ),
            await rows(OrgSAMLConfig, OrgSAMLConfig.idp_metadata_url),
            await rows(
                OrgInvite, OrgInvite.email, OrgInvite.token_hash, OrgInvite.revoked_at
            ),
            (
                member.role,
                member.is_active,
                member.deleted_at,
                member.password_hash,
                member.must_change_password,
            ),
            resets,
        )


@pytest.mark.parametrize(
    ("method", "path", "body"),
    ORG_ROUTES + USER_ROUTES,
    ids=[f"{m} {p}" for m, p, _ in ORG_ROUTES + USER_ROUTES],
)
async def test_another_workspace_is_not_found_and_unchanged(
    app, client: AsyncClient, admin_headers, method, path, body
):
    ids = await _other_workspace(app)
    before = await _snapshot(app, ids)
    url = path.format(
        org=ids["org"], user=ids["user"], domain=ids["domain"], invite=ids["invite"]
    )

    resp = await client.request(method, url, json=body, headers=admin_headers)

    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] in ("Organization not found", "User not found")
    assert await _snapshot(app, ids) == before


async def test_the_people_list_shows_only_your_workspace(
    app, client: AsyncClient, admin_headers
):
    ids = await _other_workspace(app)

    resp = await client.get("/auth/users", headers=admin_headers)

    assert resp.status_code == 200, resp.text
    listed = {row["id"] for row in resp.json()["items"]}
    assert str(ids["user"]) not in listed
    assert {row["username"] for row in resp.json()["items"]} == {"api-admin"}


async def test_your_own_workspace_still_works(app, client: AsyncClient, admin_headers):
    me = (await client.get("/auth/me", headers=admin_headers)).json()
    org_id = TEST_ORG_ID

    org = await client.get(f"/organizations/{org_id}", headers=admin_headers)
    invites = await client.get(
        f"/organizations/{org_id}/invites", headers=admin_headers
    )
    person = await client.get(f"/auth/users/{me['id']}", headers=admin_headers)

    assert (org.status_code, invites.status_code, person.status_code) == (200, 200, 200)


async def test_a_second_workspace_is_never_created(app):
    async with app.state.session_factory() as db:
        with pytest.raises(ValueError, match="one workspace per instance"):
            await OrganizationRepo.create(db, name="Second")
        count = await db.scalar(select(func.count()).select_from(Organization))
    assert count == 1
