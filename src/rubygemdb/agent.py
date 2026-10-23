import os
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Callable, Optional

from txtai import Embeddings  # type: ignore
from smolagents import LiteLLMModel, ToolCallingAgent, tool  # type: ignore
from smolagents import MCPClient
from mcp import StdioServerParameters
from rubygemdb.services.rubygems import RubyGemsService
from rubygemdb.services.stack import guess_category
from rubygemdb.services.hybrid_search import build_fts_index, ensure_fts_index, hybrid_search
from rubygemdb.services.graphrag import GraphRAGService
from rubygemdb.services.chunking import RecursiveParentChildChunker, ChunkConfig
from rubygemdb.models.categories import CATEGORY_BY_SLUG
from rubygemdb.models.session import RAGSession
from rubygemdb.storage.session_storage import SessionStorage
from rubygemdb.core.config import settings
from rubygemdb.services.embedding_cache import wrap_embeddings
from rubygemdb.services.tracing import (
    trace_agent,
    trace_generation,
    trace_retriever,
    trace_tool,
)

# Initialize RubyGems Service
rubygems_service = RubyGemsService()
logger = logging.getLogger("rubygemdb.agent")

def create_child_chunks(text: str, max_chars: int = 512) -> list[str]:
    """Splits a parent text into child chunks using RecursiveParentChildChunker."""
    if not text:
        return []
    overlap = min(settings.chunk_child_overlap_chars, max(0, max_chars - 1))
    config = ChunkConfig(
        child_max_chars=max_chars,
        child_overlap_chars=overlap,
        parent_mode="full_doc",
        normalize_whitespace=settings.chunk_normalize_whitespace,
        strip_urls_emails=settings.chunk_strip_urls_emails,
    )
    chunker = RecursiveParentChildChunker(config=config)
    preview = chunker.preview_chunks(text)
    chunks = [c.text for p in preview.parent_chunks for c in p.children]
    return chunks or [text]



@tool
def fetch_rubygems_info(gem_name: str) -> str:
    """
    Fetches detailed metadata about a Ruby gem from rubygems.org API.
    
    Args:
        gem_name: The name of the Ruby gem to look up.
        
    Returns:
        JSON string containing the gem's description, dependencies, and URLs.
    """
    with trace_tool(name="fetch-rubygems-info", input={"gem_name": gem_name}) as t_span:
        info = rubygems_service.fetch_gem_info(gem_name)
        if not info:
            res = f"Gem '{gem_name}' not found on rubygems.org."
            t_span.update(output={"found": False})
            return res
        
        t_span.update(output={"found": True, "name": info.get("name"), "version": info.get("version")})
        return json.dumps({
            "name": info.get("name"),
            "info": info.get("info"),
            "version": info.get("version"),
            "project_uri": info.get("project_uri"),
            "dependencies": info.get("dependencies", {}).get("runtime", [])
        }, indent=2)





class TxtaiAgent:
    def __init__(
        self,
        db_path: str = "data/rubygemdb.sqlite",
        txtai_dir: str = "data/txtai",
        session_storage: Optional[SessionStorage] = None,
    ):
        self.db_path = db_path
        self.txtai_dir = txtai_dir
        os.makedirs(self.txtai_dir, exist_ok=True)
        self.session_storage: SessionStorage = session_storage or SessionStorage()
        self.active_session_id: Optional[str] = None
        
        # Configure Embeddings with:
        # - Local GGUF via llama.cpp
        # - SQLite-vec backend
        # - Content enabled (dict documents)
        # - Hybrid search (dense+sparse)
        # - Reciprocal Rank Fusion (rrf) configured
        
        self.embeddings = Embeddings(
            path=settings.embedding_model_path,
            backend="sqlite",
            content=True,
            hybrid=True,
            gpu=settings.txtai_gpu,
            # Normalization / RRF Fusion
            # Convex combination is the default, but RRF is used for unnormalized sparse scores
            scoring={"method": "bm25", "normalize": False},
            fusion="rrf" 
        )
        wrap_embeddings(self.embeddings, model_name=settings.embedding_model_path)
        
        # Load or rebuild index
        self.index_path = os.path.join(self.txtai_dir, "rubygems_index")
        self._initialize_index()

        # Agent is lazily initialized on first run() call to avoid loading
        # the LLM during --embed (index-only) operations.
        self._agent = None
        self.model: LiteLLMModel | None = None
        self._graphrag_service: Optional[GraphRAGService] = None
        self._graphrag_gems: list[str] = []
        self._graphrag_intention: str = ""
        self._chat_history: list[dict] = []
        
    def _initialize_index(self, force_rebuild: bool = False):
        """Loads data from the original sqlite table and indexes into txtai."""
        if os.path.exists(self.index_path) and not force_rebuild:
            self.embeddings.load(self.index_path)
            print("Loaded existing txtai index.")
            # Keep the FTS5 keyword index in sync when loading from disk.
            ensure_fts_index(self.db_path)
            return

        print(f"Building txtai index from SQLite using {settings.embedding_model_path}...")
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
            total_rows = len(rows)
            chunk_size = settings.embedding_chunk_size or 500
            total_chunks = (total_rows + chunk_size - 1) // chunk_size if total_rows else 1
            
            # Load gem_cache.json for last updated date
            import json
            gem_cache = {}
            if os.path.exists("data/cache/gem_cache.json"):
                with open("data/cache/gem_cache.json", "r") as f:
                    gem_cache = json.load(f)
            
            # Initialize chunker for recursive parent-child indexing
            chunk_config = ChunkConfig(
                parent_mode=settings.chunk_parent_mode,
                parent_max_chars=settings.chunk_parent_max_chars,
                child_max_chars=settings.chunk_child_max_chars,
                child_overlap_chars=settings.chunk_child_overlap_chars,
                normalize_whitespace=settings.chunk_normalize_whitespace,
                strip_urls_emails=settings.chunk_strip_urls_emails,
            )
            chunker = RecursiveParentChildChunker(config=chunk_config)

            def stream_data():
                for i, row in enumerate(rows):
                    if i % chunk_size == 0:
                        chunk_idx = (i // chunk_size) + 1
                        chunk_end = min(i + chunk_size, total_rows)
                        print(f"Vectorizing inventory chunk {chunk_idx}/{total_chunks} (gems {i + 1}-{chunk_end} of {total_rows})...")
                    gem_name = row["name"]
                    gem_info = gem_cache.get(gem_name, {})
                    updated_at = gem_info.get("version_created_at", "2000-01-01T00:00:00.000Z")
                    # Parent text is the description or gem name
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

                    preview = chunker.preview_chunks(parent_text, doc_id=gem_name)
                    has_children = False
                    for p_idx, parent_chunk in enumerate(preview.parent_chunks):
                        for c_idx, child_chunk in enumerate(parent_chunk.children):
                            has_children = True
                            searchable_text = f"Gem Name: {gem_name}\nDescription: {child_chunk.text}"
                            document = {
                                "id": f"{gem_name}_{p_idx}_{c_idx}",
                                "text": searchable_text,  # Used for vector retrieval
                                "name": gem_name,
                                "source_code_uri": row["source_code_uri"],
                                "context7_id": row["context7_id"],
                                "parent_id": parent_chunk.parent_id,
                                "child_id": child_chunk.chunk_id,
                                "parent_text": parent_chunk.text,  # Parent chunk stored for context recall
                                "type": "gem",
                                "category": category,
                                "layer": layer,
                                "updated_at": updated_at,
                            }
                            yield document

                    if not has_children:
                        searchable_text = f"Gem Name: {gem_name}\nDescription: {parent_text}"
                        document = {
                            "id": f"{gem_name}_0_0",
                            "text": searchable_text,
                            "name": gem_name,
                            "source_code_uri": row["source_code_uri"],
                            "context7_id": row["context7_id"],
                            "parent_id": f"{gem_name}_p0",
                            "child_id": f"{gem_name}_p0_c0",
                            "parent_text": parent_text,
                            "type": "gem",
                            "category": category,
                            "layer": layer,
                            "updated_at": updated_at,
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

        chunk_config = ChunkConfig(
            parent_mode=settings.chunk_parent_mode,
            parent_max_chars=settings.chunk_parent_max_chars,
            child_max_chars=settings.chunk_child_max_chars,
            child_overlap_chars=settings.chunk_child_overlap_chars,
            normalize_whitespace=settings.chunk_normalize_whitespace,
            strip_urls_emails=settings.chunk_strip_urls_emails,
        )
        chunker = RecursiveParentChildChunker(config=chunk_config)
        base_id = str(uuid.uuid4())
        preview = chunker.preview_chunks(text, doc_id=base_id)

        docs = []
        for p_idx, parent_chunk in enumerate(preview.parent_chunks):
            for c_idx, child_chunk in enumerate(parent_chunk.children):
                doc = {
                    "id": f"{doc_type}_{base_id}_{p_idx}_{c_idx}",
                    "text": child_chunk.text,
                    "parent_id": parent_chunk.parent_id,
                    "child_id": child_chunk.chunk_id,
                    "parent_text": parent_chunk.text,
                    "type": doc_type,
                    "timestamp": time.time(),
                }
                if metadata:
                    doc.update(metadata)
                docs.append(doc)

        if not docs and text:
            doc = {
                "id": f"{doc_type}_{base_id}_0_0",
                "text": text,
                "parent_id": f"{base_id}_p0",
                "child_id": f"{base_id}_p0_c0",
                "parent_text": text,
                "type": doc_type,
                "timestamp": time.time(),
            }
            if metadata:
                doc.update(metadata)
            docs.append(doc)

        if docs:
            self.embeddings.upsert(docs)
            self.embeddings.save(self.index_path)

    def build_index(self):
        """Forces a rebuild of the txtai index."""
        self._initialize_index(force_rebuild=True)

    @property
    def agent(self) -> ToolCallingAgent:
        """Lazy-initialize the Agent LLM on first use."""
        if self._agent is None:
            model_id = settings.active_agent_model
            
            # Instantiate native LiteLLMModel for proper tool-calling support
            self.model = LiteLLMModel(
                model_id=model_id,
                api_key=settings.llm_api_key,
                max_tokens=settings.agent_max_tokens,
            )

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
                with trace_retriever(
                    name="hybrid-memory-search",
                    input={"query": query, "limit": limit},
                    tags=["rubygemdb", "search", "tool"],
                ) as mem_span:
                    results = hybrid_search(query, phase=None, limit=limit, vector_search=self._vector_search, db_path=self.db_path)
                    mem_span.update(output={"results_count": len(results)})
                    return json.dumps(results, indent=2)

            agent_prompt = """You are the RubyGemDB Gem Assistant and Architect. You answer questions about Ruby gems: what a gem does, whether it fits the user's project, how two gems compare, or what its risks are. You do NOT design implementation backlogs or scaffold code.

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

    # -- LLM & retrieval helpers --------------------------------------------

    def _ensure_model(self) -> LiteLLMModel:
        """Lazily create the LiteLLM model shared by the agent."""
        if self.model is None:
            self.model = LiteLLMModel(
                model_id=settings.active_agent_model,
                api_key=settings.llm_api_key,
                max_tokens=settings.agent_max_tokens,
            )
        return self.model

    def _llm_json(self, system_prompt: str, user_prompt: str) -> dict | None:
        """One JSON-only LLM call; returns None on any failure."""
        with trace_generation(
            name="agent-llm-json",
            model=settings.active_agent_model,
            input={"messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]},
            metadata={"session_id": self.active_session_id},
        ) as json_span:
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
                res = parsed if isinstance(parsed, dict) else None
                json_span.update(output=res)
                return res
            except Exception as e:
                logger.warning(f"Agent LLM call failed: {e}")
                json_span.update(level="ERROR", status_message=str(e))
                return None

    def _vector_search(self, query: str, categories: list[str] | None, limit: int) -> list[dict]:
        """txtai similarity search over child chunks, deduplicated by parent gem."""
        safe_query = query.replace("'", "''")
        req_limit = max(1, int(limit))
        candidate_limit = max(req_limit * 4, 20)
        sql = (
            "SELECT id, name, category, layer, parent_text, source_code_uri, context7_id, score "
            "FROM txtai WHERE similar('" + safe_query + "')"
        )
        if categories:
            cat_list = ", ".join("'" + c.replace("'", "''") + "'" for c in categories)
            sql += f" AND category IN ({cat_list})"
        sql += f" LIMIT {candidate_limit}"

        seen_gems: dict[str, dict] = {}
        for res in self.embeddings.search(sql):
            if res.get("type") and res.get("type") != "gem":
                continue
            gem_name = res.get("name")
            if not gem_name:
                continue
            score = float(res.get("score", 0.0))
            if gem_name not in seen_gems:
                seen_gems[gem_name] = {
                    "name": gem_name,
                    "description": res.get("parent_text", ""),
                    "category": res.get("category"),
                    "layer": res.get("layer"),
                    "source_code_uri": res.get("source_code_uri"),
                    "context7_id": res.get("context7_id"),
                    "score": score,
                }
            elif score > seen_gems[gem_name]["score"]:
                seen_gems[gem_name]["score"] = score
                if res.get("parent_text"):
                    seen_gems[gem_name]["description"] = res.get("parent_text")

            if len(seen_gems) >= req_limit:
                break

        return list(seen_gems.values())[:req_limit]

    def _fetch_gem(self, name: str) -> dict | None:
        """Case-insensitive inventory lookup for named gems."""
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

    @property
    def graphrag_service(self) -> GraphRAGService:
        if self._graphrag_service is None:
            self._graphrag_service = GraphRAGService()
        return self._graphrag_service

    @graphrag_service.setter
    def graphrag_service(self, service: GraphRAGService) -> None:
        self._graphrag_service = service

    def bind_session(self, session_id: Optional[str] = None, create_if_missing: bool = True) -> RAGSession:
        """Bind agent to an active session, restoring active gems, intention, and chat history."""
        if session_id:
            session = self.session_storage.get_session(session_id)
            if not session:
                if create_if_missing:
                    session = self.session_storage.create_session(
                        session_id=session_id,
                        active_gems=self._graphrag_gems,
                        intention=self._graphrag_intention,
                    )
                else:
                    raise ValueError(f"Session '{session_id}' not found.")
        else:
            session = self.session_storage.create_session(
                active_gems=self._graphrag_gems,
                intention=self._graphrag_intention,
            )

        self.active_session_id = session.session_id
        if session.active_gems:
            self._graphrag_gems = list(session.active_gems)
        elif self._graphrag_gems:
            self.session_storage.update_session(self.active_session_id, active_gems=self._graphrag_gems)

        if session.intention:
            self._graphrag_intention = session.intention
        elif self._graphrag_intention:
            self.session_storage.update_session(self.active_session_id, intention=self._graphrag_intention)

        # Reload conversation turns into _chat_history
        self._chat_history.clear()
        messages = self.session_storage.get_messages(session.session_id)
        for msg in messages:
            if msg.role in ("user", "agent"):
                self._chat_history.append({
                    "role": msg.role,
                    "content": msg.content,
                    "timestamp": msg.timestamp,
                })

        return session

    def set_graphrag_context(
        self,
        gems: list[str],
        intention: str = "",
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> dict[str, Any]:
        """Pre-warm and build GraphRAG index for the selected gems and intention."""
        self._graphrag_gems = sorted(list({g.strip() for g in gems if g.strip()}))
        self._graphrag_intention = intention.strip()

        if self.active_session_id:
            self.session_storage.update_session(
                self.active_session_id,
                active_gems=self._graphrag_gems,
                intention=self._graphrag_intention,
            )

        cb: Optional[Callable[[str], None]]
        if progress_callback is not None or self.active_session_id:
            def wrapped_progress(msg: str) -> None:
                if self.active_session_id:
                    try:
                        self.session_storage.append_trace(self.active_session_id, msg)
                    except Exception:
                        pass
                if progress_callback:
                    progress_callback(msg)
            cb = wrapped_progress
        else:
            cb = None

        if not self._graphrag_gems:
            return {"gems": [], "intention": self._graphrag_intention, "nodes": 0, "edges": 0}

        data = self.graphrag_service.build_or_load_index(
            self._graphrag_gems, progress_callback=cb
        )
        return {
            "gems": self._graphrag_gems,
            "intention": self._graphrag_intention,
            "nodes": len(self.graphrag_service.nodes_by_id),
            "edges": len(data.get("edges", [])),
        }

    def chat(self, query: str, project_context: Optional[str] = None) -> str:
        """Grounded conversational query using contextual GraphRAG when gems are selected."""
        if not self.active_session_id:
            self.bind_session()

        assert self.active_session_id is not None
        # Persist user query to rag_messages
        self.session_storage.append_message(self.active_session_id, role="user", content=query)
        # Index user query into txtai embedding memory tagged with session_id
        self.index_memory(
            query,
            doc_type="session_turn",
            metadata={"session_id": self.active_session_id, "role": "user"},
        )

        with trace_agent(
            name="rag-chat-turn",
            session_id=self.active_session_id,
            input={
                "query": query,
                "project_context": project_context,
                "active_gems": self._graphrag_gems,
            },
            metadata={
                "active_gems": self._graphrag_gems,
                "intention": self._graphrag_intention,
                "project_context": project_context,
            },
            tags=["rubygemdb", "chat", "graphrag"] if self._graphrag_gems else ["rubygemdb", "chat"],
        ) as turn_span:
            if not self._graphrag_gems:
                full_query = f"[Target Project Context: {project_context}]\n{query}" if project_context else query
                ans = self.run(full_query)
                self._chat_history.append({"role": "user", "content": query})
                self._chat_history.append({"role": "agent", "content": ans})

                # Index response into txtai embedding memory tagged with session_id
                self.index_memory(
                    ans,
                    doc_type="session_turn",
                    metadata={"session_id": self.active_session_id, "role": "agent"},
                )
                # Persist agent response to rag_messages
                self.session_storage.append_message(
                    self.active_session_id,
                    role="agent",
                    content=ans,
                    retrieved_nodes=[],
                )
                turn_span.update(output={"response": ans})
                return ans

            # Retrieve 2-stage GraphRAG context
            retrieval = self.graphrag_service.retrieve(query, top_k=5)
            grounded_context = self.graphrag_service.format_grounded_context(
                retrieval, intention=self._graphrag_intention
            )
            citations = retrieval.get("citations", [])

            retrieved_node_ids: list[str] = []
            for n in retrieval.get("primary_nodes", []):
                if isinstance(n, dict) and "id" in n:
                    retrieved_node_ids.append(str(n["id"]))
                elif isinstance(n, str):
                    retrieved_node_ids.append(n)
            for n in retrieval.get("expanded_nodes", []):
                if isinstance(n, dict) and "id" in n:
                    retrieved_node_ids.append(str(n["id"]))
                elif isinstance(n, str):
                    retrieved_node_ids.append(n)

            system_prompt = (
                "You are the RubyGemDB Gem Architect and Assistant. You provide concrete, highly grounded "
                "architectural and implementation advice for Ruby gems.\n\n"
                "### Mandatory Grounding & Citation Rules:\n"
                "1. EXPLICIT CITATIONS: You MUST cite the source of every Ruby class, module, method, parameter, "
                "or verified pattern using its citation tag (e.g. `[C1]`, `[C2]`).\n"
                "2. STRICT ANTI-HALLUCINATION CONTRACT (POKA-YOKE):\n"
                "   - You may ONLY use Ruby classes, modules, methods, and options that explicitly appear in the Grounded Gem Knowledge citations.\n"
                "   - DO NOT invent classes or namespaces (e.g. do NOT invent `PragmaticSegmenter::Tokenizer` when only `PragmaticSegmenter::Segmenter` is cited).\n"
                "   - DO NOT invent options, delimiters, or parameters (e.g. do NOT invent `delimiters`, `chunk_type: :recursive`, or `max_size` if they are not listed in the verified signatures/parameters).\n"
                "3. HANDLING UNSUPPORTED CAPABILITIES:\n"
                "   - If the user's task requires functionality not natively provided by the cited gem APIs (e.g. recursive parent/child chunking or delimiter hierarchies when the gem only segments sentences):\n"
                "     a) Explicitly state that the gem does not support this capability out of the box.\n"
                "     b) Cite the exact methods the gem DOES provide (e.g. `PragmaticSegmenter::Segmenter#segment` [C1]).\n"
                "     c) Provide the missing algorithm or glue logic as clean, custom Ruby application code, clearly distinguishing custom code from gem API calls.\n"
                "4. COMPLETENESS:\n"
                "   - Provide complete, executable code solutions. Never truncate mid-implementation or end abruptly.\n"
                "5. CITATIONS SUMMARY:\n"
                "   - Conclude your answer with a brief '### Citations' section listing the citation tags you used and their gem/symbol."
            )

            user_content_parts = []
            if project_context:
                user_content_parts.append(f"[Target Project Context: {project_context}]")
            if grounded_context:
                user_content_parts.append(f"Grounded Gem Knowledge:\n{grounded_context}")
            user_content_parts.append(f"User Query:\n{query}")

            user_prompt = "\n\n".join(user_content_parts)

            model = self._ensure_model()
            messages = [{"role": "system", "content": system_prompt}]

            for turn in self._chat_history[-4:]:
                # Normalize internal domain role 'agent' to LLM provider role 'assistant'
                role = "assistant" if turn["role"] == "agent" else turn["role"]
                if role in ("user", "assistant"):
                    messages.append({"role": role, "content": turn["content"]})

            messages.append({"role": "user", "content": user_prompt})

            with trace_generation(
                name="synthesize-response",
                model=settings.active_agent_model,
                input={"messages": messages},
            ) as gen_span:
                try:
                    response = str(model(messages).content).strip()
                    gen_span.update(output=response)
                except Exception as e:
                    response = f"LLM generation failed: {e}"
                    gen_span.update(level="ERROR", status_message=str(e), output=response)

            self._chat_history.append({"role": "user", "content": query})
            self._chat_history.append({"role": "agent", "content": response})

            # Index response into txtai embedding memory tagged with session_id
            self.index_memory(
                response,
                doc_type="session_turn",
                metadata={"session_id": self.active_session_id, "role": "agent"},
            )
            # Persist agent response, citations, and retrieved node IDs to rag_messages
            self.session_storage.append_message(
                self.active_session_id,
                role="agent",
                content=response,
                retrieved_nodes=retrieved_node_ids,
                citations=citations,
            )
            turn_span.update(output={"response": response})
            return response

    def export_transcript(self, out_dir: Optional[Path] = None) -> tuple[Path, Path]:
        """Export the active GraphRAG chat conversation as markdown and JSON."""
        from datetime import datetime

        target_dir = out_dir or (settings.project_root / "output" / "chat_exports")
        target_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = f"graphrag_chat_{stamp}"
        md_path = target_dir / f"{prefix}.md"
        json_path = target_dir / f"{prefix}.json"

        if self.active_session_id:
            md_content, json_data = self.session_storage.export_transcript(self.active_session_id)
            md_path.write_text(md_content, encoding="utf-8")
            json_path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")
            return md_path, json_path

        # 1. Build Markdown
        md_lines = [
            "# RubyGemDB Contextual GraphRAG Transcript",
            "",
            f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"**Project Intention:** {self._graphrag_intention or 'None specified'}",
            f"**Indexed Gems:** {', '.join(self._graphrag_gems) or 'None'}",
            "",
            "---",
            "",
            "## Conversation",
            "",
        ]

        for msg in self._chat_history:
            role_title = "User" if msg["role"] == "user" else "Agent"
            md_lines.append(f"### {role_title}")
            md_lines.append(msg["content"])
            md_lines.append("")

        md_lines.extend([
            "---",
            "",
            "## Grounded Index Summary",
            f"- Total Nodes: {len(self.graphrag_service.nodes_by_id)}",
            f"- Total Edges: {len(self.graphrag_service.graph_data.get('edges', []))}",
            "",
        ])

        md_path.write_text("\n".join(md_lines), encoding="utf-8")

        # 2. Build JSON
        json_data = {
            "timestamp": datetime.now().isoformat(),
            "project_intention": self._graphrag_intention,
            "indexed_gems": self._graphrag_gems,
            "messages": self._chat_history,
            "index_summary": {
                "nodes_count": len(self.graphrag_service.nodes_by_id),
                "edges_count": len(self.graphrag_service.graph_data.get("edges", [])),
            },
        }
        json_path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")

        return md_path, json_path

    def clear_chat(self) -> None:
        """Clear the conversational chat history."""
        self._chat_history.clear()

    def run(self, query: str) -> str:
        """Run the txtai Agent to answer a query and manage memory window."""
        with trace_agent(
            name="agent-tool-calling-turn",
            session_id=self.active_session_id,
            input={"query": query},
            tags=["rubygemdb", "agent"],
        ) as run_span:
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
                    
            run_span.update(output={"response": str(response)})
            return response

if __name__ == "__main__":
    agent = TxtaiAgent()
    print("Agent ready. Testing a query...")
    response = agent.run("Find information about the 'rails' gem and check its context7 documentation.")
    print("Response:", response)
