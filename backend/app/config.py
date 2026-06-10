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
    # clarify protocol: dual | v2
    #   v2   = LIST + BLPOP handshake only (race-free; requires updated agent callback)
    #   dual = v2 plus legacy GET/pubsub keys for not-yet-updated agent deployments
    clarify_protocol: str = Field(default="dual")
    # How long the runner waits for the user to answer a clarify modal.
    # Must stay well under acp_prompt_timeout so one clarify round can't kill the prompt.
    clarify_timeout_seconds: int = 240
    # session/prompt deadline for the ACP subprocess.
    acp_prompt_timeout: int = 900
    # Smart-strategy keyword lists (tunable per deployment without redeploy).
    clarify_high_risk_keywords: list[str] = Field(default=[
        "删除", "覆盖", "执行", "停止", "取消", "购买",
        "remove", "delete", "execute", "run", "stop", "cancel",
        "buy", "purchase", "overwrite", "drop", "truncate",
        "rm -", "format", "destroy", "kill", "sudo",
    ])
    clarify_medium_risk_keywords: list[str] = Field(default=[
        "生成", "创建", "修改", "配置", "部署", "写入", "安装",
        "generate", "create", "modify", "configure", "deploy",
        "write", "install", "update", "upgrade", "push", "commit",
        "merge", "发布", "上线", "重启", "reboot", "restart",
    ])
    clarify_low_risk_patterns: list[str] = Field(default=[
        "继续", "下一步", "确认", "好的", "开始", "是", "ok",
        "yes", "proceed", "continue", "next", "confirm", "start",
        "go ahead", "sure", "没问题", "可以", "确定",
        "advance", "forward", "行", "中", "对", "嗯",
    ])

    # ── Rate limiting ──
    rate_limit_per_min: int = 30  # per-user message sends / minute (default)
    login_rate_limit_per_min: int = 10  # per-IP login attempts / minute (brute-force guard)

    # ── Uploads ──
    max_upload_mb: int = 25  # reject uploads larger than this (per file)

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

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def validate_for_production(self) -> list[str]:
        """Return a list of fatal misconfigurations for a production deploy.

        Empty list ⇒ safe to boot. Callers (startup) should refuse to start
        when this is non-empty in production so insecure defaults never ship.
        """
        problems: list[str] = []
        if self.secret_key.startswith("change-me") or len(self.secret_key) < 32:
            problems.append(
                "SECRET_KEY is the insecure default (or <32 chars) — set a strong random value"
            )
        if self.first_admin_password == "Hermes@2026":
            problems.append(
                "FIRST_ADMIN_PASSWORD is the well-known default — override it"
            )
        if self.storage_backend == "minio" and self.minio_secret_key == "hermes-minio-secret":
            problems.append("MINIO_SECRET_KEY is the default — override it")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
