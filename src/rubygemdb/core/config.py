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
