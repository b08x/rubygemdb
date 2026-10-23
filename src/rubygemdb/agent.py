import re
import os
import json
import sqlite3

from txtai import Embeddings  # type: ignore
from smolagents import LiteLLMModel, ToolCallingAgent, tool  # type: ignore
from smolagents import MCPClient
from mcp import StdioServerParameters
from rubygemdb.services.rubygems import RubyGemsService
from rubygemdb.services.stack import guess_category
from rubygemdb.services.hybrid_search import build_fts_index, ensure_fts_index, hybrid_search
from rubygemdb.services.advisor import AdvisorSession
from rubygemdb.models.categories import CATEGORY_BY_SLUG
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
            gpu=settings.txtai_gpu,
            # Normalization / RRF Fusion
            # Convex combination is the default, but RRF is used for unnormalized sparse scores
            scoring={"method": "bm25", "normalize": False},
            fusion="rrf" 
        )
        
        # Load or rebuild index
        self.index_path = os.path.join(self.txtai_dir, "rubygems_index")
        self._initialize_index()

        # Agent is lazily initialized on first run() call to avoid loading
        # the LLM during --embed (index-only) operations.
        self._agent = None
        self.model: LiteLLMModel | None = None
        # Phase-walking advisor session, created on first advise() call.
        self._advisor: AdvisorSession | None = None
        
    def _initialize_index(self, force_rebuild: bool = False):
        """Loads data from the original sqlite table and indexes into txtai."""
        if os.path.exists(self.index_path) and not force_rebuild:
            self.embeddings.load(self.index_path)
            print("Loaded existing txtai index.")
            # Keep the FTS5 keyword index in sync when loading from disk.
            ensure_fts_index(self.db_path)
            return

        print("Building txtai index from SQLite...")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            # Query the existing table; join classified_gems for the category
            cursor.execute("""
                SELECT i.name, i.homepage, i.source_code_uri, i.context7_id, i.description,
                       c.classification
                FROM inventory i
                LEFT JOIN classified_gems c ON i.name = c.name
            """)
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

                    # Stack layer metadata from the classified category
                    category = None
                    try:
                        classification = json.loads(row["classification"]) if row["classification"] else {}
                        category = classification.get("primary")
                    except (ValueError, TypeError):
                        category = None
                    if category not in CATEGORY_BY_SLUG:
                        category = guess_category(gem_name)
                    cat = CATEGORY_BY_SLUG[category]
                    layer = cat.layer

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
                            "category": category,
                            "layer": layer,
                            "updated_at": updated_at
                        }
                        yield document
            
            self.embeddings.index(stream_data())
            self.embeddings.save(self.index_path)
            # The FTS5 keyword index is built or refreshed together with the txtai index.
            build_fts_index(self.db_path)
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

            # Codebase Memory MCP
            try:
                cbm_mcp = MCPClient(
                    StdioServerParameters(command="/home/b08x/.local/bin/codebase-memory-mcp", args=[]),
                    structured_output=False,
                )
                allowed_cbm_tools = ["list_projects", "get_architecture", "search_code", "search_graph", "trace_path"]
                cbm_tools = [t for t in cbm_mcp.get_tools() if t.name in allowed_cbm_tools]
            except Exception as e:
                print(f"Failed to load codebase-memory-mcp: {e}")
                cbm_tools = []


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

            # Define the memory tool dynamically so it can access the agent instance
            @tool
            def search_memory_tool(query: str, limit: int = 5) -> str:
                """
                Hybrid search engine over the Ruby gem database. Use this tool FIRST
                when asked to find, explain, or compare Ruby gems for a use-case.

                Args:
                    query: Natural language search query (e.g., 'fast web framework' or 'NLP text processing').
                    limit: Maximum number of results to return (default 5).
                """
                results = hybrid_search(query, phase=None, limit=limit, vector_search=self._vector_search, db_path=self.db_path)
                return json.dumps(results, indent=2)

            agent_prompt = """You are the RubyGemDB Gem Advisor. You answer follow-up questions about Ruby gems: what a gem does, whether it fits the user's project, how two gems compare, or what its risks are. You do NOT design implementation backlogs or scaffold code.

Rules:
- Call `search_memory_tool` first to find relevant gems; results carry category, layer, and build phase metadata.
- Call `fetch_rubygems_info` for current gem metadata (version, dependencies).
- Use the Context7 tools for usage documentation when available.
- If a `[Target Project Context: <project_name>]` is provided, call `get_architecture(project="<project_name>")` and `search_graph` or `search_code` to ground the answer in that project's actual structure.
- Keep answers concise, cite gems by name, and note integration risks or coupling concerns when relevant."""

            self._agent = ToolCallingAgent(
                model=self.model,
                instructions=agent_prompt,
                tools=[
                    search_memory_tool,
                    fetch_rubygems_info,
                    *c7_tools,
                    *cbm_tools,
                ],
                max_steps=10
            )
        return self._agent

    # -- phase-walking advisor -----------------------------------------------

    def _ensure_model(self) -> LiteLLMModel:
        """Lazily create the LiteLLM model shared by the agent and the advisor."""
        if self.model is None:
            self.model = LiteLLMModel(model_id=settings.rubygemdb_agent_model)
        return self.model

    def _llm_json(self, system_prompt: str, user_prompt: str) -> dict | None:
        """One JSON-only LLM call; returns None on any failure."""
        try:
            model = self._ensure_model()
            response = model([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]).content
            text = str(response).strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except Exception as e:
            print(f"Advisor LLM call failed: {e}")
            return None

    def _vector_search(self, query: str, categories: list[str] | None, limit: int) -> list[dict]:
        """txtai similarity search restricted to gem documents (and optionally categories)."""
        safe_query = query.replace("'", "''")
        sql = (
            "SELECT name, category, layer, parent_text, source_code_uri, context7_id, score "
            "FROM txtai WHERE similar('" + safe_query + "')"
        )
        if categories:
            cat_list = ", ".join("'" + c.replace("'", "''") + "'" for c in categories)
            sql += f" AND category IN ({cat_list})"
        sql += f" LIMIT {max(1, int(limit))}"
        hits = []
        for res in self.embeddings.search(sql):
            if res.get("type") != "gem":
                continue
            hits.append({
                "name": res.get("name"),
                "description": res.get("parent_text", ""),
                "category": res.get("category"),
                "layer": res.get("layer"),
                "source_code_uri": res.get("source_code_uri"),
                "context7_id": res.get("context7_id"),
                "score": res.get("score", 0.0),
            })
        return hits

    def _card_llm(self, brief: str) -> dict | None:
        system = (
            "You extract a structured requirement card from a Ruby project brief.\n"
            "- deliverable: one line, the shape of the thing being built (CLI tool, "
            "web service, library, GUI app)\n"
            "- inputs: data formats and sources the tool takes in (e.g. PDF "
            "documents, CSV files, HTTP APIs)\n"
            "- transforms: processing steps applied to that data (e.g. chunk text "
            "with context, extract tables, embed passages)\n"
            "- outputs: artifacts the tool produces (e.g. markdown report, JSON "
            "manifest, PDF)\n"
            "- constraints: hard requirements (e.g. no native extensions, must run "
            "offline)\n"
            "Also return open_questions: up to 3 short questions for anything "
            "genuinely ambiguous or missing. Do not invent requirements.\n"
            "If the brief is too vague to fill deliverable plus at least one other "
            'slot, return {"clarify": "<one clarifying question>"} instead.\n'
            'Return JSON only: {"card": {"deliverable": "...", "inputs": [...], '
            '"transforms": [...], "outputs": [...], "constraints": [...]}, '
            '"open_questions": [...]}'
        )
        return self._llm_json(system, f"Brief:\n{brief}")

    def _intent_llm(self, text: str, context: str) -> dict | None:
        system = (
            "You steer a gem-stack advisor conversation. Classify the user's "
            "message into exactly one action:\n"
            "- go: (card stage only) accept the requirement card and propose a stack\n"
            '- edit: change the requirement card (include "card" with any of '
            '"deliverable", "inputs", "transforms", "outputs", "constraints" — '
            "lists or strings)\n"
            "- accept: confirm the proposed stack as the final pick\n"
            '- add: include a named gem that was never offered (include "gem")\n'
            '- remove: drop a gem from the proposal (include "gem")\n'
            '- swap: replace one gem with another (include "gem" and "with")\n'
            "- retry: the proposed gems for a slot are off target; re-search with "
            'adjusted focus (include "slot" — deliverable/inputs/transforms/outputs '
            '- and "hint" with the words to focus on)\n'
            '- ask: a follow-up question about a gem (include "gem" and "question")\n'
            "- restart: start over with a new brief\n"
            "- chat: anything conversational — feedback, preferences, concerns. "
            "Answer it in one or two sentences that acknowledge the current card "
            'and stack, and suggest the next action (include "reply")\n'
            "The context line carries the stage, the current card, and the proposed "
            "stack. Return JSON only, with only the fields the action needs."
        )
        return self._llm_json(system, f"Context: {context}\nUser message: {text}")

    def _reason_llm(self, card_text: str, gems: list[dict]) -> dict | None:
        system = (
            "Given a project requirement summary and candidate gems, write a "
            "one-line reason per gem tying it to the requirements. If a gem "
            "ignores a stated constraint, say so in its reason.\n"
            'Return JSON only: {"<gem name>": "<one-line reason>", ...}'
        )
        gem_lines = "\n".join(f"- {g.get('name')}: {g.get('description', '')}" for g in gems)
        return self._llm_json(system, f"Requirements:\n{card_text}\nCandidate gems:\n{gem_lines}")

    def _ask_gem(self, gem: str, question: str) -> str:
        """Delegate a follow-up question to the tool-calling agent."""
        brief = self._advisor.brief if self._advisor else ""
        context = f"Project brief: {brief}\n\n" if brief else ""
        prompt = f"{context}Follow-up question about the Ruby gem '{gem}': {question}"
        return str(self.agent(prompt))

    def _fetch_gem(self, name: str) -> dict | None:
        """Case-insensitive inventory lookup for manually named gems."""
        try:
            conn = sqlite3.connect(self.db_path)
        except sqlite3.Error:
            return None
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT i.name, i.description, i.source_code_uri, i.context7_id, c.classification "
                "FROM inventory i LEFT JOIN classified_gems c ON i.name = c.name "
                "WHERE i.name = ? COLLATE NOCASE",
                (name,),
            ).fetchone()
        except sqlite3.Error:
            return None
        finally:
            conn.close()
        if row is None:
            return None
        category: str | None = None
        if row["classification"]:
            try:
                category = (json.loads(row["classification"]) or {}).get("primary")
            except (ValueError, TypeError):
                category = None
        if category not in CATEGORY_BY_SLUG:
            category = guess_category(row["name"])
        return {
            "name": row["name"],
            "description": row["description"] or "",
            "category": category,
            "source_code_uri": row["source_code_uri"],
            "context7_id": row["context7_id"],
        }

    def _build_advisor(self) -> AdvisorSession:
        def search(query: str, phase: str | None, limit: int) -> list[dict]:
            return hybrid_search(
                query,
                phase=phase,
                limit=limit,
                vector_search=self._vector_search,
                db_path=self.db_path,
            )

        return AdvisorSession(
            card_llm=self._card_llm,
            intent_llm=self._intent_llm,
            reason_llm=self._reason_llm,
            search=search,
            ask=self._ask_gem,
            fetch_gem=self._fetch_gem,
        )

    def advise(self, message: str) -> str:
        """Run the card-driven advisor over one chat message."""
        if self._advisor is None:
            self._advisor = self._build_advisor()
        return self._advisor.respond(message)

    @property
    def advisor_session(self) -> AdvisorSession | None:
        """The active advisor session, if one has been started."""
        return self._advisor

    def reset_advisor(self) -> None:
        """Drop the advisor session (Clear Chat)."""
        self._advisor = None

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
