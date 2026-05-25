"""Combine the 33 individual screenshots into 8 labeled composites.

Each composite covers one sidebar area (or two related ones) and lays
the source images out in a grid with per-panel labels and a banner
header. Panels are not downscaled below 1100 px wide so the UI stays
readable when the composite is viewed full-size.

Usage:
    uv run python scripts/combine_screenshots.py
"""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "screenshots"
OUT = ROOT / "screenshots"

# Look + feel
BG = (10, 12, 16)             # near-black to match the dashboard
PANEL_LABEL_BG = (24, 28, 36)
TITLE_FG = (240, 244, 252)
LABEL_FG = (200, 210, 230)
COUNT_FG = (140, 148, 168)

# Layout
COL_WIDTH = 1200              # each panel scaled to this width (keeps text readable)
PAD_OUTER = 32
PAD_INNER = 24                # gap between panels
HEADER_H = 90
LABEL_H = 48

# Font discovery
_FONT_CANDIDATES = [
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for p in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(p, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def load_scaled(name: str, width: int = COL_WIDTH) -> Image.Image:
    img = Image.open(SRC / name).convert("RGB")
    if img.width == width:
        return img
    ratio = width / img.width
    return img.resize((width, round(img.height * ratio)), Image.LANCZOS)


def compose(
    out_name: str,
    title: str,
    subtitle: str,
    panels: list[tuple[str, str]],          # [(filename, label), ...]
    cols: int,
) -> None:
    """Build a single composite.

    Layout: outer pad → header → grid of (label + image) cells with
    inner pad between them → outer pad.
    """
    loaded = [(load_scaled(f), label) for f, label in panels]

    # Row heights: each row's height is the max panel height in that row + LABEL_H.
    rows = [loaded[i : i + cols] for i in range(0, len(loaded), cols)]
    row_heights = [max(p.height for p, _ in row) + LABEL_H for row in rows]

    content_w = cols * COL_WIDTH + (cols - 1) * PAD_INNER
    content_h = sum(row_heights) + (len(rows) - 1) * PAD_INNER

    total_w = content_w + 2 * PAD_OUTER
    total_h = HEADER_H + content_h + 2 * PAD_OUTER

    canvas = Image.new("RGB", (total_w, total_h), BG)
    d = ImageDraw.Draw(canvas)

    # Header
    d.text((PAD_OUTER, PAD_OUTER), title, font=font(36, bold=True), fill=TITLE_FG)
    d.text(
        (PAD_OUTER, PAD_OUTER + 46),
        subtitle,
        font=font(20),
        fill=COUNT_FG,
    )

    # Panels
    y = PAD_OUTER + HEADER_H
    panel_idx = 0
    for row in rows:
        row_h = max(p.height for p, _ in row) + LABEL_H
        for col_idx, (img, label) in enumerate(row):
            x = PAD_OUTER + col_idx * (COL_WIDTH + PAD_INNER)
            # Label strip
            d.rectangle(
                [x, y, x + COL_WIDTH, y + LABEL_H],
                fill=PANEL_LABEL_BG,
            )
            d.text(
                (x + 16, y + 12),
                label,
                font=font(22, bold=True),
                fill=LABEL_FG,
            )
            d.text(
                (x + 16, y + 12),
                "",  # placeholder; could add per-panel detail later
                font=font(14),
                fill=COUNT_FG,
            )
            # Image
            canvas.paste(img, (x, y + LABEL_H))
            panel_idx += 1
        y += row_h + PAD_INNER

    canvas.save(OUT / out_name, "PNG", optimize=True)
    size_kb = (OUT / out_name).stat().st_size // 1024
    print(f"✓ {out_name}  ({total_w}x{total_h}, {size_kb} KB)")


COMPOSITES: list[dict] = [
    {
        "out": "composite_01_auth.png",
        "title": "Auth surfaces (public)",
        "subtitle": "Login, registration, invite-accept landing.",
        "cols": 3,
        "panels": [
            ("00_login.png", "Login — /login"),
            ("01_register.png", "Register — /register"),
            ("60_public_invite_invalid.png", "Invite accept — invalid token state"),
        ],
    },
    {
        "out": "composite_02_incident_management.png",
        "title": "Incident Management",
        "subtitle": "Incidents list, modals, detail view, approvals, live session.",
        "cols": 2,
        "panels": [
            ("02_incidents_list.png", "Incidents — /dashboard/incidents"),
            ("05_incident_detail.png", "Incident detail — /dashboard/incidents/detail"),
            ("03_incidents_new_modal.png", "New incident modal"),
            ("04_incidents_fire_test_modal.png", "Fire test incident modal"),
            ("06_approvals.png", "Approvals queue — /dashboard/approvals"),
        ],
    },
    {
        "out": "composite_03_paging_primary.png",
        "title": "Paging & On-call (1/2)",
        "subtitle": "Teams, services, rosters, priority rules.",
        "cols": 2,
        "panels": [
            ("10_paging_teams.png", "Teams — /dashboard/paging/teams"),
            ("11_paging_services.png", "Services — /dashboard/paging/services"),
            ("12_paging_rosters.png", "Rosters — /dashboard/paging/rosters"),
            ("13_paging_priority_rules.png", "Priority Rules — /dashboard/paging/priority-rules"),
        ],
    },
    {
        "out": "composite_04_paging_secondary.png",
        "title": "Paging & On-call (2/2)",
        "subtitle": "Escalation chains, maintenance windows, per-user notifications.",
        "cols": 1,
        "panels": [
            ("14_paging_escalation_chains.png", "Escalation Chains — /dashboard/paging/escalation-chains"),
            ("15_paging_maintenance_windows.png", "Maintenance Windows — /dashboard/paging/maintenance-windows"),
            ("16_paging_my_notifications.png", "My Notifications — /dashboard/paging/my-notifications"),
        ],
    },
    {
        "out": "composite_05_ai_agent.png",
        "title": "AI Agent",
        "subtitle": "Skills, memories, MCP servers, models, workflows, agent teams.",
        "cols": 2,
        "panels": [
            ("20_ai_skills.png", "Skills — /dashboard/skills"),
            ("21_ai_memories.png", "Memories — /dashboard/memories"),
            ("22_ai_mcp_servers.png", "MCP Servers — /dashboard/mcp-servers"),
            ("23_ai_models.png", "Models — /dashboard/models"),
            ("24_ai_workflows.png", "Workflows — /dashboard/workflows"),
            ("25_ai_agent_teams.png", "Agent Teams — /dashboard/agent-teams"),
        ],
    },
    {
        "out": "composite_06_integrations_observe.png",
        "title": "Integrations + Observe",
        "subtitle": "Outbound + inbound integrations and the observability surface.",
        "cols": 2,
        "panels": [
            ("30_integ_bot_connectors.png", "Bot Connectors — /dashboard/bot-connectors"),
            ("31_integ_webhooks.png", "Webhook Triggers — /dashboard/webhooks"),
            ("32_integ_ingest_tokens.png", "Ingest Tokens — /dashboard/ingest-tokens"),
            ("40_observe_scans.png", "Environment Scans — /dashboard/scans"),
            ("41_observe_reliability.png", "Reliability — /dashboard/reliability"),
            ("42_observe_activity.png", "Activity — /dashboard/activity"),
        ],
    },
    {
        "out": "composite_07_admin_people.png",
        "title": "Admin — People (Sprint 56)",
        "subtitle": "User + invite management, per-user detail, new-invite modal.",
        "cols": 2,
        "panels": [
            ("50_admin_people_users.png", "People — Users tab"),
            ("51_admin_people_invites.png", "People — Invites tab"),
            ("52_admin_people_new_invite_modal.png", "New invite modal"),
            ("53_admin_people_detail.png", "Per-user page — /dashboard/people/detail"),
        ],
    },
    {
        "out": "composite_08_admin_org_config.png",
        "title": "Admin — Organizations + Config",
        "subtitle": "Org-level settings and the slimmed-down runtime config page.",
        "cols": 1,
        "panels": [
            ("54_admin_organizations.png", "Organizations — /dashboard/organizations"),
            ("55_admin_config.png", "Config — /dashboard/config (Runtime + Storage & retention)"),
        ],
    },
]


def main() -> None:
    for spec in COMPOSITES:
        compose(
            out_name=spec["out"],
            title=spec["title"],
            subtitle=spec["subtitle"],
            panels=spec["panels"],
            cols=spec["cols"],
        )
    print(f"\n{len(COMPOSITES)} composites written to {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
