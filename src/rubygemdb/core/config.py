import os
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from typing import Literal, Optional, Union

class Settings(BaseSettings):
    # API Keys
    mistral_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    context7_api_key: Optional[str] = None
    
    # Endpoints & Models
    rubygemdb_provider: str = Field(
        default="openrouter",
        validation_alias=AliasChoices("rubygemdb_provider", "provider"),
    )
    llm_endpoint: Optional[str] = None
    rubygemdb_model: str = Field(
        default="upstage/solar-mini4",
        validation_alias=AliasChoices("rubygemdb_model", "model"),
    )
    rubygemdb_agent_model: str = Field(
        default="openrouter/z-ai/glm-5.3-flash",
        validation_alias=AliasChoices("rubygemdb_agent_model", "agent_model"),
    )

    rubygemdb_embedding_provider: Optional[str] = Field(
        default="ollama",
        validation_alias=AliasChoices("rubygemdb_embedding_provider", "embedding_provider"),
    )
    rubygemdb_embedding_model: str = Field(
        default="embeddinggemma-2:440m",
        validation_alias=AliasChoices("rubygemdb_embedding_model", "embedding_model"),
    )
    embedding_chunk_size: int = 500
    rubygems_api_url: str = "https://rubygems.org/api/v1/gems/{name}.json"
    rubygems_search_url: str = "https://rubygems.org/api/v1/search.json?query={query}"

    # Chunking Configuration
    chunk_parent_mode: Literal["paragraph", "full_doc"] = "paragraph"
    chunk_parent_max_chars: int = 4000
    chunk_child_max_chars: int = 512
    chunk_child_overlap_chars: int = 50
    chunk_normalize_whitespace: bool = True
    chunk_strip_urls_emails: bool = False

    # Query Expansion Configuration
    query_expansion_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("query_expansion_enabled", "expander_enabled"),
    )
    query_expander_repo_id: str = Field(
        default="mradermacher/qmd-query-expansion-1.7B-GGUF",
        validation_alias=AliasChoices("query_expander_repo_id", "expander_repo_id"),
    )
    query_expander_filename: str = Field(
        default="qmd-query-expansion-1.7B.Q4_K_M.gguf",
        validation_alias=AliasChoices("query_expander_filename", "expander_filename"),
    )
    query_expander_n_ctx: int = Field(
        default=1024,
        validation_alias=AliasChoices("query_expander_n_ctx", "expander_n_ctx"),
    )
    query_expander_n_threads: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("query_expander_n_threads", "expander_n_threads"),
    )

    # Cross-Encoder Reranker Configuration
    reranker_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("reranker_enabled", "cross_encoder_enabled"),
    )
    reranker_repo_id: str = Field(
        default="ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF",
        validation_alias=AliasChoices("reranker_repo_id", "cross_encoder_repo_id"),
    )
    reranker_filename: str = Field(
        default="qwen3-reranker-0.6b-q8_0.gguf",
        validation_alias=AliasChoices("reranker_filename", "cross_encoder_filename"),
    )
    reranker_n_ctx: int = Field(
        default=1024,
        validation_alias=AliasChoices("reranker_n_ctx", "cross_encoder_n_ctx"),
    )
    reranker_n_threads: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("reranker_n_threads", "cross_encoder_n_threads"),
    )
    reranker_candidate_multiplier: int = Field(
        default=3,
        validation_alias=AliasChoices("reranker_candidate_multiplier", "candidate_multiplier"),
    )

    # Models Cache Directory
    models_cache_dir: Optional[Path] = Field(
        default=None,
        validation_alias=AliasChoices("models_cache_dir", "model_cache_dir"),
    )

    @property
    def resolved_models_dir(self) -> Path:
        """Directory for downloaded GGUF models."""
        if self.models_cache_dir:
            return Path(self.models_cache_dir)
        return self.data_dir / "models"

    @property
    def active_provider(self) -> str:
        """Normalized LLM provider name ('openrouter', 'mistral', etc.)."""
        return self.rubygemdb_provider.strip().lower()

    @property
    def active_model(self) -> str:
        """Active primary LLM model for classification and generation."""
        return self.rubygemdb_model.strip()

    @property
    def active_agent_model(self) -> str:
        """Active agent model configured for LiteLLM (with provider prefix if needed)."""
        m = self.rubygemdb_agent_model.strip()
        if self.active_provider == "openrouter" and not m.startswith("openrouter/"):
            return f"openrouter/{m}"
        return m

    @property
    def llm_api_key(self) -> Optional[str]:
        """Resolves the active API key according to the configured provider."""
        if self.active_provider == "openrouter":
            return self.openrouter_api_key or os.environ.get("OPENROUTER_API_KEY")
        if self.active_provider == "mistral":
            return self.mistral_api_key or os.environ.get("MISTRAL_API_KEY")
        return (
            self.openrouter_api_key
            or os.environ.get("OPENROUTER_API_KEY")
            or self.mistral_api_key
            or os.environ.get("MISTRAL_API_KEY")
        )

    @property
    def active_llm_endpoint(self) -> str:
        """Resolves the chat completion endpoint for LLMService."""
        if self.llm_endpoint:
            return self.llm_endpoint
        if self.active_provider == "openrouter":
            return "https://openrouter.ai/api/v1/chat/completions"
        return "https://api.mistral.ai/v1/chat/completions"

    @property
    def embedding_model_path(self) -> str:
        """Derives the full model path for txtai Embeddings from .env (e.g. 'ollama/embeddinggemma-2:440m')."""
        model = (self.rubygemdb_embedding_model or "").strip()
        provider = (self.rubygemdb_embedding_provider or "").strip()
        if not model:
            return "ollama/embeddinggemma-2:440m"
        if "/" in model:
            return model
        if provider:
            return f"{provider}/{model}"
        return model
    
    # Delays & Batches
    rate_limit_delay: float = 0.5
    llm_rate_delay: float = 0.5
    llm_batch_size: int = 10
    
    # Hardware & Accelerator
    cuda_enabled: Optional[bool] = None
    device: Optional[str] = None

    @property
    def is_cuda_available(self) -> bool:
        if self.cuda_enabled is False:
            return False
        try:
            import torch
            available = bool(torch.cuda.is_available())
            return available
        except Exception:
            return False

    @property
    def txtai_gpu(self) -> Union[bool, str]:
        """Value to pass to txtai Embeddings gpu parameter."""
        if self.cuda_enabled is False:
            return False
        if self.device:
            return self.device
        return self.is_cuda_available

    # Paths
    project_root: Path = Path(__file__).parent.parent.parent.parent
    data_dir: Path = project_root / "data"
    cache_dir: Path = data_dir / "cache"
    
    gem_cache_file: Path = cache_dir / "gem_cache.json"
    llm_cache_file: Path = cache_dir / "llm_cache.json"
    classified_gems_file: Path = data_dir / "classified_gems.json"
    sqlite_db_file: Path = data_dir / "rubygemdb.sqlite"
    session_db_file: Path = data_dir / "sessions.sqlite"
    txtai_dir: Path = data_dir / "txtai"
    prune_report_file: Path = data_dir / "prune-report.md"
    gem_repos_dir: Path = cache_dir / "gem_repos"
    graphrag_dir: Path = cache_dir / "graphrag"
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()

# Ensure directories exist
settings.data_dir.mkdir(exist_ok=True)
settings.cache_dir.mkdir(exist_ok=True)
settings.gem_repos_dir.mkdir(parents=True, exist_ok=True)
settings.graphrag_dir.mkdir(parents=True, exist_ok=True)
settings.resolved_models_dir.mkdir(parents=True, exist_ok=True)
