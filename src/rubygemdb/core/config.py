from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from typing import Optional, Union

class Settings(BaseSettings):
    # API Keys
    mistral_api_key: Optional[str] = None
    context7_api_key: Optional[str] = None
    
    # Endpoints
    llm_endpoint: str = "https://api.mistral.ai/v1/chat/completions"
    rubygemdb_model: str = "mistral-medium-latest"
    rubygemdb_agent_model: str = "mistral/mistral-small-latest"
    trackboi_distiller_model: str = "mistral/mistral-large-latest"
    rubygems_api_url: str = "https://rubygems.org/api/v1/gems/{name}.json"
    
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
    txtai_dir: Path = data_dir / "txtai"
    prune_report_file: Path = data_dir / "prune-report.md"
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()

# Ensure directories exist
settings.data_dir.mkdir(exist_ok=True)
settings.cache_dir.mkdir(exist_ok=True)
