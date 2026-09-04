from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from typing import Optional

class Settings(BaseSettings):
    # API Keys
    mistral_api_key: Optional[str] = None
    context7_api_key: Optional[str] = None
    
    # Endpoints
    llm_endpoint: str = "https://api.mistral.ai/v1/chat/completions"
    rubygemdb_model: str = "mistral-medium-latest"
    rubygemdb_agent_model: str = "mistral/mistral-small-latest"
    rubygems_api_url: str = "https://rubygems.org/api/v1/gems/{name}.json"
    
    # Delays & Batches
    rate_limit_delay: float = 0.5
    llm_rate_delay: float = 0.5
    llm_batch_size: int = 10
    
    # Paths
    project_root: Path = Path(__file__).parent.parent.parent.parent
    data_dir: Path = project_root / "data"
    cache_dir: Path = data_dir / "cache"
    
    gem_cache_file: Path = cache_dir / "gem_cache.json"
    llm_cache_file: Path = cache_dir / "llm_cache.json"
    classified_gems_file: Path = data_dir / "classified_gems.json"
    sqlite_db_file: Path = data_dir / "rubygemdb.sqlite"
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()

# Ensure directories exist
settings.data_dir.mkdir(exist_ok=True)
settings.cache_dir.mkdir(exist_ok=True)
