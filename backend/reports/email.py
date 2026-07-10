"""Resolve per-organization SMTP settings with environment fallback."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth.secrets import decrypt_secret
from backend.config_loader import AppConfig
from backend.db.repos import OrgEmailSettingsRepo


@dataclass(frozen=True)
class ResolvedEmailSettings:
    host: str
    port: int
    security: str
    username: str | None
    password: str | None
    from_name: str | None
    from_address: str


async def resolve_email_settings(
    db: AsyncSession,
    org_id,
    *,
    config: AppConfig | None = None,
) -> ResolvedEmailSettings | None:
    row = await OrgEmailSettingsRepo.get_for_org(db, org_id)
    if row is not None:
        password = (
            decrypt_secret(row.password_encrypted) if row.password_encrypted else None
        )
        return ResolvedEmailSettings(
            host=row.host,
            port=row.port,
            security=row.security,
            username=row.username,
            password=password,
            from_name=row.from_name,
            from_address=row.from_address,
        )
    cfg = (config or AppConfig.load()).smtp
    if not cfg.configured:
        return None
    return ResolvedEmailSettings(
        host=str(cfg.host),
        port=cfg.port,
        security="starttls" if cfg.use_tls else "none",
        username=cfg.user,
        password=cfg.password,
        from_name=None,
        from_address=str(cfg.from_address),
    )


def build_email_channel(settings: ResolvedEmailSettings):
    from backend.paging.channels import EmailChannel

    return EmailChannel(
        smtp_host=settings.host,
        smtp_port=settings.port,
        smtp_user=settings.username,
        smtp_password=settings.password,
        from_addr=settings.from_address,
        from_name=settings.from_name,
        security=settings.security,
    )
