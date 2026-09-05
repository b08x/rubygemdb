import re
import os
import json
import sqlite3

from txtai import Embeddings
from smolagents import LiteLLMModel, ToolCallingAgent, tool
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



@tool
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
        self.model: LiteLLMModel | None = None
        
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
            
            # Load gem_cache.json for last updated date
            import json
            gem_cache = {}
            if os.path.exists("data/cache/gem_cache.json"):
                with open("data/cache/gem_cache.json", "r") as f:
                    gem_cache = json.load(f)
            
            def stream_data():
                for i, row in enumerate(rows):
                    gem_name = row["name"]
                    gem_info = gem_cache.get(gem_name, {})
                    updated_at = gem_info.get("version_created_at", "2000-01-01T00:00:00.000Z")
                    # Parent chunk is the full description
                    parent_text = row["description"] or f"Ruby gem {gem_name}"
                    
                    # Split into child chunks for fine-grained retrieval
                    child_chunks = create_child_chunks(parent_text, max_chars=512)
                    if not child_chunks:
                        child_chunks = [parent_text]
                        
                    for chunk_idx, child_text in enumerate(child_chunks):
                        # Prepend the gem name to the chunk so the embedding captures both
                        searchable_text = f"Gem Name: {gem_name}\nDescription: {child_text}"
                        
                        document = {
                            "id": f"{gem_name}_{chunk_idx}",
                            "text": searchable_text, # Used for vector retrieval
                            "name": gem_name,
                            "source_code_uri": row["source_code_uri"],
                            "context7_id": row["context7_id"],
                            "parent_text": parent_text, # Parent chunk stored for context recall
                            "type": "gem",
                            "updated_at": updated_at
                        }
                        yield document
            
            self.embeddings.index(stream_data())
            self.embeddings.save(self.index_path)
            print(f"Successfully indexed {len(rows)} gems into txtai via sqlite-vec.")
            
        except sqlite3.OperationalError as e:
            print(f"Could not read from database: {e}")
        finally:
            conn.close()

    
    def index_memory(self, text: str, doc_type: str, metadata: dict | None = None):
        """Chunks and dynamically indexes new text into the embedding database."""
        if not text or not self.embeddings:
            return
            
        import time
        import uuid
        
        child_chunks = create_child_chunks(text, max_chars=512)
        if not child_chunks:
            child_chunks = [text]
            
        docs = []
        base_id = str(uuid.uuid4())
        
        for idx, child_text in enumerate(child_chunks):
            doc = {
                "id": f"{doc_type}_{base_id}_{idx}",
                "text": child_text,
                "parent_text": text,
                "type": doc_type,
                "timestamp": time.time()
            }
            if metadata:
                doc.update(metadata)
            docs.append(doc)
            
        self.embeddings.upsert(docs)
        self.embeddings.save(self.index_path)

    def build_index(self):
        """Forces a rebuild of the txtai index."""
        self._initialize_index(force_rebuild=True)

    @property
    def agent(self) -> ToolCallingAgent:
        """Lazy-initialize the Agent LLM on first use."""
        if self._agent is None:
            model_id = settings.rubygemdb_agent_model
            
            # Instantiate native LiteLLMModel for proper tool-calling support
            self.model = LiteLLMModel(model_id=model_id)

            c7_mcp = MCPClient(
                StdioServerParameters(command="npx", args=["-y", "@upstash/context7-mcp"]),
                structured_output=False,
            )
            
            # Wrap Context7 tools to intercept and index their results
            c7_tools = c7_mcp.get_tools()
            for tool_obj in c7_tools:
                original_forward = tool_obj.forward
                def wrap_forward(orig=original_forward, tool_name=tool_obj.name):
                    def forward_interceptor(*args, **kwargs):
                        result = orig(*args, **kwargs)
                        if isinstance(result, str) and result.strip():
                            self.index_memory(result, doc_type="context7", metadata={"source_tool": tool_name})
                        return result
                    return forward_interceptor
                tool_obj.forward = wrap_forward()

            # Define the memory tool dynamically so it can access self.model
            @tool
            def search_memory_tool(query: str, limit: int = 5) -> str:
                """
                Primary semantic search engine. Use this tool FIRST when asked to explore, 
                design, or find Ruby gems for a specific use-case or functionality (e.g., 'NLP pipeline').
                It searches a vector database of Ruby gems, past chat history, and Context7 docs.

                Args:
                    query: Natural language search query (e.g., 'fast web framework' or 'NLP text processing').
                    limit: Maximum number of results to return (default 5).
                """
                return self._execute_search_with_expansion(query, limit)

            self._agent = ToolCallingAgent(
                model=self.model,
                tools=[
                    search_memory_tool,
                    fetch_rubygems_info,
                    *c7_tools,
                ],
                max_steps=10
            )
        return self._agent
        
    def _execute_search_with_expansion(self, query: str, limit: int) -> str:
        if _embeddings_ref is None:
            return "Embeddings index not yet loaded."
            
        # 1. Multi-Query Expansion
        messages = [{"role": "user", "content": f"Generate 2 alternative search queries based on this query to find relevant documents in a semantic vector database. Output ONLY the queries separated by newlines, no markdown or intro.\nQuery: {query}"}]
        
        try:
            expansion_response = self.model(messages).content # type: ignore
            queries = [query] + [q.strip() for q in expansion_response.split('\n') if q.strip()]
        except Exception as e:
            print(f"Query expansion failed: {e}")
            queries = [query] # Fallback to original query on failure
            
        unique_parents = set()
        candidates = []
        
        from datetime import datetime
        current_year = datetime.now().year
        
        # 2. Search for all variations and gather candidates
        for q in queries:
            safe_query = q.replace("'", "''")
            sql = f"SELECT id, text, name, source_code_uri, context7_id, parent_text, type, updated_at, score FROM txtai WHERE similar('{safe_query}') LIMIT {limit * 4}"
            
            raw_results = _embeddings_ref.search(sql)
            
            for res in raw_results:
                doc_type = res.get("type", "unknown")
                parent_text = res.get("parent_text", "")
                dedup_key = res.get("name") if doc_type == "gem" and res.get("name") else parent_text
                
                if dedup_key not in unique_parents:
                    unique_parents.add(dedup_key)
                    
                    match_score = res.get("score", 0.0)
                    updated_at_str = res.get("updated_at", "2000-01-01T00:00:00.000Z")
                    
                    # Calculate recency boost
                    recency_score = 0.0
                    try:
                        dt = datetime.fromisoformat(updated_at_str.replace("Z", "+00:00"))
                        # Boost starts decaying from the current year
                        years_old = current_year - dt.year
                        if years_old <= 0:
                            recency_score = 1.0
                        elif years_old < 10:
                            recency_score = 1.0 - (years_old * 0.1) # 0.9 for 1 yr old, 0.1 for 9 yrs old
                    except Exception:
                        pass
                        
                    # Hybrid weighting: 70% semantic, 30% recency
                    hybrid_score = (match_score * 0.7) + (recency_score * 0.3)
                    
                    item = {
                        "type": doc_type,
                        "content": parent_text,
                        "match_score": round(match_score, 4),
                        "recency_score": round(recency_score, 4),
                        "hybrid_score": round(hybrid_score, 4),
                        "matched_query": q
                    }
                    
                    if doc_type == "gem":
                        item.update({
                            "name": res.get("name"),
                            "last_updated": updated_at_str,
                            "context7_id": res.get("context7_id"),
                            "source_code_uri": res.get("source_code_uri")
                        })
                        
                    candidates.append(item)

        # 3. Sort by hybrid score and limit
        candidates.sort(key=lambda x: x["hybrid_score"], reverse=True)
        formatted_results = candidates[:limit]
                
        return json.dumps(formatted_results, indent=2)

    def run(self, query: str) -> str:
        """Run the txtai Agent to answer a query and manage memory window."""
        # Index the user's query
        self.index_memory(query, doc_type="chat_user")
        
        response = self.agent(query)
        
        # Index the agent's final response
        self.index_memory(response, doc_type="chat_agent")
        
        # Prevent Context Bloat via Sliding Window
        # Keep only the system prompt + the last 6 steps (3 user/agent turns)
        if hasattr(self.agent, "memory") and hasattr(self.agent.memory, "steps"):
            if len(self.agent.memory.steps) > 6:
                # Assuming first step might be system prompt, but we simply keep last 6 for safety.
                # Usually we'd preserve step 0 if it's a SystemPromptStep.
                system_steps = [s for s in self.agent.memory.steps if getattr(s, "role", "") == "system"]
                recent_steps = self.agent.memory.steps[-6:]
                
                new_steps = []
                for s in system_steps:
                    if s not in recent_steps:
                        new_steps.append(s)
                new_steps.extend(recent_steps)
                
                self.agent.memory.steps = new_steps
                
        return response

if __name__ == "__main__":
    agent = TxtaiAgent()
    print("Agent ready. Testing a query...")
    response = agent.run("Find information about the 'rails' gem and check its context7 documentation.")
    print("Response:", response)
