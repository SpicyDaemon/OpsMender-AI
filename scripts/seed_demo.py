"""Seed a realistic-looking demo database for screenshots.

Idempotent-ish: re-running on an existing DB will fail on unique
constraints, so the screenshot workflow should start from a fresh DB.

Usage:
    rm -f /tmp/opsmender_demo.db
    OPSMENDER_DATABASE_URL="sqlite+aiosqlite:////tmp/opsmender_demo.db" \\
    OPSMENDER_JWT_SECRET="demo-secret-32-chars-long-enough-ok" \\
    uv run python scripts/seed_demo.py

After it runs, start the server with the same DB and screenshot.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("OPSMENDER_DEPLOYMENT_MODE", "development")

import bcrypt
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config_loader import AppConfig
from backend.db.engine import resolve_database_url
from backend.db.models import (
    ApprovalRequest,
    AuditEntry,
    Base,
    BotConnector,
    EscalationChain,
    EscalationStep,
    Incident,
    IncidentMemory,
    IngestToken,
    MaintenanceWindow,
    MCPServer,
    ModelConfig,
    OrgInvite,
    PriorityRule,
    Roster,
    RosterMember,
    Service,
    Session as SessionModel,
    ServiceEscalationChain,
    Skill,
    Team,
    User,
)
from backend.db.repos import OrganizationRepo, UserRepo


def hp(p: str) -> str:
    return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()


async def main():
    config = AppConfig.load()
    url = resolve_database_url(config.db)
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    async with factory() as db:
        # ---- Org ----
        org = (await OrganizationRepo.list_all(db))[0] if await OrganizationRepo.list_all(db) else None
        if org is None:
            org = await OrganizationRepo.create(db, name="Acme Corp", slug="acme")
            await db.commit()
        else:
            # Make the seeded "Main" org feel realistic
            org.name = "Acme Corp"
            org.slug = "acme"
            await db.commit()
        oid = org.id

        # ---- Users ----
        # The admin matches the .env bootstrap credentials so the same login
        # works whether the instance was bootstrapped (empty DB) or demo-seeded,
        # and so the design-audit harness / agents (which read
        # OPSMENDER_BOOTSTRAP_ADMIN_*) can sign in against the seeded demo world.
        _admin_email = os.getenv("OPSMENDER_BOOTSTRAP_ADMIN_EMAIL") or "admin@example.com"
        _admin_pw = os.getenv("OPSMENDER_BOOTSTRAP_ADMIN_PASSWORD") or "AcmeDemo2026!"
        users_seed = [
            ("admin", _admin_email, _admin_pw, "admin"),
            ("john", "john@acme.com", "john123", "operator"),
            ("dmitri", "dmitri@acme.com", "dmitri123", "operator"),
            ("sam", "sam@acme.com", "sam123", "viewer"),
        ]
        users: dict[str, User] = {}
        for username, email, pw, role in users_seed:
            existing = await UserRepo.get_by_username(db, username)
            if existing is None:
                u = await UserRepo.create(
                    db, username=username, email=email,
                    password_hash=hp(pw), role=role, primary_org_id=oid,
                )
                await UserRepo.add_to_organization(db, user_id=u.id, org_id=oid, role=role)
            else:
                u = existing
            users[username] = u
        await db.commit()

        # ---- Teams + Services ----
        teams = {}
        for name, slug, desc in [
            ("Platform", "platform", "Core platform infrastructure."),
            ("Payments", "payments", "Payments + checkout services."),
            ("SRE", "sre", "Site reliability + on-call rotations."),
        ]:
            t = Team(org_id=oid, name=name, slug=slug, description=desc, created_by=users["admin"].id)
            db.add(t)
            teams[slug] = t
        await db.flush()

        services = {}
        for tslug, name, slug, desc in [
            ("platform", "api-gateway", "api-gateway", "Edge API gateway. Routes inbound traffic to internal services."),
            ("platform", "auth-service", "auth-service", "OIDC + session token issuer."),
            ("payments", "checkout-api", "checkout-api", "Stripe-backed checkout orchestrator."),
            ("payments", "payments-db", "payments-db", "Primary Postgres for payment records (RDS)."),
            ("sre", "ingest-pipeline", "ingest-pipeline", "Inbound alert ingestion + dedup."),
        ]:
            s = Service(org_id=oid, team_id=teams[tslug].id, name=name, slug=slug, description=desc, is_active=True)
            db.add(s)
            services[slug] = s
        await db.flush()

        # ---- Rosters + members ----
        rosters = {}
        for tslug, name, tz in [
            ("platform", "Platform on-call", "America/New_York"),
            ("payments", "Payments on-call", "Europe/London"),
            ("sre", "SRE weekly", "UTC"),
        ]:
            r = Roster(
                org_id=oid, team_id=teams[tslug].id, name=name,
                pattern="weekly", pattern_length=7,
                handoff_time="09:00", time_zone=tz,
                anchor_date=(date.today() - timedelta(days=14)),
            )
            db.add(r)
            rosters[tslug] = r
        await db.flush()

        for ridx, (tslug, member_usernames) in enumerate([
            ("platform", ["john", "dmitri"]),
            ("payments", ["dmitri", "john"]),
            ("sre", ["john", "dmitri", "admin"]),
        ]):
            for pos, uname in enumerate(member_usernames):
                db.add(RosterMember(
                    org_id=oid, roster_id=rosters[tslug].id,
                    user_id=users[uname].id, position_index=pos,
                ))

        # ---- Escalation chains ----
        chains = {}
        for tslug, name, desc in [
            ("platform", "Platform default", "Roster → team lead → SRE backup."),
            ("payments", "Payments critical", "Aggressive 2-min cadence for P0."),
        ]:
            c = EscalationChain(
                org_id=oid, team_id=teams[tslug].id, name=name, description=desc, is_active=True,
            )
            db.add(c)
            chains[tslug] = c
        await db.flush()

        for cslug, steps in {
            "platform": [
                ("roster", rosters["platform"].id, 300),
                ("user", users["dmitri"].id, 600),
                ("roster", rosters["sre"].id, 900),
            ],
            "payments": [
                ("roster", rosters["payments"].id, 120),
                ("user", users["admin"].id, 240),
            ],
        }.items():
            for idx, (ttype, tid, timeout) in enumerate(steps):
                db.add(EscalationStep(
                    org_id=oid, chain_id=chains[cslug].id, step_index=idx,
                    target_type=ttype, target_id=tid, timeout_seconds=timeout,
                ))

        # Bind chains to services
        for sslug, cslug in [
            ("api-gateway", "platform"),
            ("auth-service", "platform"),
            ("checkout-api", "payments"),
            ("payments-db", "payments"),
        ]:
            db.add(ServiceEscalationChain(
                org_id=oid, service_id=services[sslug].id, chain_id=chains[cslug].id,
            ))

        # ---- Priority rules ----
        for idx, (name, cond, prio, mode) in enumerate([
            ("Critical payment alerts", {"service": "checkout-api", "severity": "critical"}, "P0", "page"),
            ("Auth outages", {"service": "auth-service", "severity": "critical"}, "P0", "page"),
            ("High severity → page", {"severity": "high"}, "P1", "page"),
            ("Medium → notify", {"severity": "medium"}, "P2", "notify"),
            ("Catch-all → advise", {}, "P3", "auto_resolve"),
        ]):
            db.add(PriorityRule(
                org_id=oid, name=name, rule_index=idx,
                condition=cond, priority=prio, response_mode=mode, is_active=True,
            ))

        # ---- Maintenance window ----
        db.add(MaintenanceWindow(
            org_id=oid,
            name="Payments DB upgrade",
            description="Postgres 15 → 16 patch.",
            starts_at=now + timedelta(days=2),
            ends_at=now + timedelta(days=2, hours=2),
            target_ids=[str(services["payments-db"].id)],
            scope_type="service",
            scope_id=services["payments-db"].id,
            created_by=users["admin"].id,
        ))

        # ---- AI Agent surface ----
        for name, provider, mid, default in [
            ("Claude Sonnet (default)", "anthropic", "claude-sonnet-4-6", True),
            ("GPT-4o", "openai", "gpt-4o", False),
            ("Local Llama", "ollama", "llama3.2", False),
        ]:
            db.add(ModelConfig(
                org_id=oid, name=name, provider=provider, model_id=mid,
                api_key_env_var=f"{provider.upper()}_API_KEY" if provider != "ollama" else None,
                is_default=default, max_tokens=4096, temperature=0.0,
            ))

        mcp_servers = {}
        for name, transport, cmd, args, url_, active in [
            ("kubernetes-prod", "stdio", "uvx", ["mcp-server-kubernetes"], None, True),
            ("postgres-prod", "stdio", "uvx", ["mcp-server-postgres"], None, True),
            ("github-readonly", "http", None, None, "https://api.githubcopilot.com/mcp/", True),
            ("legacy-script", "stdio", "python", ["legacy_mcp.py"], None, False),
        ]:
            m = MCPServer(
                org_id=oid, name=name, transport=transport,
                command=cmd, args=args, url=url_, is_active=active,
                last_successful_call_at=now - timedelta(minutes=2) if active else None,
            )
            db.add(m)
            mcp_servers[name] = m
        await db.flush()

        services["api-gateway"].mcp_server_ids = [str(mcp_servers["kubernetes-prod"].id)]
        services["auth-service"].mcp_server_ids = [str(mcp_servers["kubernetes-prod"].id)]
        services["checkout-api"].mcp_server_ids = [
            str(mcp_servers["kubernetes-prod"].id),
            str(mcp_servers["github-readonly"].id),
        ]
        services["payments-db"].mcp_server_ids = [str(mcp_servers["postgres-prod"].id)]
        services["ingest-pipeline"].mcp_server_ids = [
            str(mcp_servers["kubernetes-prod"].id)
        ]

        for name, mcp_name, desc, body in [
            ("Kubernetes safe ops", "kubernetes-prod", "Read pods, describe deployments, view events.",
             "# Kubernetes Skill\n\n## safe\n- get pods\n- describe deployment\n- get events\n\n## caution\n- restart deployment\n- scale\n\n## destructive\n- delete pod\n- delete deployment\n"),
            ("Postgres read-only", "postgres-prod", "SELECT-only queries against payments-db.",
             "# Postgres Skill\n\n## safe\n- SELECT queries\n- EXPLAIN ANALYZE\n\n## destructive\n- INSERT, UPDATE, DELETE\n- DROP, TRUNCATE\n"),
            ("GitHub PR ops", "github-readonly", "Read PRs, comments, file diffs.",
             "# GitHub Skill\n\n## safe\n- list PRs\n- read comments\n- view diffs\n"),
        ]:
            db.add(Skill(
                org_id=oid, name=name, description=desc,
                mcp_server_id=mcp_servers[mcp_name].id, content_md=body,
            ))

        # ---- Memories ----
        memory_seed = [
            ("Checkout API 503 → restart auth-service", "Symptom: bursts of 503 on /checkout. Root cause: auth-service connection pool exhaustion after stripe-webhook spike. Fix: rolling restart of auth-service.",
             ["payments", "checkout", "503", "auth"], services["checkout-api"].id, 4, 0),
            ("payments-db slow queries during RDS storage autoscale", "Whenever RDS storage autoscale fires, write latency spikes ~3x for ~10 minutes. Trigger maintenance mode preemptively.",
             ["postgres", "rds", "slow-queries"], services["payments-db"].id, 7, 1),
            ("api-gateway 429 cascade from rate-limit misconfig", "A bad terraform deploy lowered the gateway rate-limit to 100rps last week. Look at the most recent terraform apply if 429s cluster after a deploy.",
             ["api-gateway", "rate-limit", "terraform"], services["api-gateway"].id, 2, 0),
            ("Ingest pipeline dedup window too narrow at scale", "Default 30s dedup window misses repeated noisy CloudWatch alarms. Widen to 120s during incidents.",
             ["ingest", "dedup", "cloudwatch"], services["ingest-pipeline"].id, 1, 0),
        ]
        for title, summary, tags, sid, h, u in memory_seed:
            db.add(IncidentMemory(
                org_id=oid, service_id=sid, title=title, summary_md=summary,
                tags=tags, helpful_count=h, unhelpful_count=u,
                created_by_user_id=users["admin"].id,
            ))

        # ---- Integrations ----
        db.add(BotConnector(
            org_id=oid, name="Acme Slack", platform="slack",
            config={"workspace": "acme"}, credentials={"bot_token": "xoxb-redacted"},
            allowed_capabilities=["paging", "interactions", "slash_commands"],
            status="configured", is_enabled=True,
            last_checked_at=now - timedelta(minutes=5),
        ))
        db.add(BotConnector(
            org_id=oid, name="On-call Email", platform="email",
            config={"smtp_host": "smtp.acme.com"}, credentials={},
            allowed_capabilities=["paging"], status="configured", is_enabled=True,
        ))

        # (Legacy WebhookTrigger seeding removed — the model no longer exists;
        # outbound hooks are handled by Notification Channels / integrations.)

        for name, provider, sid in [
            ("Alertmanager prod", "alertmanager", services["api-gateway"].id),
            ("CloudWatch payments", "cloudwatch", services["payments-db"].id),
            ("Azure monitor bridge", "azure_monitor", None),
        ]:
            db.add(IngestToken(
                org_id=oid, name=name, provider=provider,
                token_hash=hashlib.sha256(name.encode()).hexdigest(),
                service_id=sid, is_active=True,
                last_used_at=now - timedelta(minutes=15),
            ))

        # ---- Incidents + sessions + audit ----
        incidents_seed = [
            # (title, status, severity, service, source, created_offset_min)
            ("Checkout API returning 503 (cluster restart)", "in_progress", "critical", "checkout-api", "alertmanager:checkout-5xx", 12),
            ("api-gateway elevated 5xx rate (>2%)", "open", "high", "api-gateway", "alertmanager:gateway-5xx", 4),
            ("payments-db CPU at 92% sustained", "in_progress", "high", "payments-db", "cloudwatch:rds-cpu", 28),
            ("Slow query alert — payments-db", "resolved", "medium", "payments-db", "alertmanager:slow-query", 720),
            ("Ingest backlog growing", "resolved", "medium", "ingest-pipeline", None, 1440),
            ("Sandbox cluster Terraform drift", "closed", "low", None, None, 4320),
        ]
        for title, status_, sev, sslug, ext_src, offset in incidents_seed:
            inc = Incident(
                org_id=oid, title=title,
                description=f"Auto-seeded demo incident. Severity {sev}.",
                status=status_, severity=sev,
                service_id=services[sslug].id if sslug else None,
                external_source=ext_src,
                external_id=f"demo-{sslug}-{int(offset)}" if ext_src else None,
                created_at=now - timedelta(minutes=offset),
                updated_at=now - timedelta(minutes=max(0, offset - 5)),
            )
            db.add(inc)
            await db.flush()

            # Create a session for in-progress + high-priority demo incidents.
            if status_ in ("in_progress", "open") and sev in ("critical", "high"):
                approval_demo = sev == "critical"
                session_tier = 1 if approval_demo else 2
                sess = SessionModel(
                    org_id=oid, incident_id=inc.id, tier=session_tier,
                    model_provider="anthropic", model_id="claude-sonnet-4-6",
                    status="awaiting_approval" if approval_demo else "active",
                    started_at=now - timedelta(minutes=offset - 2),
                )
                db.add(sess)
                await db.flush()

                # Audit entries
                for ai, (tname, tparams, perm) in enumerate([
                    ("kubectl_get_pods", {"namespace": "payments"}, True),
                    ("kubectl_describe_deployment", {"name": "checkout-api"}, True),
                    ("kubectl_logs", {"pod": "checkout-api-7d8f9-x2pq", "tail": 200}, True),
                    ("kubectl_delete_pod", {"pod": "checkout-api-7d8f9-x2pq"}, False),
                ]):
                    routed_to_approval = approval_demo and not perm
                    db.add(AuditEntry(
                        org_id=oid, session_id=sess.id,
                        timestamp=now - timedelta(minutes=offset - 2, seconds=-ai * 12),
                        tier=session_tier, entry_type="tool_call",
                        tool_name=tname, tool_parameters=tparams,
                        result=(
                            {"ok": True, "lines": 42}
                            if perm
                            else {
                                "ok": False,
                                "requires_approval": True,
                                "message": "Routed to operator approval (Tier 1)",
                            }
                            if routed_to_approval
                            else None
                        ),
                        permitted=perm or routed_to_approval,
                        block_reason=None
                        if perm or routed_to_approval
                        else "Operation 'delete pod' classified destructive - Tier 2 cannot execute.",
                        duration_ms=124 + ai * 8,
                    ))

                # Pending approval on the critical incident
                if sev == "critical":
                    db.add(ApprovalRequest(
                        org_id=oid, session_id=sess.id,
                        action={"tool": "kubectl_rollout_restart", "args": {"deployment": "auth-service"}},
                        justification="Auth-service pool exhaustion appears to be the root cause; rolling restart is the standard fix.",
                        status="pending",
                        requested_at=now - timedelta(minutes=1),
                        expires_at=now + timedelta(minutes=14),
                    ))

        # ---- Org invites ----
        db.add(OrgInvite(
            org_id=oid, email="newhire@acme.com", role="operator",
            token_hash=hashlib.sha256(b"invite-pending").hexdigest(),
            invited_by_user_id=users["admin"].id,
            expires_at=now + timedelta(days=6),
        ))
        db.add(OrgInvite(
            org_id=oid, email="contractor@acme.com", role="viewer",
            token_hash=hashlib.sha256(b"invite-accepted").hexdigest(),
            invited_by_user_id=users["admin"].id,
            expires_at=now + timedelta(days=4),
            accepted_at=now - timedelta(hours=2),
            accepted_by_user_id=users["sam"].id,
        ))
        db.add(OrgInvite(
            org_id=oid, email="oldteam@acme.com", role="operator",
            token_hash=hashlib.sha256(b"invite-revoked").hexdigest(),
            invited_by_user_id=users["admin"].id,
            expires_at=now + timedelta(days=3),
            revoked_at=now - timedelta(hours=20),
        ))

        await db.commit()

    await engine.dispose()
    print("[seed] Demo DB seeded:", url)


if __name__ == "__main__":
    asyncio.run(main())
