"""Application configuration, driven by environment variables."""
from __future__ import annotations

import os
import secrets
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))


def _default_secret_key() -> str:
    """Generate a secure random secret for development. In production, always set SECRET_KEY env var."""
    env_val = os.environ.get("SECRET_KEY")
    if env_val:
        return env_val
    return f"dev-{secrets.token_hex(24)}"


def _default_admin_password() -> str:
    """Generate a random admin password for development. In production, always set FIRST_ADMIN_PASSWORD."""
    env_val = os.environ.get("FIRST_ADMIN_PASSWORD")
    if env_val:
        return env_val
    return f"Admin-{secrets.token_hex(8)}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @model_validator(mode="after")
    def _resolve_relative_paths(self) -> "Settings":
        if self.workspace_root:
            self.workspace_root = os.path.expanduser(self.workspace_root)
            if not os.path.isabs(self.workspace_root):
                self.workspace_root = os.path.join(_BACKEND_DIR, self.workspace_root)
        return self

    # ── App ──
    app_name: str = "Hermes 信使"
    environment: str = Field(default="development")  # development | staging | production
    debug: bool = Field(default=True)
    api_v1_prefix: str = "/api/v1"

    # ── Security / JWT ──
    secret_key: str = Field(default_factory=_default_secret_key)
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
    # Public origin of the app (scheme://host[:port]); used to scope OAuth
    # postMessage and absolute links. Empty ⇒ fall back to the first CORS origin.
    app_base_url: str = Field(default="")

    # ── Bootstrap super admin (seeded on first run) ──
    first_admin_email: str = "admin@hermes.io"
    first_admin_password: str = Field(default_factory=_default_admin_password)
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
    workspace_root: str = Field(default="~/hermes-data/workspaces")
    # Redis Stream (prompt queue) + consumer group.
    acp_stream: str = "acp:prompt"
    acp_group: str = "runner"
    acp_consumer: str = Field(default="runner-1")
    # Streaming hot-path: coalesce tokens for at most N ms (0 = emit immediately).
    stream_coalesce_ms: int = 0

    # ── Feature flags ──
    feature_followup_chips: bool = False  # show smart follow-up suggestion chips after agent replies
    # clarify protocol: dual | v2
    #   v2   = LIST + BLPOP handshake only (race-free; requires updated agent callback)
    #   dual = v2 plus legacy GET/pubsub keys for not-yet-updated agent deployments
    clarify_protocol: str = Field(default="dual")
    # How long the runner waits for the user to answer a clarify modal.
    # Must stay well under acp_prompt_timeout so one clarify round can't kill the prompt.
    clarify_timeout_seconds: int = 240
    # Set to "disabled" to turn off the clarify preamble on first turns.
    clarify_strategy: str = Field(default="")
    # session/prompt deadline for the ACP subprocess.
    acp_prompt_timeout: int = 900

    # ── Rate limiting ──
    rate_limit_per_min: int = 30  # per-user message sends / minute (default)
    login_rate_limit_per_min: int = 10  # per-IP login attempts / minute (brute-force guard)

    # ── Agent memory (做梦整理记忆) ──
    memory_total_chars: int = 2200  # combined budget for user_profile + soul + notes
    memory_consolidate_cooldown_seconds: int = 600  # non-admin trigger cooldown
    memory_consolidate_input_chars: int = 24000  # conversation-excerpt budget fed to the LLM
    memory_consolidate_msg_chars: int = 400  # per-message truncation in excerpts
    memory_consolidate_max_conversations: int = 50  # newest-first cap per run
    memory_consolidate_status_ttl: int = 3600  # done/error status visibility window
    # running-lock TTL; must exceed acp_prompt_timeout so a slow run can't double-start
    memory_consolidate_lock_ttl: int = 1200
    # Retrieval-time injection of episodic memory + skills — kill switch in
    # case pg_trgm relevance misbehaves in production; the flat
    # user_profile/soul/notes injection is unaffected either way.
    memory_episodic_injection_enabled: bool = True

    # ── Background subagents (persistent, non-blocking ACP peer sessions) ──
    subagent_idle_timeout_seconds: int = 900     # evict if no activity for 15 min
    subagent_max_lifetime_seconds: int = 14400   # hard cap: 4 hours, regardless of activity
    subagent_status_flush_interval_seconds: int = 5  # min gap between DB status writes

    # ── Self-evolving skills: eval-dataset builder (backend/skill_evolution/) ──
    skill_evolution_min_real_firings: int = 8       # below this, top up with synthetic examples
    skill_evolution_max_firings_per_skill: int = 60  # newest-first cap per dataset build
    skill_evolution_firing_excerpt_chars: int = 500  # per-example trigger-query truncation
    skill_evolution_dataset_input_chars: int = 24000  # total budget across real examples
    skill_evolution_synthetic_examples: int = 10     # how many to generate when topping up
    # Gates a proposal must clear before it's written (still requires manual approval after).
    skill_evolution_min_score_improvement: float = 0.05  # candidate must beat baseline by this much
    skill_evolution_max_content_bytes: int = 15360       # 15KB — injected verbatim into every firing's prompt
    skill_evolution_max_content_diff_ratio: float = 0.6  # 1 - SequenceMatcher.ratio(); too-large a rewrite is rejected
    # run-lock TTL; must exceed the optimizer's own worst-case runtime
    skill_evolution_lock_ttl: int = 1200
    skill_evolution_status_ttl: int = 3600  # done/error status visibility window
    # Real DSPy+GEPA optimizer — off by default; an admin must both flip this
    # AND configure a key before any direct-LLM call is ever made (same kill-switch
    # precedent as memory_episodic_injection_enabled). While off (or misconfigured),
    # run_evolution() falls back to the free, LLM-free stub from Stage D1.
    skill_evolution_enabled: bool = False
    skill_evolution_llm_model: str = ""      # litellm-style model string, e.g. "openai/gpt-4o-mini"
    skill_evolution_llm_api_key: str = ""    # treat as a secret: never logged, never in a response
    skill_evolution_llm_api_base: str = ""   # optional self-hosted/proxy endpoint
    skill_evolution_llm_max_calls_per_run: int = 60  # hard cap on judge-LM calls, on top of GEPA's own budget

    # ── P1-1 RAG: vector retrieval over team knowledge ──
    # Off by default. When off, _build_knowledge_prompt keeps the legacy
    # whole-document injection (truncated at _KNOWLEDGE_TOTAL). When on AND a
    # knowledge item has chunks indexed, dispatch embeds the user's query and
    # fetches only the top-k relevant chunks via pgvector cosine search.
    rag_enabled: bool = False
    rag_embedding_model: str = "BAAI/bge-small-zh-v1.5"  # 512-dim, ~95MB, CJK-optimised
    rag_embedding_dim: int = 512                          # must match migration 0057 Vector(N)
    rag_chunk_size: int = 500        # chars per chunk (CJK ≈ 250 tokens)
    rag_chunk_overlap: int = 100     # overlap between adjacent chunks
    rag_top_k: int = 5               # chunks fetched per query
    # When the combined top-k chunks exceed this many chars, they are
    # truncated to fit the prompt budget (mirrors the legacy _KNOWLEDGE_TOTAL).
    rag_max_context_chars: int = 8000

    # ── Auxiliary LLM: shared cheap-model channel for background tasks ──
    # Used by P1-2 (conversation summarisation) and P1-3 (staged auto-switch).
    # Off by default; an admin must configure a key before any direct LLM call
    # is made. Same kill-switch precedent as skill_evolution_llm_*.
    auxiliary_llm_model: str = ""       # litellm-style, e.g. "openai/gpt-4o-mini"
    auxiliary_llm_api_key: str = ""     # treat as a secret: never logged
    auxiliary_llm_api_base: str = ""    # optional proxy endpoint

    # ── P1-2 conversation summarisation ──
    # Periodically LLM-summarise a conversation's older messages and inject
    # the summary into the prompt prefix, so long chats don't overflow the
    # model context window. Never blocks a turn — runs async via Redis Stream.
    summary_enabled: bool = False
    summary_trigger_msg_count: int = 30   # summarise once history exceeds this
    summary_preserve_recent: int = 10     # keep the most-recent N messages verbatim
    summary_increment_threshold: int = 10  # only re-summarise if ≥N new msgs since last run
    summary_max_calls_per_conversation: int = 5  # hard cost cap per conversation
    # Per-message char budget fed to the summariser (mirrors memory_consolidate).
    summary_msg_chars: int = 400
    summary_input_chars: int = 12000       # total input budget across messages

    # ── Uploads ──
    max_upload_mb: int = 25  # reject uploads larger than this (per file)
    # P2-file: archive extraction limits (zip bomb defense).
    archive_max_files: int = 100        # cap on files inside an archive
    archive_max_total_mb: int = 100     # cap on decompressed total size
    # P2-file: strip EXIF metadata from uploaded images (privacy: GPS/camera).
    strip_exif_enabled: bool = True
    # Shared across every upload endpoint (conversation attachments, personal
    # file storage, team knowledge base, project docs): non-office files
    # bigger than this offload to object storage instead of inlining in
    # Postgres. Office docs (docx/xlsx/pptx/csv/rtf) always offload regardless
    # of size — see app.core.files.process_upload.
    file_offload_threshold_kb: int = 256

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
    def enforce_secure_config(self) -> bool:
        """Non-dev environments must refuse to boot with insecure defaults."""
        return self.environment in ("production", "staging")

    @property
    def primary_origin(self) -> str:
        """The app's canonical public origin (for OAuth postMessage scoping)."""
        return self.app_base_url or (self.cors_origins[0] if self.cors_origins else "")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def validate_for_production(self) -> list[str]:
        """Return a list of fatal misconfigurations for a non-dev deploy.

        Empty list ⇒ safe to boot. Callers (startup) should refuse to start
        when this is non-empty in production/staging so insecure defaults and
        debug mode never ship.
        """
        problems: list[str] = []
        weak_secret = (
            self.secret_key.startswith("dev-")
            or "change" in self.secret_key.lower()
            or len(self.secret_key) < 32
        )
        if weak_secret:
            problems.append(
                "SECRET_KEY is the insecure default (or <32 chars) — set a strong random value"
            )
        if self.first_admin_password.startswith("Admin-") or len(self.first_admin_password) < 12:
            problems.append(
                "FIRST_ADMIN_PASSWORD is the well-known default — override it"
            )
        # NOTE: MinIO secret key kept as default for backward compatibility with
        # existing data volume. Override in production only when rotating secrets
        # AND recreating the miniodata volume.
        # if self.storage_backend == "minio" and self.minio_secret_key == "hermes-minio-secret":
        #     problems.append("MINIO_SECRET_KEY is the default — override it")
        if self.debug:
            problems.append("DEBUG must be false outside development")
        if self.app_base_url == "":
            problems.append(
                "APP_BASE_URL is not set — required for OAuth postMessage scoping and absolute links"
            )
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
