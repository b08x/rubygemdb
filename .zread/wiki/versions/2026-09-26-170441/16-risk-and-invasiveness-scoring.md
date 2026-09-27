The Risk & Invasiveness Scoring subsystem is a decision-support mechanism that evaluates each classified gem across three independent dimensions — invasiveness, coupling, and abstraction leak — producing a quantitative risk profile that informs adoption decisions in agent-guided dependency management. The scoring lives entirely within the `GemClassifier.score_gem()` method and is computed late in the classification pipeline, after both heuristic and LLM-based classification have settled on a final category.

## Three Dimensions of Risk

The risk profile is captured by the `GemRisks` Pydantic model, a lean container with three integer/string fields designed for both human readability in the TUI and machine parsing in agent workflows. Each dimension measures a distinct facet of integration cost.

```python
class GemRisks(BaseModel):
    invasiveness: int = 1    # Depth of runtime integration (1–5)
    coupling: int = 1        # Dependency count impact (1–4)
    abstraction_leak: str = "low"   # How much the gem's patterns leak into app code
```

Sources: [gem.py](src/rubygemdb/models/gem.py#L15-L19)

| Dimension | Scale | Meaning |
|---|---|---|
| **Invasiveness** | 1–5 | How deeply the gem integrates into the application's runtime — from surface-level CLI tools (1) to framework-level boot and wiring (5) |
| **Coupling** | 1–4 | How many runtime dependencies the gem brings in — a proxy for the breadth of surface area introduced |
| **Abstraction Leak** | low / medium / high | The degree to which the gem forces its own architectural patterns, data structures, or lifecycle conventions onto consuming code |

These three dimensions are deliberately orthogonal. A gem can be highly invasive (e.g., a Rails engine that rewires the boot sequence) but have low coupling (few external deps), or conversely be low-invasiveness but high-coupling (a simple logger that pulls in a dozen transitive dependencies). The trio gives a nuanced profile rather than a single risk score.

Sources: [gem.py](src/rubygemdb/models/gem.py#L4-L19), [classifier.py](src/rubygemdb/services/classifier.py#L194-L222)

## Invasiveness: Mapping Category to Integration Depth

Invasiveness is the primary risk axis. It is derived from the gem's final primary category through an explicit lookup table hardcoded in `score_gem()`. The mapping reflects a first-principles judgment about how much each category of gem typically reaches into an application's runtime:

```python
invasiveness_map = {
    "runtime_spine": 5,                       # Core boot/wiring — highest risk
    "async_networking_orchestration": 4,      # HTTP, messaging, job queues
    "storage_persistence": 4,                 # ORMs, DB adapters
    "data_processing": 3,                     # HTML/XML parsing, CSV, PDF
    "ai_nlp": 3,                              # AI/ML, embeddings, LLM clients
    "retrieval_similarity_fuzzy": 3,          # Search, fuzzy matching
    "algorithms_knowledge_structures": 2,     # Data structures, graph, tree
    "validation_types": 2,                    # Validation, type systems
    "parsing_encoding": 2,                    # JSON, YAML, serializers
    "cli_terminal_ui": 1,                     # CLI frameworks — lowest risk
    "debugging_introspection": 1,             # Debuggers, profilers
    "mcp_tooling": 1,                         # MCP protocol tools
}
```

Sources: [classifier.py](src/rubygemdb/services/classifier.py#L195-L208)

The logic is straightforward: if the category key exists, use its value; otherwise default to `3` (mid-range). The default catches any edge cases where a custom or unknown category might slip through. The scoring is intentionally **not** additive — it is a deterministic function of the final category, meaning an LLM override of classification (see [LLM-Augmented Classification with Fallback](14-llm-augmented-classification-with-fallback)) also implicitly adjusts the invasiveness score.

The rationale behind the tier assignments warrants explanation:
- **Level 5** (`runtime_spine`) represents gems that sit at the very foundation of the application's boot sequence, affecting how classes load, how the object graph is wired, and how the runtime initializes. A Rails engine or Bundler extension can break an entire application if misconfigured.
- **Level 4** (`async_networking_orchestration`, `storage_persistence`) covers gems that open external connections — databases, message brokers, HTTP servers — creating socket-level integration points that introduce latency, connection pooling concerns, and failure modes that differ from in-process code.
- **Level 3** (`data_processing`, `ai_nlp`, `retrieval_similarity_fuzzy`) includes gems that are standalone in their processing but may have significant runtime overhead or external dependencies (e.g., Nokogiri's native C extension, an LLM client's HTTP calls).
- **Level 2** (`algorithms_knowledge_structures`, `validation_types`, `parsing_encoding`) covers gems that operate mostly on in-memory data structures with minimal side effects. Their integration risk is primarily about correctness and API stability.
- **Level 1** (`cli_terminal_ui`, `debugging_introspection`, `mcp_tooling`) is reserved for tools that are development-time or user-interface concerns with little to no runtime footprint in production — they are invoked explicitly and do not hook into the application's execution flow.

## Coupling: Quantifying Dependency Surface Area

Coupling is the only dimension that is computed programmatically rather than looked up from a static map. The formula is a simple linear function of the gem's dependency count:

```python
coupling = min(4, max(1, len(deps)//3 + 1))
```

Sources: [classifier.py](src/rubygemdb/services/classifier.py#L211)

This creates a tiered system where every three runtime dependencies increment the coupling score by one level, clamped to the 1–4 range:

| Dependencies | Coupling Score |
|---|---|
| 0–2 | 1 |
| 3–5 | 2 |
| 6–8 | 3 |
| 9+ | 4 |

The `//3` divisor is a pragmatic threshold chosen to distinguish gems that are near-leaf nodes (zero or one transitive dependency) from those that act as integration hubs (six or more dependencies). The `+1` offset ensures that even a gem with zero dependencies scores a baseline coupling of 1, never 0. The `max(1, ...)` guard prevents edge cases where the formula could produce zero for negative dependency counts (which shouldn't happen but is defensive), and `min(4, ...)` caps the score at 4 to keep the scale bounded.

The coupling score reflects **breadth of dependency surface area** rather than depth — it does not attempt to recursively count transitive dependencies. This is an intentional simplification; the runtime dependencies list passed to `score_gem` is the direct dependency list extracted from the RubyGems API response in the `heuristic_classify` method. Transitive dependencies are not fetched, partly because the RubyGems API does not expose them directly, and partly because keeping the computation O(n) on direct deps avoids an explosion of API calls.

Sources: [classifier.py](src/rubygemdb/services/classifier.py#L63-L64)

## Abstraction Leak: Architectural Pattern Penetration

The abstraction leak dimension captures how likely a gem is to force its own conventions, data structures, or lifecycle management into the consuming codebase. It is a three-level ordinal classification derived from another hardcoded lookup:

```python
leak_map = {
    "async_networking_orchestration": "high",
    "storage_persistence": "high",
    "runtime_spine": "medium",
    "ai_nlp": "medium",
    "data_processing": "medium",
}
leak = leak_map.get(primary_cat, "low")
```

Sources: [classifier.py](src/rubygemdb/services/classifier.py#L213-L220)

The classification reflects architectural judgment:
- **High** leak categories (`async_networking_orchestration`, `storage_persistence`) typically define the event loop model, connection pooling patterns, or query DSLs that permeate the application. A web framework like Puma or an ORM like ActiveRecord fundamentally shapes how developers write controllers and data access code.
- **Medium** leak categories (`runtime_spine`, `ai_nlp`, `data_processing`) introduce conventions that are significant but more localized — Rails' convention-over-configuration spreads through the entire app, while an NLP library's text processing pipeline is contained to specific modules.
- **Low** leak (the default for all other categories) covers gems whose abstractions are mostly self-contained. A CLI command parser, a JSON encoder, or a debugging gem interacts with the application at call boundaries rather than dictating its structure.

Any category not present in `leak_map` defaults to `"low"`, making the mapping intentionally conservative — only categories with strong architectural evidence for pattern leakage are elevated.

## Integration into the Classification Pipeline

The `score_gem` method is invoked as the penultimate step in the `classify` pipeline, after both heuristic rules and (optionally) the LLM have determined the final primary category:

```
classify()
  ├── heuristic_classify()       → provisional category, signals, deps
  ├── build_prompt() + call_llm() → optional category override
  ├── score_gem(final_category, deps) → GemRisks instance
  └── return GemEntry(risks=risks, ...)
```

Sources: [classifier.py](src/rubygemdb/services/classifier.py#L224-L256)

The critical design point is that **risk scoring operates on the final, resolved category** — after any LLM-driven category override. If the LLM overrides the heuristic classification (when heuristic confidence is below 0.7 and LLM confidence exceeds 0.6), the invasiveness and abstraction leak scores shift accordingly, since they both use `primary_cat` as their sole lookup key. This means the LLM's category judgment has downstream consequences on the risk profile, a coupling that is worth noting when evaluating LLM outputs.

The default `GemRisks()` factory (with invasiveness=1, coupling=1, abstraction_leak="low") is only used for the `risks` field default in `GemEntry` — in practice it is always overridden because `score_gem` runs unconditionally for every classified gem. The default exists for model backward compatibility and test convenience.

Sources: [gem.py](src/rubygemdb/models/gem.py#L25)

## Persistence, Display, and Export

The risk profile travels through the entire data pipeline, surviving in each storage and presentation layer with full fidelity.

**SQLite storage** serializes `GemRisks` as a JSON blob in the `classified_gems` table under a `risks TEXT` column. On load, it reconstructs the model via `GemRisks(**json.loads(row["risks"]))`, ensuring round-trip integrity. The JSON serialization is standard `model_dump()` — all three fields serialize naturally as an object with `invasiveness` (int), `coupling` (int), and `abstraction_leak` (str).

Sources: [sqlite_storage.py](src/rubygemdb/storage/sqlite_storage.py#L43-L46), [sqlite_storage.py](src/rubygemdb/storage/sqlite_storage.py#L133), [sqlite_storage.py](src/rubygemdb/storage/sqlite_storage.py#L206)

**JSON file storage** handles risks identically, since `GemEntry.model_dump()` includes the `risks` key. The fallback storage backend persists the full serialized `GemRisks` object as a nested JSON object within each gem entry.

Sources: [json_storage.py](src/rubygemdb/storage/json_storage.py#L18-L20)

**TUI display** renders the three scores in a dedicated `#details-risks` label within the `GemDetails` composite widget:

```python
risk_text = (
    f"Invasiveness: {gem.risks.invasiveness}/5\n"
    f"Coupling: {gem.risks.coupling}/4\n"
    f"Leak: {gem.risks.abstraction_leak}"
)
```

Sources: [tui.py](src/rubygemdb/ui/tui.py#L77-L81)

The TUI presents invasiveness `/5` and coupling `/4` as explicit fractions, giving the developer a quick visual sense of where each score falls relative to its maximum. The abstraction leak is shown as a human-readable string.

**Export formats** (Gemfile, CSV, JSON, Markdown) all include risk data through the serialized `GemEntry` structure. The YAML category files produced by the CLI pipeline also include the full `risks` object as a nested dictionary, making the risk profile available for CI pipelines, code review tools, and agent consumption.

Sources: [cli.py](src/rubygemdb/cli.py#L204-L218)

## Verification and Test Coverage

The scoring subsystem is tested through four dedicated test cases in `test_classifier.py`, each targeting a boundary condition:

| Test | Assertion | Rationale |
|---|---|---|
| `test_score_gem_runtime_spine_high_invasiveness` | `risks.invasiveness >= 4` | Verifies that the highest-risk category maps to a high score |
| `test_score_gem_mcp_tooling_low_invasiveness` | `risks.invasiveness <= 2` | Verifies that a low-risk category maps to a low score |
| `test_score_gem_cli_terminal_ui` | `risks.invasiveness >= 1` | Sanity check that scoring runs without error for a known category |
| `test_score_gem_async_networking` | `risks.invasiveness >= 1` | Same safety check for async category |

Sources: [test_classifier.py](src/rubygemdb/tests/test_classifier.py#L152-L170)

The coupling score is indirectly tested through the full `heuristic_classify` → `score_gem` path in the `classify` method, but there is no dedicated test for the coupling formula's edge conditions (0 deps, 3 deps, 9 deps) — a gap that could be addressed by adding parametrized tests with controlled dependency lists.

## Design Rationale and Trade-offs

The scoring system favors **simplicity and determinism** over machine-learned or LLM-driven risk assessment. Every design decision carries explicit trade-offs:

**Why not use the LLM for risk scoring?** The Mistral-powered LLM is already invoked for category classification and agent description generation. Adding risk scoring to the LLM prompt would increase latency, cost, and variability — a gem might get different risk scores across runs depending on LLM output. A deterministic lookup table guarantees reproducibility: the same gem, same category, same dependencies always produces the same risk profile. This is crucial for auditability in a dependency management context.

**Why three separate dimensions instead of a single composite score?** A single "risk score" would conflate fundamentally different concerns. A gem with invasiveness=5 but coupling=1 (a core framework with no external deps) is a very different adoption decision from one with invasiveness=1 but coupling=4 (a trivial utility that pulls in nine dependencies). The three dimensions allow an AI agent or human developer to weight each factor according to their project's tolerance: a microservices project might care more about coupling (surface area), while a monolithic Rails app cares more about invasiveness (boot sequence impact).

**Why is the invasiveness map not configurable?** The map is currently hardcoded in `score_gem()` with no configuration hook, meaning the risk model is baked into the source code. This keeps the scoring function pure (no external state) but means adjusting risk weightings requires a code change and redeployment. For the project's current scope as a single-user tool, this is acceptable; a future mult-tenant or enterprise version could externalize these maps to the settings system (see [Centralized Configuration with Pydantic-Settings](9-centralized-configuration-with-pydantic-settings)).

## Relationship to the Broader Classification System

Risk scoring is the terminal component of the Classification Engine trio, consuming the output of both the [Heuristic Rule-Based Classification](13-heuristic-rule-based-classification) and the [LLM-Augmented Classification with Fallback](14-llm-augmented-classification-with-fallback). It depends on the [12-Category Taxonomy System](15-12-category-taxonomy-system) for its category keys — any changes to the category list must be reflected in all three maps (`invasiveness_map`, `leak_map`, and the implicit default handler). The risk scores feed into downstream consumers including the [Textual-Based Interactive TUI Implementation](23-textual-based-interactive-tui-implementation) for visual review, and the [Gemfile, CSV, JSON & Markdown Export](24-gemfile-csv-json-and-markdown-export) for documentation and CI consumption.

The risk data also surfaces in the AI agent's decision-making context: the [Multi-Query Expansion with "Other Steve" Prompt](21-multi-query-expansion-with-other-steve-prompt) includes a "Risks & Trade-offs" section in its output, and the agent's system prompt references gem risk profiles when formulating dependency recommendations.

Sources: [agent.py](src/rubygemdb/agent.py#L286)

## Next Steps in Documentation

After understanding how risk scoring works, the natural progression is to see how risk profiles are consumed downstream. Continue with the [TUI Interactive Gem Explorer](5-tui-interactive-gem-explorer) to see scores in the visual interface, or jump to [Gemfile, CSV, JSON & Markdown Export](24-gemfile-csv-json-and-markdown-export) to understand how risk data appears in output formats.