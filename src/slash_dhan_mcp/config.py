from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from slashdhan.config import DEFAULT_SCRIP_MASTER_URL

DEFAULT_PORT = 8020
DEFAULT_PUBLIC_URL = f"http://127.0.0.1:{DEFAULT_PORT}"
MIN_SECRET_LEN = 32
SKILL_NAME = "slash-markets-dhan"
SKILL_ZIP_NAME = f"{SKILL_NAME}.zip"
DEFAULT_SKILL_FILE = Path(__file__).resolve().parents[2] / "SKILL.md"


class ConfigError(RuntimeError):
    """A required setting is missing or malformed."""


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError as e:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from e


def _secret(name: str) -> str:
    value = _env(name)
    if len(value) < MIN_SECRET_LEN:
        raise ConfigError(f"{name} must be set to at least {MIN_SECRET_LEN} characters")
    if not value.replace("-", "").replace("_", "").isalnum():
        raise ConfigError(f"{name} may only contain letters, digits, '-' and '_'")
    return value


@dataclass(frozen=True)
class Settings:
    public_url: str
    host: str
    port: int
    admin_secret: str
    creds_encryption_key: str
    state_dir: Path
    db_path: Path
    dhan_base_url: str
    scrip_master_url: str
    resend_api_key: str
    email_from: str
    operator_emails: tuple[str, ...]
    sweep_secret: str
    sweep_interval_s: int
    feed_warning_hours: int
    script_timeout_s: int
    script_max_timeout_s: int
    script_timeout_openai_s: int
    max_output_bytes: int
    max_concurrent_runs: int
    call_budget: int
    watchers_max_armed: int
    watchers_min_gap_minutes: int
    openai_apps_challenge: str
    skill_file: Path

    @property
    def mcp_url(self) -> str:
        return f"{self.public_url}/"

    @property
    def watchers_url(self) -> str:
        return f"{self.public_url}/watchers"

    @property
    def skill_url(self) -> str:
        return f"{self.public_url}/{SKILL_ZIP_NAME}"

    @property
    def workspace_dir(self) -> Path:
        return self.state_dir / "workspace"

    @property
    def cache_dir(self) -> Path:
        return self.state_dir / "cache"


def load_settings() -> Settings:
    state_dir = Path(_env("SLASH_DHAN_MCP_STATE_DIR") or "~/.slash-dhan-mcp").expanduser()
    operators = tuple(
        e.strip().lower() for e in _env("SLASH_DHAN_MCP_OPERATOR_EMAILS").split(",") if e.strip()
    )
    return Settings(
        public_url=(_env("SLASH_DHAN_MCP_PUBLIC_URL") or DEFAULT_PUBLIC_URL).rstrip("/"),
        host=_env("SLASH_DHAN_MCP_HOST") or "127.0.0.1",
        port=_int("SLASH_DHAN_MCP_PORT", DEFAULT_PORT),
        admin_secret=_secret("SLASH_DHAN_MCP_ADMIN_SECRET"),
        creds_encryption_key=_secret("CREDS_ENCRYPTION_KEY"),
        state_dir=state_dir,
        db_path=Path(
            _env("SLASH_DHAN_MCP_DB") or state_dir / "slash-dhan-mcp.sqlite3"
        ).expanduser(),
        dhan_base_url=(_env("DHAN_BASE_URL") or "https://api.dhan.co/v2").rstrip("/"),
        scrip_master_url=_env("SLASHDHAN_SCRIP_MASTER_URL") or DEFAULT_SCRIP_MASTER_URL,
        resend_api_key=_env("RESEND_API_KEY"),
        email_from=_env("EMAIL_FROM") or "/markets <no-reply@example.com>",
        operator_emails=operators,
        sweep_secret=_env("SWEEP_SECRET"),
        sweep_interval_s=_int("SWEEP_INTERVAL_SECONDS", 60),
        feed_warning_hours=_int("SLASH_DHAN_MCP_FEED_WARNING_HOURS", 2),
        script_timeout_s=_int("SCRIPT_TIMEOUT_SECONDS", 60),
        script_max_timeout_s=_int("SCRIPT_MAX_TIMEOUT_SECONDS", 180),
        script_timeout_openai_s=_int("SCRIPT_TIMEOUT_OPENAI_SECONDS", 50),
        max_output_bytes=_int("SCRIPT_MAX_OUTPUT_BYTES", 64 * 1024),
        max_concurrent_runs=_int("MAX_CONCURRENT_RUNS", 2),
        call_budget=_int("SLASHDHAN_CALL_BUDGET", 40),
        watchers_max_armed=_int("WATCHERS_MAX_ARMED", 50),
        watchers_min_gap_minutes=_int("WATCHERS_MIN_GAP_MINUTES", 15),
        openai_apps_challenge=_env("OPENAI_APPS_CHALLENGE_TOKEN"),
        skill_file=Path(_env("SLASH_DHAN_MCP_SKILL_FILE") or DEFAULT_SKILL_FILE).expanduser(),
    )
