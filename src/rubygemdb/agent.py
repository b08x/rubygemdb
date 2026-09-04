import re
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

def create_child_chunks(text: str, max_chars: int = 512) -> list[str]:
    """Splits a parent text into child chunks based on paragraphs and sentences."""
    if not text:
        return []
    
    # Split by paragraphs (double newlines)
    paragraphs = re.split(r'\n\s*\n', text)
    chunks = []
    
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
            
        if len(p) <= max_chars:
            chunks.append(p)
            continue
            
        # If paragraph is too long, split by sentence boundaries (.!?)
        sentences = re.split(r'(?<=[.!?])\s+', p)
        current_chunk = ""
        
        for sentence in sentences:
            if len(current_chunk) + len(sentence) + 1 <= max_chars:
                current_chunk += (sentence + " ") if current_chunk else sentence
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sentence
                
        if current_chunk:
            chunks.append(current_chunk.strip())
            
    return chunks



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
    Searches the local RubyGemDB vector index for gems using Parent/Child chunking.
    Retrieves small child chunks but returns the full parent context to the LLM.

    Args:
        query: Natural language search query describing the gem or use-case.
        limit: Maximum number of results to return (default 5).

    Returns:
        JSON string of matching gems with full parent text context.
    """
    if _embeddings_ref is None:
        return "Embeddings index not yet loaded."
        
    # Escape quotes for SQL query
    safe_query = query.replace("'", "''")
    
    # We fetch 3x the limit because multiple child chunks might belong to the same parent
    sql = f"SELECT id, text, name, source_code_uri, context7_id, parent_text, score FROM txtai WHERE similar('{safe_query}') LIMIT {limit * 3}"
    raw_results = _embeddings_ref.search(sql)
    
    unique_parents = set()
    formatted_results = []
    
    for res in raw_results:
        gem_name = res.get("name")
        
        if gem_name not in unique_parents:
            unique_parents.add(gem_name)
            
            # Return the PARENT chunk (full text) as the context instead of just the child chunk
            formatted_results.append({
                "name": gem_name,
                "description": res.get("parent_text"), # The parent context!
                "context7_id": res.get("context7_id"),
                "source_code_uri": res.get("source_code_uri"),
                "match_score": round(res.get("score", 0), 4),
                "matched_child_chunk": res.get("text") # Include for debug/transparency
            })
            
            if len(formatted_results) >= limit:
                break
                
    return json.dumps(formatted_results, indent=2)

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
                    # Parent chunk is the full description
                    parent_text = row["description"] or f"Ruby gem {gem_name}"
                    
                    # Split into child chunks for fine-grained retrieval
                    child_chunks = create_child_chunks(parent_text, max_chars=512)
                    if not child_chunks:
                        child_chunks = [parent_text]
                        
                    for chunk_idx, child_text in enumerate(child_chunks):
                        document = {
                            "id": f"{gem_name}_{chunk_idx}",
                            "text": child_text, # Child chunk used for vector retrieval
                            "name": gem_name,
                            "source_code_uri": row["source_code_uri"],
                            "context7_id": row["context7_id"],
                            "parent_text": parent_text # Parent chunk stored for context recall
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
