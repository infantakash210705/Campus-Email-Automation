"""Configuration loaded from environment variables (optionally via a .env file).

Secrets are never stored in the database and never shown in the UI.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
VALID_SECURITY = ("starttls", "ssl", "none")


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = (env.get(key) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = (env.get(key) or "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    db_path: str = "data/shortfundly.db"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = field(default="", repr=False)  # never printed
    smtp_security: str = "starttls"
    smtp_timeout: int = 30
    from_email: str = ""
    from_name: str = ""
    send_mode: str = "test"  # "test" or "live"
    test_recipient: str = ""
    sender_name: str = ""
    sender_title: str = ""
    max_attempts: int = 3
    send_delay_seconds: float = 1.0
    max_batch_size: int = 50
    # ---- monitoring / discovery (Phase 3 enhancement) ----
    monitor_interval: str = "daily"          # 6h | 12h | daily | weekly | manual
    max_pages_per_college: int = 5
    max_colleges_per_run: int = 25
    request_timeout: int = 20
    retry_count: int = 3
    per_domain_delay: float = 2.0
    user_agent: str = "ShortfundlyCampusBot/1.0 (+contact: admin)"
    notifications_enabled: bool = False
    notify_recipient: str = ""
    log_level: str = "INFO"
    # ---- full automation (Phase 4) ----
    # Auto-approve: contacts scoring >= threshold are approved without human review.
    auto_approve_enabled: bool = False
    auto_approve_threshold: int = 80
    # Seed CSV: colleges listed here are imported automatically on every worker run.
    seed_csv_path: str = "data/seed_colleges.csv"
    # Auto-send: queue + send emails after monitoring (requires SEND_MODE=live).
    auto_send_enabled: bool = False
    auto_send_campaign: str = "auto"

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Config":
        env = os.environ if env is None else env
        get = lambda k, d="": (env.get(k) or d).strip()
        security = get("SMTP_SECURITY", "starttls").lower()
        if security not in VALID_SECURITY:
            security = "starttls"
        mode = "live" if get("SEND_MODE", "test").lower() == "live" else "test"
        db_path = get("DB_PATH", "data/shortfundly.db")
        if db_path != ":memory:" and not Path(db_path).is_absolute():
            db_path = str(BASE_DIR / db_path)
        return cls(
            db_path=db_path,
            smtp_host=get("SMTP_HOST"),
            smtp_port=_int(env, "SMTP_PORT", 587),
            smtp_username=get("SMTP_USERNAME"),
            smtp_password=env.get("SMTP_PASSWORD") or "",
            smtp_security=security,
            smtp_timeout=max(1, _int(env, "SMTP_TIMEOUT_SECONDS", 30)),
            from_email=get("SMTP_FROM_EMAIL"),
            from_name=get("SMTP_FROM_NAME"),
            send_mode=mode,
            test_recipient=get("TEST_RECIPIENT"),
            sender_name=get("SENDER_NAME"),
            sender_title=get("SENDER_TITLE"),
            max_attempts=max(1, _int(env, "MAX_ATTEMPTS", 3)),
            send_delay_seconds=max(0.0, _float(env, "SEND_DELAY_SECONDS", 1.0)),
            max_batch_size=max(1, _int(env, "MAX_BATCH_SIZE", 50)),
            monitor_interval=get("MONITOR_INTERVAL", "daily").lower(),
            max_pages_per_college=max(1, _int(env, "MAX_PAGES_PER_COLLEGE", 5)),
            max_colleges_per_run=max(1, _int(env, "MAX_COLLEGES_PER_RUN", 25)),
            request_timeout=max(1, _int(env, "REQUEST_TIMEOUT", 20)),
            retry_count=max(0, _int(env, "RETRY_COUNT", 3)),
            per_domain_delay=max(0.0, _float(env, "PER_DOMAIN_DELAY", 2.0)),
            user_agent=get("USER_AGENT", "ShortfundlyCampusBot/1.0 (+contact: admin)"),
            notifications_enabled=get("NOTIFICATIONS_ENABLED", "false").lower() in ("1", "true", "yes", "on"),
            notify_recipient=get("NOTIFY_RECIPIENT"),
            log_level=get("LOG_LEVEL", "INFO").upper(),
            # Phase 4: full automation
            auto_approve_enabled=get("AUTO_APPROVE_ENABLED", "false").lower() in ("1", "true", "yes", "on"),
            auto_approve_threshold=max(1, min(100, _int(env, "AUTO_APPROVE_THRESHOLD", 80))),
            seed_csv_path=get("SEED_CSV_PATH", "data/seed_colleges.csv"),
            auto_send_enabled=get("AUTO_SEND_ENABLED", "false").lower() in ("1", "true", "yes", "on"),
            auto_send_campaign=get("AUTO_SEND_CAMPAIGN", "auto") or "auto",
        )

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_port > 0 and self.from_email)

    def redact(self, text: str) -> str:
        """Remove the SMTP password from any text before it is stored or displayed."""
        if self.smtp_password and self.smtp_password in text:
            text = text.replace(self.smtp_password, "********")
        return text


def load_config() -> Config:
    """Load .env (values already in the environment win) and build a Config."""
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env", override=False)
    return Config.from_env(os.environ)
