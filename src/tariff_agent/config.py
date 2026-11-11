from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TARIFF_", env_file=".env", extra="ignore")
    database: Path = Path("data/private/app.db")
    database_url: SecretStr = SecretStr("")
    production: bool = False
    cookie_secure: bool = False
    session_seconds: int = 28800
    frontend_dist: Path = Path("frontend/dist")
    allowed_hosts: list[str] = ["127.0.0.1", "localhost", "testserver"]
    max_model_calls: int = 40
    governance_transport: str = "http"
    business_assets_enabled: bool = False
    tax_source_url: str = "http://127.0.0.1:18021"
    tax_language_url: str = "http://127.0.0.1:18022"
    language_rules: Path = Path("data/private/language_rules.json")
    evidence_budget_chars: int = 24000
    durable_execution_enabled: bool = True
    expert_memory_enabled: bool = True
    expert_memory_limit: int = 2
    memory_context_budget_chars: int = 2400
    otlp_endpoint: str = ""
    otlp_headers: SecretStr = SecretStr("")
    context_compression_enabled: bool = True
    context_max_input_tokens: int = 12000
    context_tokenizer: Path = Path("models/Qwen3-Embedding-8B/tokenizer.json")
    agentic_retrieval_enabled: bool = True
    agentic_retrieval_max_rounds: int = 2
    native_human_review_enabled: bool = True
    notes_pdf: Path = Path("data/private/source-documents/notes2022.pdf")
    strict_readiness: bool = True
    corpus: Path = Path("data/private/tariff.json")
    rules: Path = Path("data/private/valid_rules.json")
    word2vec: Path = Path("data/private/word2vec.pkl")
    risk_rules: Path = Path("data/private/risk_rules.json")
    api_key: SecretStr = SecretStr("")
    reviewer_key: SecretStr = SecretStr("")
    deepseek_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_enabled: bool = True
    embedding_url: str = "http://127.0.0.1:8011/v1"
    model_gateway_key: SecretStr = SecretStr("")
    model_gateway_ca: Path | None = None
    small_model_enabled: bool = False
    small_model_url: str = ""
    small_model_name: str = "Qwen3-4B"
    small_model_fallback_enabled: bool = True
    history_dense_enabled: bool = True
    embedding_model: str = "Qwen3-Embedding-8B"
    rerank_url: str = "http://127.0.0.1:8011/rerank"
    rerank_enabled: bool = True
    dense_enabled: bool = True
    allow_test_corpus: bool = False
    lease_seconds: int = 1800
    max_attempts: int = 3
    index_directory: Path = Path("data/private/index")
    history_enabled: bool = False
    history_directory: Path = Path("data/private/history-index")
    qdrant_url: str = "http://127.0.0.1:18033"
    qdrant_api_key: SecretStr = SecretStr("")
    history_alias: str = "tariff_history"
    qdrant_catalog_enabled: bool = False
    embedding_dimensions: int = 4096
    benchmark_directory: Path = Path("data/private/benchmark-industry-v1")
    evaluation_directory: Path = Path("data/private/evaluation-runs")
