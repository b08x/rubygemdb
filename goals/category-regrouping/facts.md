# Facts

- Category `web_frameworks` — web application frameworks, routing, Rack middleware, templating engines, and Rails frontend integrations. Examples: rails, sinatra, roda, rack, haml, turbo-rails.
- Category `http_networking` — HTTP clients, third-party API SDKs, websockets, low-level networking. Examples: faraday, httparty, excon, aws-sdk-s3, google-apis-drive_v3.
- Category `servers_concurrency` — app servers, async/evented runtimes, thread and process pools, subprocess control. Examples: puma, falcon, async, concurrent-ruby, childprocess.
- Category `background_jobs` — job queues and workflow runners. Examples: sidekiq, solid_queue, resque, gush, async-job.
- Category `persistence` — ORMs, database drivers, key-value stores, caches, file attachment toolkits. Examples: activerecord, sequel, pg, sqlite3, redis, shrine.
- Category `document_parsing` — parsing structured formats (HTML, XML, Markdown, CSV, PDF, subtitles) and data serialization (JSON, YAML, TOML). Examples: nokogiri, rexml, csv, pdf-reader, commonmarker, yajl-ruby.
- Category `document_generation` — generating documents: PDFs, API docs, syntax highlighting. Examples: prawn, pdfkit, wicked_pdf, asciidoctor-pdf, rouge.
- Category `static_site_generation` — static site generators and their themes/plugins. Examples: jekyll, cvless, hacked-jekyll, asciidoctor, beckett.
- Category `cli_libraries` — CLI frameworks and terminal UI tools with sub-categories: cli_frameworks (thor, gli, drydock, highline), terminal_ui (tty-prompt, bubbletea, cli-ui), terminal_styling (pastel, paint, colorize, ansi_palette), terminal_output (terminal-table, tty-progressbar, table_tennis).
- Category `testing_qa` — test frameworks, matchers, factories, mocks/stubs, browser test drivers. Examples: rspec, capybara, factory_bot, webmock, selenium-webdriver.
- Category `code_quality_typing` — linters, static analyzers, type systems, language servers. Examples: rubocop, brakeman, sorbet, rbs, ruby-lsp.
- Category `runtime_validation` — runtime validation, schemas, and typed structs. Examples: dry-schema, dry-types, activemodel, schematist, hashie.
- Category `developer_tools` — debuggers, profilers, REPLs, boot speedup, gem/bundler tooling. Examples: pry, debug, stackprof, vernier, bundler-audit.
- Category `ai_llm` — LLM and AI tooling with sub-categories: llm_clients (ruby_llm, ruby-openai, rllama), agent_frameworks (dspy, sublayer, deepsearch-rb), embeddings_vector (chroma-db, tokenizers), prompt_tooling (prompt_manager, ruby_llm-template, aigcm).
- Category `mcp_tooling` — MCP (Model Context Protocol) clients and servers. Examples: ruby-mcp-client, ruby_llm-mcp.
- Category `text_search` — classical NLP, fuzzy matching, information retrieval, and search engine integrations. Examples: pragmatic_segmenter, amatch, elasticsearch, searchkick, bm25f.
- Category `security_auth` — authentication/authorization and cryptography/encryption. Examples: devise, pundit, bcrypt_pbkdf, ed25519, symmetric-encryption.
- Category `media_processing` — image, audio, and video processing. Examples: ruby-vips, wavefile, taglib-ruby, ruby-sox, aubio.
- Category `native_bindings` — FFI, C extension toolchains, binary file formats. Examples: ffi, fiddle, rake-compiler, ruby-macho.
- Category `core_extensions` — core language extensions and stdlib-style utilities. Examples: activesupport, facets, refinements, bigdecimal, securerandom, dotenv.
- All categories are defined in one module (slug, human-readable label, short description, optional sub-categories) that classifier.py, llm.py, and tui.py all import; no duplicated category lists remain in the code.
- The old 12 slugs (runtime_spine, cli_terminal_ui, storage_persistence, async_networking_orchestration, ai_nlp, data_processing, retrieval_similarity_fuzzy, algorithms_knowledge_structures, validation_types, parsing_encoding, debugging_introspection, mcp_tooling) no longer appear anywhere under src/.
- Classification combines keyword/dependency rules with description-text scoring and txtai embedding similarity against category descriptions; the best-scoring category wins, and the existing LLM fallback still runs when heuristic confidence is below 0.7.
- A re-classification command wipes the classified_gems table, classified_gems.json, and the txtai index, then rebuilds all of them from the pruned inventory under the new taxonomy.
- After migration, SQLite, classified_gems.json, and the txtai index agree: same gem set as the pruned inventory, same new category slugs, no old taxonomy values anywhere.
- CLI batch mode writes one YAML file per new category slug into the --out directory.
- The TUI 'Edit Category' radio set lists the new categories rendered as readable labels, and manual edits persist to storage.
- Risk scoring (invasiveness, coupling, abstraction leak) is re-keyed to the new categories, and an unknown category falls back to default risk values without raising an error.
- A litmus test suite verifies: jekyll→static_site_generation, nokogiri→document_parsing, thor→cli_libraries, sidekiq→background_jobs, ruby_llm→ai_llm.
- AGENTS.md (and the other agent instruction files that list categories) documents the new taxonomy with the same slugs as the code.
- Category `gui_desktop` — desktop GUI frameworks and game development libraries. Examples: glimmer-dsl-libui, tk, gosu.
- Before re-classification, a prune report identifies overlapping/duplicate gems (e.g. standardrb vs standard) and stale gems with no release in 3+ years per RubyGems API metadata; the user approves the prune list before any inventory entries are removed.

## Added from plan-gate feedback

- Every category carries a layer (substrate, plumbing, composition, quality) so search and retrieval can present gems as a layered stack for back-end tooling design.
- The agent RAG search exposes layer metadata, and for back-end tooling queries the agent composes a recommended gem stack ordered base-layer-first, with common base picks like dotenv, drydock, pry, rubocop, and journald-logger (or the stdlib logger on non-systemd hosts).
- A rubygemdb stack command turns a context query into a layered stack manifest (layer, category, gem, role) plus a Gemfile snippet; deeper Rubysmith project generation is a follow-up goal.
