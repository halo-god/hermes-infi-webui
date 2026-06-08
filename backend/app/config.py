"""Application configuration, driven by environment variables."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── App ──
    app_name: str = "Hermes 信使"
    environment: str = Field(default="development")  # development | staging | production
    debug: bool = Field(default=True)
    api_v1_prefix: str = "/api/v1"

    # ── Security / JWT ──
    secret_key: str = Field(default="change-me-in-production-please-32+chars")
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7

    # ── Database ──
    # e.g. postgresql+asyncpg://hermes:hermes@postgres:5432/hermes
    database_url: str = Field(
        default="postgresql+asyncpg://hermes:hermes@localhost:5432/hermes"
    )
    db_echo: bool = False

    # ── Redis ──
    redis_url: str = Field(default="redis://localhost:6379/0")

    # ── CORS ──
    cors_origins: list[str] = Field(default=["http://localhost:5173", "http://localhost:8080"])

    # ── Bootstrap super admin (seeded on first run) ──
    first_admin_email: str = "admin@hermes.io"
    first_admin_password: str = "Hermes@2026"
    first_admin_name: str = "林知微"

    # ── Agent Runner / ACP ──
    # NousResearch Hermes Agent CLI: `hermes acp` serves ACP over stdio JSON-RPC.
    hermes_bin: str = Field(default="hermes")
    hermes_acp_args: list[str] = Field(default=["acp"])
    hermes_acp_auth_method: str = Field(default="")  # ACP authMethods id, if required
    # Override ~/.hermes location (useful inside Docker when host dir is mounted)
    hermes_home: str = Field(default="")
    acp_protocol_version: int = 1
    # Fall back to the bundled mock ACP agent when the real CLI isn't on PATH.
    acp_allow_mock_fallback: bool = True
    # Per-conversation working dir where agents drop produced files.
    workspace_root: str = Field(default="/tmp/hermes-workspaces")
    # Redis Stream (prompt queue) + consumer group.
    acp_stream: str = "acp:prompt"
    acp_group: str = "runner"
    acp_consumer: str = Field(default="runner-1")
    # Streaming hot-path: coalesce tokens for at most N ms (0 = emit immediately).
    stream_coalesce_ms: int = 0

    # ── Feature flags ──
    feature_followup_chips: bool = False  # show smart follow-up suggestion chips after agent replies
    # clarify strategy: interactive | smart | auto_first | disabled
    #   interactive = always pop confirmation modal (legacy)
    #   smart       = risk-based auto-decision (low=auto, medium/high=modal)
    #   auto_first  = always auto-select first option
    #   disabled    = suppress clarify tool calls entirely (auto-resolve with first option)
    clarify_strategy: str = Field(default="smart")

    # ── Rate limiting ──
    rate_limit_per_min: int = 30  # per-user message sends / minute (default)

    # ── Agent sandbox (P5) ──
    sandbox_enabled: bool = False         # apply POSIX rlimits to agent subprocesses
    sandbox_cpu_seconds: int = 120        # RLIMIT_CPU
    sandbox_nproc: int = 256              # RLIMIT_NPROC
    sandbox_fsize_mb: int = 256           # RLIMIT_FSIZE
    sandbox_memory_mb: int = 0            # RLIMIT_AS (0 = off; prefer cgroups/gVisor)
    sandbox_cmd: str = ""                 # optional wrapper, e.g. bwrap/firejail/runsc

    # ── Object storage (workspace artifacts) ──
    storage_backend: str = Field(default="db")  # db | minio
    minio_endpoint: str = Field(default="http://localhost:9000")
    minio_access_key: str = Field(default="hermes")
    minio_secret_key: str = Field(default="hermes-minio-secret")
    minio_bucket: str = Field(default="hermes-workspace")
    minio_region: str = Field(default="us-east-1")

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
