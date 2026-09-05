<div align="center">

# RubyGemDB

**Ruby gem classification and analysis through heuristic taxonomy and LLM-augmented semantic search**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.14+](https://img.shields.io/badge/python-3.14+-blue.svg)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/badge/uv-package--manager-a855f7.svg)](https://docs.astral.sh/uv/)

</div>

---

## Features

- **12-Category Taxonomy** — Classifies gems into architectural patterns (runtime_spine, storage_persistence, ai_nlp, etc.) with confidence scoring and sub-category detection from dependency analysis
- **Heuristic + LLM Hybrid** — Fast pattern matching for obvious cases, with Mistral-powered LLM fallback when heuristic confidence drops below 0.7
- **Semantic Vector Search** — txtai-powered embeddings with multi-query expansion and recency boosting for finding gems by natural language description
- **AI Agent with MCP Tools** — Context7 documentation retrieval, Codebase Memory integration, and Trackboi backlog distillation via Model Context Protocol
- **Interactive TUI** — Textual-based terminal UI for browsing, filtering, editing, and exporting classified gems with real-time classification
- **Batch CLI Pipeline** — Headless processing of gem inventories with metadata verification, classification, and YAML report generation
- **Risk Assessment** — Invasiveness, coupling, and abstraction leak scoring for architectural impact analysis

---

## Architecture

```mermaid
graph LR
    cli["CLI Processor\nsrc/rubygemdb/cli.py"]
    tui["TUI Explorer\nsrc/rubygemdb/ui/tui.py"]
    agent["AI Agent\nsrc/rubygemdb/agent.py"]
    core["Core Config\nsrc/rubygemdb/core/"]
    models["Data Models\nsrc/rubygemdb/models/"]
    services["Services\nsrc/rubygemdb/services/"]
    storage["Storage\nsrc/rubygemdb/storage/"]

    cli -->|"orchestrates pipeline"| services
    cli -->|"reads/writes"| storage
    tui -->|"displays/manages gems"| services
    tui -->|"reads/writes"| storage
    tui -->|"launches agent queries"| agent
    agent -->|"semantic search"| services
    agent -->|"indexes/retrieves"| storage
    services -->|"uses"| core
    services -->|"returns"| models
    storage -->|"persists"| models

    style cli fill:#89b4fa,stroke:#89b4fa,color:#1e1e2e
    style tui fill:#89b4fa,stroke:#89b4fa,color:#1e1e2e
    style agent fill:#89b4fa,stroke:#89b4fa,color:#1e1e2e
    style storage fill:#a6e3a1,stroke:#a6e3a1,color:#1e1e2e
```

## External Integrations

```mermaid
graph LR
    rubygems-api["RubyGems API\nrubygems.org/api/v1"]
    context7-api["Context7 API\ncontext7.com/api/v2"]
    mistral-api["Mistral LLM API\napi.mistral.ai/v1"]
    ollama["Ollama\nembeddinggemma model"]
    mcp-context7["Context7 MCP\n@upstash/context7-mcp"]
    mcp-cbm["Codebase Memory MCP\ncodebase-memory-mcp"]
    mcp-trackboi["Trackboi MCP\ntrackboi mcp"]

    rubygems-service["RubyGems Service"] -->|"fetches gem info"| rubygems-api
    context7-service["Context7 Service"] -->|"searches libraries"| context7-api
    llm-service["LLM Service"] -->|"classifies gems"| mistral-api
    agent["AI Agent"] -->|"generates embeddings"| ollama
    agent -->|"retrieves docs"| mcp-context7
    agent -->|"searches code"| mcp-cbm
    agent -->|"distills backlogs"| mcp-trackboi

    style rubygems-api fill:#585b70,stroke:#585b70,color:#cdd6f4
    style context7-api fill:#585b70,stroke:#585b70,color:#cdd6f4
    style mistral-api fill:#585b70,stroke:#585b70,color:#cdd6f4
    style ollama fill:#585b70,stroke:#585b70,color:#cdd6f4
    style mcp-context7 fill:#585b70,stroke:#585b70,color:#cdd6f4
    style mcp-cbm fill:#585b70,stroke:#585b70,color:#cdd6f4
    style mcp-trackboi fill:#585b70,stroke:#585b70,color:#cdd6f4
```

---

## Classification Taxonomy

### Primary Categories

| Category | Description | Risk Level |
|----------|-------------|------------|
| `runtime_spine` | Rails core, frameworks, Ruby extensions | Invasiveness: 5/5 |
| `cli_terminal_ui` | CLI frameworks, terminal UI tools | Invasiveness: 1/5 |
| `storage_persistence` | ORMs, DB adapters, file storage | Invasiveness: 4/5 |
| `async_networking_orchestration` | HTTP clients, messaging, job queues | Invasiveness: 4/5 |
| `ai_nlp` | AI/ML, NLP, LLM, embeddings | Invasiveness: 3/5 |
| `data_processing` | HTML/XML parsing, CSV, PDF, scraping | Invasiveness: 3/5 |
| `retrieval_similarity_fuzzy` | Search, fuzzy matching, indexing | Invasiveness: 3/5 |
| `algorithms_knowledge_structures` | Data structures, graphs, trees | Invasiveness: 2/5 |
| `validation_types` | Validation, type systems, schemas | Invasiveness: 2/5 |
| `parsing_encoding` | JSON, YAML, XML, MessagePack | Invasiveness: 2/5 |
| `debugging_introspection` | Debuggers, profilers, monitoring | Invasiveness: 1/5 |
| `mcp_tooling` | Model Context Protocol tools | Invasiveness: 1/5 |

### Sub-Category Detection

Automatic sub-category extraction from dependency names:

- `rails`, `sidekiq`, `background_jobs`, `activejob`, `server`
- `database`, `redis`, `search`, `auth`, `api`
- `serialization`, `spreadsheet`, `pdf`, `cloud`
- `monitoring`, `messaging`

---

## AI Agent Architecture

### Semantic Search Pipeline

The agent implements a multi-stage search process:

1. **Query Expansion** — "Other Steve" prompt architect rewrites queries into SFL-compliant semantic variations
2. **Vector Retrieval** — Hybrid search (dense + sparse) with 30% recency boost for updated gems
3. **Codebase Context** — Optional MCP integration with Codebase Memory for target project analysis
4. **Synthesis** — Generates pragmatic implementation backlogs with concrete file references

### MCP Tool Integration

| Tool | Purpose |
|------|---------|
| `search_memory_tool` | Primary semantic search across gems, chat history, and docs |
| `fetch_rubygems_info` | Fetch detailed gem metadata from RubyGems API |
| Context7 MCP | Retrieve library documentation and cheatsheets |
| Codebase Memory MCP | Search target project architecture |
| Trackboi MCP | Distill backlogs into task boards |

### Agent Prompt Structure

```
Pragmatic Intent → Context & Findings → Implementation Backlog → Risks & Trade-offs
```

Each backlog item includes:
- User Story (`As a <system>, I need <capability>`)
- Technical tasks with concrete file references
- Architectural integration points

---

## Development

### Project Structure

```
rubygemdb/
├── src/rubygemdb/
│   ├── cli.py              # CLI batch processor
│   ├── agent.py            # AI agent with txtai embeddings
│   ├── core/
│   │   └── config.py       # Pydantic Settings configuration
│   ├── models/
│   │   └── gem.py          # Pydantic data models
│   ├── services/
│   │   ├── classifier.py   # Heuristic + LLM classification
│   │   ├── rubygems.py     # RubyGems API client
│   │   ├── llm.py          # Mistral LLM client
│   │   └── context7.py     # Context7 API client
│   ├── storage/
│   │   ├── base.py         # Storage interface
│   │   ├── json_storage.py # JSON file storage
│   │   └── sqlite_storage.py # SQLite storage
│   └── ui/
│       └── tui.py          # Textual TUI application
├── tests/
│   ├── test_classifier.py
│   └── test_export.py
└── pyproject.toml
```

### Data Flow

```mermaid
graph TD
    csv["CSV Input\ndata/gems-inventory.csv"]
    rubygems-api["RubyGems API\nrubygems.org"]
    context7-api["Context7 API\ncontext7.com"]
    llm-api["Mistral LLM API\napi.mistral.ai"]
    classifier["Gem Classifier\nservices/classifier.py"]
    sqlite-db["SQLite DB\ndata/rubygemdb.sqlite"]
    yaml-output["YAML Reports\noutput/*.yaml"]
    txtai-index["txtai Index\ndata/txtai/"]

    csv -->|"gem names"| classifier
    rubygems-api -->|"gem metadata"| classifier
    classifier -->|"classification results"| sqlite-db
    classifier -->|"categorized gems"| yaml-output
    sqlite-db -->|"inventory data"| txtai-index
    context7-api -->|"library IDs"| sqlite-db
    llm-api -->|"agent descriptions"| classifier

    style csv fill:#89b4fa,stroke:#89b4fa,color:#1e1e2e
    style yaml-output fill:#89b4fa,stroke:#89b4fa,color:#1e1e2e
    style sqlite-db fill:#a6e3a1,stroke:#a6e3a1,color:#1e1e2e
    style txtai-index fill:#a6e3a1,stroke:#a6e3a1,color:#1e1e2e
    style rubygems-api fill:#585b70,stroke:#585b70,color:#cdd6f4
    style context7-api fill:#585b70,stroke:#585b70,color:#cdd6f4
    style llm-api fill:#585b70,stroke:#585b70,color:#cdd6f4
```

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## References

- [RubyGems API](https://rubygems.org/api/v1/gems/{name}.json)
- [Context7 API](https://context7.com/api/v2/)
- [txtai](https://neuml.github.io/txtai/)
- [Textual](https://textual.textualize.io/)
- [Pydantic](https://pydantic.dev/)
