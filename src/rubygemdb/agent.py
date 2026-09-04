import os
import json
import sqlite3
from typing import List, Dict, Optional, Any

from txtai import Embeddings, Agent, LLM
from smolagents import MCPClient
from mcp import StdioServerParameters
from rubygemdb.services.rubygems import RubyGemsService
from rubygemdb.core.config import settings

# Initialize RubyGems Service
rubygems_service = RubyGemsService()

def fetch_rubygems_info(gem_name: str) -> str:
    """
    Fetches detailed metadata about a Ruby gem from rubygems.org API.
    
    Args:
        gem_name: The name of the Ruby gem to look up.
        
    Returns:
        JSON string containing the gem's description, dependencies, and URLs.
    """
    info = rubygems_service.fetch_gem_info(gem_name)
    if not info:
        return f"Gem '{gem_name}' not found on rubygems.org."
    
    return json.dumps({
        "name": info.get("name"),
        "info": info.get("info"),
        "version": info.get("version"),
        "project_uri": info.get("project_uri"),
        "dependencies": info.get("dependencies", {}).get("runtime", [])
    }, indent=2)


# Module-level reference set after TxtaiAgent builds its index.
# smolagents introspects this wrapper (not the raw bound method) so all
# parameters must carry type hints.
_embeddings_ref: "Embeddings | None" = None

def search_gems(query: str, limit: int = 5) -> str:
    """
    Searches the local RubyGemDB vector index for gems matching a query.

    Args:
        query: Natural language search query describing the gem or use-case.
        limit: Maximum number of results to return (default 5).

    Returns:
        JSON string of matching gems with name, description, context7_id, and score.
    """
    if _embeddings_ref is None:
        return "Embeddings index not yet loaded."
    results = _embeddings_ref.search(query, limit)
    return json.dumps(results, indent=2)

class TxtaiAgent:
    def __init__(self, db_path: str = "data/rubygemdb.sqlite", txtai_dir: str = "data/txtai"):
        self.db_path = db_path
        self.txtai_dir = txtai_dir
        os.makedirs(self.txtai_dir, exist_ok=True)
        
        # Configure Embeddings with:
        # - Local GGUF via llama.cpp
        # - SQLite-vec backend
        # - Content enabled (dict documents)
        # - Hybrid search (dense+sparse)
        # - Reciprocal Rank Fusion (rrf) configured
        
        self.embeddings = Embeddings(
            path="ollama/embeddinggemma",
            backend="sqlite",
            content=True,
            hybrid=True,
            # Normalization / RRF Fusion
            # Convex combination is the default, but RRF is used for unnormalized sparse scores
            scoring={"method": "bm25", "normalize": False},
            fusion="rrf" 
        )
        
        # Load or rebuild index
        self.index_path = os.path.join(self.txtai_dir, "rubygems_index")
        self._initialize_index()

        # Wire the module-level search wrapper so smolagents can introspect it
        global _embeddings_ref
        _embeddings_ref = self.embeddings
        
        # Agent is lazily initialized on first run() call to avoid loading
        # the LLM during --embed (index-only) operations.
        self._agent = None
        
    def _initialize_index(self, force_rebuild: bool = False):
        """Loads data from the original sqlite table and indexes into txtai."""
        if os.path.exists(self.index_path) and not force_rebuild:
            self.embeddings.load(self.index_path)
            print("Loaded existing txtai index.")
            return

        print("Building txtai index from SQLite...")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            # Query the existing table
            cursor.execute("SELECT name, homepage, source_code_uri, context7_id, description FROM inventory")
            rows = cursor.fetchall()
            
            def stream_data():
                for i, row in enumerate(rows):
                    gem_name = row["name"]
                    # Dictionary document mapping to txtai content: True format
                    # text must be the primary indexed field for Embeddings
                    document = {
                        "id": str(i),
                        "text": row["description"] or f"Ruby gem {gem_name}",
                        "name": gem_name,
                        "source_code_uri": row["source_code_uri"],
                        "context7_id": row["context7_id"]
                    }
                    yield document
            
            self.embeddings.index(stream_data())
            self.embeddings.save(self.index_path)
            print(f"Successfully indexed {len(rows)} gems into txtai via sqlite-vec.")
            
        except sqlite3.OperationalError as e:
            print(f"Could not read from database: {e}")
        finally:
            conn.close()

    
    def build_index(self):
        """Forces a rebuild of the txtai index."""
        self._initialize_index(force_rebuild=True)

    @property
    def agent(self) -> Agent:
        """Lazy-initialize the Agent LLM on first use."""
        if self._agent is None:
            model = settings.rubygemdb_agent_model
            # LiteLLM requires a provider prefix (e.g. "mistral/model",
            # "openai/model"). Without it, litellm.completion raises
            # BadRequestError: "LLM Provider NOT provided".
            if "/" not in model:
                raise ValueError(
                    f"rubygemdb_agent_model='{model}' is missing a LiteLLM "
                    f"provider prefix. Use 'mistral/{model}' or another "
                    f"provider. See: https://docs.litellm.ai/docs/providers"
                )

            # Explicitly construct a LiteLLM-routed LLM pipeline.
            # Passing a bare string to Agent() lets smolagents try to load it
            # as a HuggingFace model. Using LLM(..., method="litellm") forces
            # the correct routing for mistral/, ollama/, etc. prefixes.
            llm = LLM(model, method="litellm")

            # Context7 MCP via stdio — MCPClient accepts StdioServerParameters,
            # NOT a plain dict. The dict form only works with http/sse transports.
            c7_mcp = MCPClient(
                StdioServerParameters(command="npx", args=["-y", "@upstash/context7-mcp"]),
                structured_output=False,
            )

            self._agent = Agent(
                llm=llm,
                tools=[
                    # 1. Local embeddings wired via typed wrapper (smolagents requires type hints)
                    search_gems,
                    # 2. RubyGems FunctionTool via docstring introspection
                    fetch_rubygems_info,
                    # 3. Context7 tools loaded from the stdio MCP server
                    *c7_mcp.get_tools(),
                ],
                max_iterations=10
            )
        return self._agent

    def run(self, query: str) -> str:
        """Run the txtai Agent to answer a query."""
        return self.agent(query)

if __name__ == "__main__":
    agent = TxtaiAgent()
    print("Agent ready. Testing a query...")
    response = agent.run("Find information about the 'rails' gem and check its context7 documentation.")
    print("Response:", response)
