"""Gateway configuration loaded from environment variables."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    max_concurrent_slots: int = int(os.getenv("MAX_CONCURRENT_SLOTS", "10"))
    max_per_tenant_inflight: int = int(os.getenv("MAX_PER_TENANT_INFLIGHT", "4"))
    max_context_tokens: int = int(os.getenv("MAX_CONTEXT_TOKENS", "4096"))
    max_output_tokens: int = int(os.getenv("MAX_OUTPUT_TOKENS", "512"))
    default_tenant_token_budget: int = int(os.getenv("DEFAULT_TENANT_TOKEN_BUDGET", "50000"))
    retry_after_seconds: int = int(os.getenv("RETRY_AFTER_SECONDS", "1"))
    db_path: str = os.getenv("LEDGER_DB_PATH", "gateway_ledger.db")
    api_key: str = os.getenv("GATEWAY_API_KEY", "dev-secret-key")
    upstream_mode: str = os.getenv("UPSTREAM_MODE", "mock")  # "mock" | "openai_compatible"
    upstream_base_url: str = os.getenv("UPSTREAM_BASE_URL", "http://localhost:11434/v1")
    upstream_api_key: str = os.getenv("UPSTREAM_API_KEY", "ollama")
    upstream_model: str = os.getenv("UPSTREAM_MODEL", "qwen2.5:1.5b")


settings = Settings()
