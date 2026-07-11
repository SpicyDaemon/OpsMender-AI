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
from backend.skills.template import build_skill_from_tools


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
        org = (
            (await OrganizationRepo.list_all(db))[0]
            if await OrganizationRepo.list_all(db)
            else None
        )
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
        _admin_email = (
            os.getenv("OPSMENDER_BOOTSTRAP_ADMIN_EMAIL") or "admin@example.com"
        )
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
                    db,
                    username=username,
                    email=email,
                    password_hash=hp(pw),
                    role=role,
                    primary_org_id=oid,
                )
                await UserRepo.add_to_organization(
                    db, user_id=u.id, org_id=oid, role=role
                )
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
            t = Team(
                org_id=oid,
                name=name,
                slug=slug,
                description=desc,
                created_by=users["admin"].id,
            )
            db.add(t)
            teams[slug] = t
        await db.flush()

        services = {}
        for tslug, name, slug, desc in [
            (
                "platform",
                "api-gateway",
                "api-gateway",
                "Edge API gateway. Routes inbound traffic to internal services.",
            ),
            (
                "platform",
                "auth-service",
                "auth-service",
                "OIDC + session token issuer.",
            ),
            (
                "payments",
                "checkout-api",
                "checkout-api",
                "Stripe-backed checkout orchestrator.",
            ),
            (
                "payments",
                "payments-db",
                "payments-db",
                "Primary Postgres for payment records (RDS).",
            ),
            (
                "sre",
                "ingest-pipeline",
                "ingest-pipeline",
                "Inbound alert ingestion + dedup.",
            ),
        ]:
            s = Service(
                org_id=oid,
                team_id=teams[tslug].id,
                name=name,
                slug=slug,
                description=desc,
                is_active=True,
            )
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
                org_id=oid,
                team_id=teams[tslug].id,
                name=name,
                pattern="weekly",
                pattern_length=7,
                handoff_time="09:00",
                time_zone=tz,
                anchor_date=(date.today() - timedelta(days=14)),
            )
            db.add(r)
            rosters[tslug] = r
        await db.flush()

        for ridx, (tslug, member_usernames) in enumerate(
            [
                ("platform", ["john", "dmitri"]),
                ("payments", ["dmitri", "john"]),
                ("sre", ["john", "dmitri", "admin"]),
            ]
        ):
            for pos, uname in enumerate(member_usernames):
                db.add(
                    RosterMember(
                        org_id=oid,
                        roster_id=rosters[tslug].id,
                        user_id=users[uname].id,
                        position_index=pos,
                    )
                )

        # ---- Escalation chains ----
        chains = {}
        for tslug, name, desc in [
            ("platform", "Platform default", "Roster → team lead → SRE backup."),
            ("payments", "Payments critical", "Aggressive 2-min cadence for P0."),
        ]:
            c = EscalationChain(
                org_id=oid,
                team_id=teams[tslug].id,
                name=name,
                description=desc,
                is_active=True,
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
                db.add(
                    EscalationStep(
                        org_id=oid,
                        chain_id=chains[cslug].id,
                        step_index=idx,
                        target_type=ttype,
                        target_id=tid,
                        timeout_seconds=timeout,
                    )
                )

        # Bind chains to services
        for sslug, cslug in [
            ("api-gateway", "platform"),
            ("auth-service", "platform"),
            ("checkout-api", "payments"),
            ("payments-db", "payments"),
        ]:
            db.add(
                ServiceEscalationChain(
                    org_id=oid,
                    service_id=services[sslug].id,
                    chain_id=chains[cslug].id,
                )
            )

        # ---- Priority rules ----
        for idx, (name, cond, prio, mode) in enumerate(
            [
                (
                    "Critical payment alerts",
                    {"service": "checkout-api", "severity": "critical"},
                    "P0",
                    "page",
                ),
                (
                    "Auth outages",
                    {"service": "auth-service", "severity": "critical"},
                    "P0",
                    "page",
                ),
                ("High severity → page", {"severity": "high"}, "P1", "page"),
                ("Medium → notify", {"severity": "medium"}, "P2", "notify"),
                ("Catch-all → advise", {}, "P3", "auto_resolve"),
            ]
        ):
            db.add(
                PriorityRule(
                    org_id=oid,
                    name=name,
                    rule_index=idx,
                    condition=cond,
                    priority=prio,
                    response_mode=mode,
                    is_active=True,
                )
            )

        # ---- Maintenance window ----
        db.add(
            MaintenanceWindow(
                org_id=oid,
                name="Payments DB upgrade",
                description="Postgres 15 → 16 patch.",
                starts_at=now + timedelta(days=2),
                ends_at=now + timedelta(days=2, hours=2),
                target_ids=[str(services["payments-db"].id)],
                scope_type="service",
                scope_id=services["payments-db"].id,
                created_by=users["admin"].id,
            )
        )

        # ---- AI Agent surface ----
        for name, provider, mid, default in [
            ("Claude Sonnet (default)", "anthropic", "claude-sonnet-4-6", True),
            ("GPT-4o", "openai", "gpt-4o", False),
            ("Local Llama", "ollama", "llama3.2", False),
        ]:
            db.add(
                ModelConfig(
                    org_id=oid,
                    name=name,
                    provider=provider,
                    model_id=mid,
                    api_key_env_var=f"{provider.upper()}_API_KEY"
                    if provider != "ollama"
                    else None,
                    is_default=default,
                    max_tokens=4096,
                    temperature=0.0,
                )
            )

        mcp_servers = {}
        for name, transport, cmd, args, url_, active in [
            ("kubernetes-prod", "stdio", "uvx", ["mcp-server-kubernetes"], None, True),
            ("postgres-prod", "stdio", "uvx", ["mcp-server-postgres"], None, True),
            (
                "github-readonly",
                "http",
                None,
                None,
                "https://api.githubcopilot.com/mcp/",
                True,
            ),
            ("legacy-script", "stdio", "python", ["legacy_mcp.py"], None, False),
        ]:
            m = MCPServer(
                org_id=oid,
                name=name,
                transport=transport,
                command=cmd,
                args=args,
                url=url_,
                is_active=active,
                last_successful_call_at=now - timedelta(minutes=2) if active else None,
            )
            db.add(m)
            mcp_servers[name] = m
        await db.flush()

        services["api-gateway"].mcp_server_ids = [
            str(mcp_servers["kubernetes-prod"].id)
        ]
        services["auth-service"].mcp_server_ids = [
            str(mcp_servers["kubernetes-prod"].id)
        ]
        services["checkout-api"].mcp_server_ids = [
            str(mcp_servers["kubernetes-prod"].id),
            str(mcp_servers["github-readonly"].id),
        ]
        services["payments-db"].mcp_server_ids = [str(mcp_servers["postgres-prod"].id)]
        services["ingest-pipeline"].mcp_server_ids = [
            str(mcp_servers["kubernetes-prod"].id)
        ]

        for name, mcp_name, desc, body in [
            (
                "Kubernetes safe ops",
                "kubernetes-prod",
                "Read pods, describe deployments, view events.",
                build_skill_from_tools(
                    name="Kubernetes safe ops",
                    environment="production",
                    operations=[
                        {"tool": "get_pods", "classification": "safe"},
                        {"tool": "describe_deployment", "classification": "safe"},
                        {"tool": "get_events", "classification": "safe"},
                        {
                            "tool": "kubectl_rollout_restart",
                            "classification": "caution",
                            "reversible": True,
                            "compensating_inverse": "kubectl_rollout_undo",
                        },
                        {"tool": "scale_deployment", "classification": "caution"},
                        {"tool": "delete_pod", "classification": "destructive"},
                        {
                            "tool": "delete_deployment",
                            "classification": "destructive",
                        },
                    ],
                ),
            ),
            (
                "Postgres read-only",
                "postgres-prod",
                "SELECT-only queries against payments-db.",
                build_skill_from_tools(
                    name="Postgres read-only",
                    environment="production",
                    operations=[
                        {"tool": "select_query", "classification": "safe"},
                        {"tool": "explain_query", "classification": "safe"},
                        {
                            "tool": "write_query",
                            "classification": "destructive",
                            "deny": True,
                        },
                        {
                            "tool": "schema_change",
                            "classification": "destructive",
                            "deny": True,
                        },
                    ],
                ),
            ),
            (
                "GitHub PR ops",
                "github-readonly",
                "Read PRs, comments, file diffs.",
                build_skill_from_tools(
                    name="GitHub PR ops",
                    environment="production",
                    operations=[
                        {"tool": "list_pull_requests", "classification": "safe"},
                        {"tool": "get_pull_request", "classification": "safe"},
                        {"tool": "get_file_diff", "classification": "safe"},
                    ],
                ),
            ),
        ]:
            db.add(
                Skill(
                    org_id=oid,
                    name=name,
                    description=desc,
                    mcp_server_id=mcp_servers[mcp_name].id,
                    content_md=body,
                )
            )

        # ---- Memories ----
        memory_seed = [
            (
                "Checkout API 503 → restart auth-service",
                "Symptom: bursts of 503 on /checkout. Root cause: auth-service connection pool exhaustion after stripe-webhook spike. Fix: rolling restart of auth-service.",
                ["payments", "checkout", "503", "auth"],
                services["checkout-api"].id,
                4,
                0,
            ),
            (
                "payments-db slow queries during RDS storage autoscale",
                "Whenever RDS storage autoscale fires, write latency spikes ~3x for ~10 minutes. Trigger maintenance mode preemptively.",
                ["postgres", "rds", "slow-queries"],
                services["payments-db"].id,
                7,
                1,
            ),
            (
                "api-gateway 429 cascade from rate-limit misconfig",
                "A bad terraform deploy lowered the gateway rate-limit to 100rps last week. Look at the most recent terraform apply if 429s cluster after a deploy.",
                ["api-gateway", "rate-limit", "terraform"],
                services["api-gateway"].id,
                2,
                0,
            ),
            (
                "Ingest pipeline dedup window too narrow at scale",
                "Default 30s dedup window misses repeated noisy CloudWatch alarms. Widen to 120s during incidents.",
                ["ingest", "dedup", "cloudwatch"],
                services["ingest-pipeline"].id,
                1,
                0,
            ),
        ]
        for title, summary, tags, sid, h, u in memory_seed:
            db.add(
                IncidentMemory(
                    org_id=oid,
                    service_id=sid,
                    title=title,
                    summary_md=summary,
                    tags=tags,
                    helpful_count=h,
                    unhelpful_count=u,
                    created_by_user_id=users["admin"].id,
                )
            )

        # ---- Integrations ----
        db.add(
            BotConnector(
                org_id=oid,
                name="Acme Slack",
                platform="slack",
                config={"workspace": "acme"},
                credentials={"bot_token": "xoxb-redacted"},
                allowed_capabilities=["paging", "interactions", "slash_commands"],
                status="configured",
                is_enabled=True,
                last_checked_at=now - timedelta(minutes=5),
            )
        )
        db.add(
            BotConnector(
                org_id=oid,
                name="On-call Email",
                platform="email",
                config={"smtp_host": "smtp.acme.com"},
                credentials={},
                allowed_capabilities=["paging"],
                status="configured",
                is_enabled=True,
            )
        )

        # (Legacy WebhookTrigger seeding removed — the model no longer exists;
        # outbound hooks are handled by Notification Channels / integrations.)

        for name, provider, sid in [
            ("Alertmanager prod", "alertmanager", services["api-gateway"].id),
            ("CloudWatch payments", "cloudwatch", services["payments-db"].id),
            ("Azure monitor bridge", "azure_monitor", None),
        ]:
            db.add(
                IngestToken(
                    org_id=oid,
                    name=name,
                    provider=provider,
                    token_hash=hashlib.sha256(name.encode()).hexdigest(),
                    service_id=sid,
                    is_active=True,
                    last_used_at=now - timedelta(minutes=15),
                )
            )

        # ---- Incidents + sessions + audit ----
        demo_scenarios = [
            {
                "external_id": "demo-tier-0",
                "title": "Checkout API 503 rate recovered after safe rollout",
                "status": "resolved",
                "severity": "critical",
                "service": "checkout-api",
                "source": "alertmanager:checkout-5xx",
                "offset": 42,
                "tier": 0,
                "session_status": "completed",
                "summary": (
                    "Autonomously restarted the unhealthy checkout-api rollout, "
                    "verified all replicas ready, and restored the error rate to baseline."
                ),
                "progress": {
                    "observations": ["Two replicas failed readiness checks."],
                    "diagnosis": "A stale connection pool followed the prior rollout.",
                    "plan": "Restart the deployment, then verify readiness and errors.",
                    "workflow_result": "Service recovered; rollback remains available.",
                },
            },
            {
                "external_id": "demo-tier-1",
                "title": "Auth service saturation requires rollout approval",
                "status": "in_progress",
                "severity": "critical",
                "service": "auth-service",
                "source": "alertmanager:auth-saturation",
                "offset": 18,
                "tier": 1,
                "session_status": "awaiting_approval",
                "summary": (
                    "Diagnosis complete. A reversible rollout restart is ready and "
                    "waiting for operator approval."
                ),
                "progress": {
                    "observations": ["Connection pool utilization is pinned at 99%."],
                    "diagnosis": "One deployment generation is retaining stale sessions.",
                    "plan": "Request approval for a rolling restart; verify saturation.",
                },
            },
            {
                "external_id": "demo-tier-2",
                "title": "Payments database CPU elevated during reconciliation",
                "status": "in_progress",
                "severity": "high",
                "service": "payments-db",
                "source": "cloudwatch:rds-cpu",
                "offset": 9,
                "tier": 2,
                "session_status": "completed",
                "summary": (
                    "Advised only: pause the reconciliation worker, review the top "
                    "query plan, and add the proposed composite index during a change window."
                ),
                "progress": {
                    "observations": [
                        "Reconciliation query consumes 78% of database CPU."
                    ],
                    "diagnosis": "A missing composite index forces repeated full scans.",
                    "plan": "Recommend a worker pause and index review; execute no writes.",
                    "workflow_result": "Recommendations delivered without changing state.",
                },
            },
        ]

        scenario_sessions: dict[int, SessionModel] = {}
        for scenario in demo_scenarios:
            offset = int(scenario["offset"])
            incident = Incident(
                org_id=oid,
                title=str(scenario["title"]),
                description=(
                    f"Deterministic Tier {scenario['tier']} launch scenario with a "
                    "persisted workflow and audit trail."
                ),
                status=str(scenario["status"]),
                severity=str(scenario["severity"]),
                priority="P0" if scenario["severity"] == "critical" else "P1",
                response_mode="page"
                if scenario["severity"] == "critical"
                else "notify",
                service_id=services[str(scenario["service"])].id,
                external_source=str(scenario["source"]),
                external_id=str(scenario["external_id"]),
                created_at=now - timedelta(minutes=offset),
                updated_at=now - timedelta(minutes=max(0, offset - 3)),
                acknowledged_at=now - timedelta(minutes=max(0, offset - 2)),
            )
            db.add(incident)
            await db.flush()

            tier = int(scenario["tier"])
            session = SessionModel(
                org_id=oid,
                incident_id=incident.id,
                tier=tier,
                model_provider="anthropic",
                model_id="claude-sonnet-4-6",
                status=str(scenario["session_status"]),
                summary=str(scenario["summary"]),
                progress=scenario["progress"],
                started_at=now - timedelta(minutes=max(0, offset - 1)),
                ended_at=(
                    now - timedelta(minutes=max(0, offset - 7))
                    if scenario["session_status"] == "completed"
                    else None
                ),
            )
            db.add(session)
            await db.flush()
            scenario_sessions[tier] = session

        def add_scenario_events(
            session: SessionModel,
            tier: int,
            start: datetime,
            events: list[dict],
        ) -> None:
            for index, event in enumerate(events):
                db.add(
                    AuditEntry(
                        org_id=oid,
                        session_id=session.id,
                        timestamp=start + timedelta(seconds=index * 11),
                        tier=tier,
                        entry_type=event["entry_type"],
                        tool_name=event.get("tool_name"),
                        tool_parameters=event.get("tool_parameters"),
                        result=event.get("result"),
                        permitted=event.get("permitted", True),
                        block_reason=event.get("block_reason"),
                        duration_ms=event.get("duration_ms"),
                    )
                )

        def node(name: str, message: str) -> dict:
            return {
                "entry_type": "node_transition",
                "result": {"node": name, "message": message},
            }

        read_pods = {
            "entry_type": "tool_call_end",
            "tool_name": "kubectl_get_pods",
            "tool_parameters": {
                "namespace": "payments",
                "selector": "app=checkout-api",
            },
            "result": {
                "ok": True,
                "summary": "2 of 5 replicas failed readiness checks",
                "classification": "safe",
            },
            "duration_ms": 184,
        }

        add_scenario_events(
            scenario_sessions[0],
            0,
            now - timedelta(minutes=40),
            [
                {
                    "entry_type": "session_start",
                    "result": {"message": "Tier 0 session started"},
                },
                node("observe", "Collected deployment health and recent events"),
                read_pods,
                {
                    "entry_type": "tool_call_end",
                    "tool_name": "kubectl_describe_deployment",
                    "tool_parameters": {
                        "namespace": "payments",
                        "name": "checkout-api",
                    },
                    "result": {
                        "ok": True,
                        "summary": "Unhealthy replicas share rollout revision 184",
                        "classification": "safe",
                    },
                    "duration_ms": 229,
                },
                node("diagnose", "Identified a stale connection pool in revision 184"),
                node("plan", "Use a reversible rolling restart and verify readiness"),
                node(
                    "tier_gate", "Tier 0 policy allowed the reversible caution action"
                ),
                node("execute", "Executing the approved autonomous remediation plan"),
                {
                    "entry_type": "tool_call_end",
                    "tool_name": "kubectl_rollout_restart",
                    "tool_parameters": {
                        "namespace": "payments",
                        "deployment": "checkout-api",
                    },
                    "result": {
                        "ok": True,
                        "message": "Rolling restart completed; 5 of 5 replicas are ready",
                        "classification": "caution",
                        "decision": "autonomous",
                        "compensating_inverse": "kubectl_rollout_undo",
                    },
                    "duration_ms": 824,
                },
                node(
                    "verify",
                    "Readiness is healthy and the 503 rate returned to baseline",
                ),
                node(
                    "summarize", "Recorded the remediation, evidence, and rollback path"
                ),
                {
                    "entry_type": "session_end",
                    "result": {
                        "message": "Incident resolved autonomously with rollback available"
                    },
                },
            ],
        )

        add_scenario_events(
            scenario_sessions[1],
            1,
            now - timedelta(minutes=16),
            [
                {
                    "entry_type": "session_start",
                    "result": {"message": "Tier 1 session started"},
                },
                node("observe", "Collected saturation, pod health, and rollout state"),
                {
                    **read_pods,
                    "tool_parameters": {
                        "namespace": "platform",
                        "selector": "app=auth-service",
                    },
                    "result": {
                        "ok": True,
                        "summary": "Connection saturation isolated to rollout revision 77",
                        "classification": "safe",
                    },
                },
                node(
                    "diagnose", "Confirmed stale sessions in one deployment generation"
                ),
                node("plan", "Propose a reversible rolling restart"),
                node(
                    "tier_gate",
                    "Tier 1 policy requires operator approval for the write",
                ),
                {
                    "entry_type": "tool_call_blocked",
                    "tool_name": "kubectl_rollout_restart",
                    "tool_parameters": {
                        "namespace": "platform",
                        "deployment": "auth-service",
                    },
                    "result": {
                        "ok": False,
                        "requires_approval": True,
                        "message": "Routed to operator approval; no write executed",
                        "classification": "caution",
                    },
                    "permitted": False,
                    "block_reason": "Tier 1 requires operator approval before write execution.",
                    "duration_ms": 12,
                },
                {
                    "entry_type": "approval_requested",
                    "result": {
                        "message": "Waiting for operator approval to restart auth-service"
                    },
                },
            ],
        )
        db.add(
            ApprovalRequest(
                org_id=oid,
                session_id=scenario_sessions[1].id,
                action={
                    "tool": "kubectl_rollout_restart",
                    "args": {"namespace": "platform", "deployment": "auth-service"},
                    "classification": "caution",
                    "reversible": True,
                    "compensating_inverse": "kubectl_rollout_undo",
                },
                justification=(
                    "A rolling restart clears the saturated connection pool and is "
                    "reversible. Tier 1 requires your approval before execution."
                ),
                status="pending",
                requested_at=now - timedelta(minutes=14),
                expires_at=now + timedelta(minutes=16),
            )
        )

        add_scenario_events(
            scenario_sessions[2],
            2,
            now - timedelta(minutes=7),
            [
                {
                    "entry_type": "session_start",
                    "result": {"message": "Tier 2 session started"},
                },
                node("observe", "Collected CPU, active sessions, and query statistics"),
                {
                    "entry_type": "tool_call_end",
                    "tool_name": "postgres_select_query",
                    "tool_parameters": {
                        "query": "SELECT queryid, calls, total_exec_time FROM pg_stat_statements LIMIT 5"
                    },
                    "result": {
                        "ok": True,
                        "summary": "Reconciliation query accounts for 78% of CPU time",
                        "classification": "safe",
                    },
                    "duration_ms": 143,
                },
                {
                    "entry_type": "tool_call_end",
                    "tool_name": "postgres_explain_query",
                    "tool_parameters": {
                        "query": "SELECT id FROM payments WHERE state = $1 ORDER BY updated_at"
                    },
                    "result": {
                        "ok": True,
                        "summary": "Sequential scan confirms the missing composite index",
                        "classification": "safe",
                    },
                    "duration_ms": 96,
                },
                node(
                    "diagnose", "Missing index makes reconciliation scan the full table"
                ),
                node("plan", "Prepare recommendations without executing changes"),
                node(
                    "tier_gate", "Tier 2 policy limited the session to read-only advice"
                ),
                node("summarize", "Delivered a change-window plan and query evidence"),
                {
                    "entry_type": "session_end",
                    "result": {
                        "message": "Advisory report completed; no writes executed"
                    },
                },
            ],
        )

        for title, status_, sev, service_slug, source, external_id, offset in [
            (
                "Slow query alert - payments database",
                "resolved",
                "medium",
                "payments-db",
                "alertmanager:slow-query",
                "demo-history-slow-query",
                720,
            ),
            (
                "Ingest backlog growing",
                "resolved",
                "medium",
                "ingest-pipeline",
                None,
                "demo-history-ingest",
                1440,
            ),
            (
                "Sandbox cluster configuration drift",
                "resolved",
                "low",
                None,
                None,
                "demo-history-drift",
                4320,
            ),
        ]:
            db.add(
                Incident(
                    org_id=oid,
                    title=title,
                    description=f"Historical demo incident. Severity {sev}.",
                    status=status_,
                    severity=sev,
                    service_id=services[service_slug].id if service_slug else None,
                    external_source=source,
                    external_id=external_id,
                    created_at=now - timedelta(minutes=offset),
                    updated_at=now - timedelta(minutes=max(0, offset - 30)),
                )
            )

        # ---- Org invites ----
        db.add(
            OrgInvite(
                org_id=oid,
                email="newhire@acme.com",
                role="operator",
                token_hash=hashlib.sha256(b"invite-pending").hexdigest(),
                invited_by_user_id=users["admin"].id,
                expires_at=now + timedelta(days=6),
            )
        )
        db.add(
            OrgInvite(
                org_id=oid,
                email="contractor@acme.com",
                role="viewer",
                token_hash=hashlib.sha256(b"invite-accepted").hexdigest(),
                invited_by_user_id=users["admin"].id,
                expires_at=now + timedelta(days=4),
                accepted_at=now - timedelta(hours=2),
                accepted_by_user_id=users["sam"].id,
            )
        )
        db.add(
            OrgInvite(
                org_id=oid,
                email="oldteam@acme.com",
                role="operator",
                token_hash=hashlib.sha256(b"invite-revoked").hexdigest(),
                invited_by_user_id=users["admin"].id,
                expires_at=now + timedelta(days=3),
                revoked_at=now - timedelta(hours=20),
            )
        )

        await db.commit()

    await engine.dispose()
    print("[seed] Demo DB seeded:", url)


if __name__ == "__main__":
    asyncio.run(main())
