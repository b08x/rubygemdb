"""Single source of truth for the RubyGemDB functional category taxonomy.

21 functional categories, each tagged with a stack layer (substrate,
plumbing, composition, quality) so search, retrieval, and the `stack`
command can present gems as a layered stack for back-end tooling design.

Each category defines sub-categories providing fine-grained faceted
metadata (slug -> example gems) for exploration, search, and classification.

classifier.py, llm.py, tui.py, agent.py, and stack.py all import from here;
no duplicated category lists live anywhere else.
"""

from typing import Dict, List
from pydantic import BaseModel, Field

LAYERS: List[str] = ["substrate", "plumbing", "composition", "quality"]


class Category(BaseModel):
    slug: str
    label: str
    description: str
    layer: str
    keywords: List[str] = Field(default_factory=list)
    sub_categories: Dict[str, List[str]] = Field(default_factory=dict)
    # Curated stack seeds for back-end tooling design.
    base_gems: List[str] = Field(default_factory=list)
    default_invasiveness: int = 3
    default_abstraction_leak: str = "low"


CATEGORIES: List[Category] = [
    Category(
        slug="web_frameworks",
        label="Web Frameworks",
        description=(
            "Web application frameworks, routing libraries, Rack middleware, "
            "templating engines, and Rails frontend integrations. "
            "Examples: rails, sinatra, roda, rack, haml, turbo-rails."
        ),
        layer="composition",
        keywords=[
            "rails", "sinatra", "roda", "rack", "hanami", "padrino", "grape",
            "haml", "erb", "slim", "liquid", "turbo", "stimulus", "phlex",
            "actionpack", "actionview", "railties", "middleware", "router",
            "web", "framework", "sprockets", "importmap", "tailwindcss", "vite",
            "cssbundling", "jsbundling", "inertia", "sass", "pagy", "swagger-blocks",
            "graphql", "daru-view",
        ],
        sub_categories={
            "full_stack_frameworks": ["rails", "hanami", "padrino", "graphql"],
            "micro_frameworks": ["sinatra", "roda", "grape"],
            "rack_middleware": ["rack", "rack-cors", "rack-protection", "rack-attack"],
            "templating_engines": ["haml", "slim", "liquid", "phlex", "erb"],
            "frontend_assets": [
                "turbo-rails", "stimulus-rails", "sprockets-rails",
                "importmap-rails", "vite_rails", "tailwindcss-rails",
                "inertia_rails", "cssbundling-rails", "jsbundling-rails",
                "sass", "sprockets", "daru-view", "swagger-blocks", "pagy",
            ],
        },
        base_gems=["rack"],
        default_invasiveness=5,
        default_abstraction_leak="high",
    ),
    Category(
        slug="http_networking",
        label="HTTP & Networking",
        description=(
            "HTTP clients, third-party API SDKs, websockets, and low-level "
            "networking libraries. Examples: faraday, httparty, excon, "
            "aws-sdk-s3, google-apis-drive_v3."
        ),
        layer="plumbing",
        keywords=[
            "faraday", "httparty", "excon", "typhoeus", "patron", "curb",
            "rest-client", "http", "httpx", "net-http", "net-ftp", "net-imap",
            "net-smtp", "net-pop", "net-ssh", "websocket", "socket", "tcp",
            "ssl", "sdk", "stripe", "twilio", "github-api", "aws", "gcloud",
            "google-apis", "google-cloud", "azure", "api-client", "webhook",
            "down", "open-uri", "soap4r", "socksify", "thruster", "circuit_breaker",
            "geoip", "maxminddb", "google_drive", "googleauth", "stream_lines", "browser",
        ],
        sub_categories={
            "http_clients": [
                "faraday", "httparty", "excon", "httpx", "rest-client",
                "down", "net-http", "open-uri", "faraday-multipart",
            ],
            "api_sdks": [
                "aws-sdk-s3", "google-apis-drive_v3", "google-apis-customsearch_v1",
                "google-cloud-translate", "google-cloud-translate-v2", "stripe",
                "twilio", "octokit",
            ],
            "networking_protocols": [
                "net-ssh", "net-ssh-gateway", "socksify", "net-ping", "soap4r",
                "thruster", "circuit_breaker", "ratelimit", "browser",
            ],
            "remote_storage_transfer": [
                "google_drive", "googleauth", "stream_lines", "geoip", "maxminddb",
            ],
        },
        base_gems=["faraday"],
        default_invasiveness=2,
        default_abstraction_leak="low",
    ),
    Category(
        slug="servers_concurrency",
        label="Servers & Concurrency",
        description=(
            "Application servers, async/evented runtimes, thread and process "
            "pools, and subprocess control. Examples: puma, falcon, async, "
            "concurrent-ruby, childprocess."
        ),
        layer="substrate",
        keywords=[
            "puma", "falcon", "unicorn", "passenger", "thin", "rainbows",
            "async", "eventmachine", "nio4r", "concurrent", "celluloid",
            "childprocess", "subprocess", "process", "fork", "thread",
            "thread-pool", "pool", "evented", "reactor", "daemons", "open3",
            "open4", "parallel", "webrick", "rackup", "ractor", "sync",
            "sys-proctable", "timeout", "wdm",
        ],
        sub_categories={
            "application_servers": ["puma", "falcon", "unicorn", "passenger", "webrick", "rackup", "thin"],
            "async_evented": ["async", "eventmachine", "nio4r", "async-service", "async-cable", "async-http"],
            "concurrency_parallelism": ["concurrent-ruby", "parallel", "ractor-dispatch", "celluloid", "sync"],
            "process_supervision": ["childprocess", "daemons", "open3", "open4", "sys-proctable", "timeout", "wdm"],
        },
        base_gems=["concurrent-ruby"],
        default_invasiveness=3,
        default_abstraction_leak="medium",
    ),
    Category(
        slug="background_jobs",
        label="Background Jobs",
        description=(
            "Job queues and workflow runners. Examples: sidekiq, "
            "solid_queue, resque, gush, async-job."
        ),
        layer="plumbing",
        keywords=[
            "sidekiq", "solid_queue", "solid-queue", "resque", "delayed_job",
            "delayed-job", "good_job", "good-job", "sucker_punch", "que",
            "activejob", "active_job", "gush", "sidekiq-", "job", "worker",
            "queue", "workflow", "clockwork", "whenever", "cron", "async-job",
            "async-job-processor-redis", "jongleur", "tabula", "scheduler",
        ],
        sub_categories={
            "job_queues": [
                "sidekiq", "solid_queue", "resque", "delayed_job", "good_job",
                "sucker_punch", "async-job", "async-job-processor-redis", "que",
            ],
            "workflow_dag_orchestration": ["gush", "jongleur", "tabula"],
            "job_scheduling": ["whenever", "clockwork", "sidekiq-cron", "rufus-scheduler"],
        },
        base_gems=["sidekiq"],
        default_invasiveness=3,
        default_abstraction_leak="medium",
    ),
    Category(
        slug="persistence",
        label="Persistence",
        description=(
            "ORMs, database drivers, key-value stores, caches, and file "
            "attachment toolkits. Examples: activerecord, sequel, pg, sqlite3, "
            "redis, shrine."
        ),
        layer="plumbing",
        keywords=[
            "activerecord", "active_record", "sequel", "mongoid", "rom",
            "ohm", "redis", "pg", "sqlite", "sqlite3", "mysql", "mysql2",
            "mariadb", "mongodb", "dynamodb", "neo4j", "couchdb", "cassandra",
            "memcache", "dalli", "shrine", "activestorage", "active_storage",
            "paperclip", "carrierwave", "refile", "orm", "database", "migration",
            "schema", "repo", "repository", "cache", "kv", "leveldb", "lmdb",
            "pgvector", "madeleine", "redic", "sequel_pg",
        ],
        sub_categories={
            "relational_orms": ["activerecord", "sequel", "rom"],
            "database_drivers": ["pg", "sqlite3", "mysql2", "sequel_pg"],
            "key_value_caches": [
                "redis", "redis-client", "redic", "redis-namespace",
                "ohm", "ohm-contrib", "ohm-sorted", "dalli",
            ],
            "file_attachment_storage": ["shrine", "activestorage", "carrierwave", "refile", "paperclip"],
            "specialized_datastores": ["pgvector", "madeleine", "mongoid", "neo4j"],
        },
        base_gems=["sqlite3"],
        default_invasiveness=4,
        default_abstraction_leak="high",
    ),
    Category(
        slug="document_parsing",
        label="Document Parsing",
        description=(
            "Parsing structured formats (HTML, XML, Markdown, CSV, PDF, "
            "subtitles) and data serialization (JSON, YAML, TOML). "
            "Examples: nokogiri, rexml, csv, pdf-reader, commonmarker, "
            "yajl-ruby."
        ),
        layer="plumbing",
        keywords=[
            "nokogiri", "oga", "rexml", "loofah", "sanitize", "scrub",
            "csv", "csvreader", "roo", "spreadsheet", "caxlsx", "axlsx", "xlsx", "excel",
            "pdf-reader", "pdf_reader", "hexapdf", "commonmarker", "kramdown",
            "kramdown-parser-gfm", "redcarpet", "markdown", "md", "subtitle", "srt",
            "webvtt", "json", "yajl", "oj", "multi_json", "jsonl", "msgpack",
            "yaml", "psych", "toml", "tomlrb", "xml", "html", "rss", "atom",
            "feed", "mechanize", "scraping", "scraper", "crawl", "spider",
            "serialize", "deserialize", "parser", "parse", "unpack", "iniparse",
            "plist", "shale", "xml-simple", "racc", "syntax_tree", "tree_sitter",
            "regexp_parser", "rdf", "rdf-turtle", "rubyzip", "zip", "inkmark",
            "json_canvas", "kreuzberg", "poppler",
        ],
        sub_categories={
            "html_xml_parsing": ["nokogiri", "rexml", "loofah", "xml-simple", "oga"],
            "json_yaml_serialization": [
                "json", "oj", "multi_json", "yajl-ruby", "psych",
                "yaml", "jsonl", "tomlrb", "shale", "json_canvas",
            ],
            "csv_tabular_formats": ["csv", "csvreader", "roo", "spreadsheet", "caxlsx"],
            "markdown_prose_parsing": [
                "commonmarker", "kramdown", "kramdown-parser-gfm", "redcarpet",
                "inkmark", "front_matter_parser", "reverse_markdown",
            ],
            "code_ast_parsers": ["syntax_tree", "tree_sitter", "racc", "regexp_parser", "rsyntaxtree"],
            "structured_extractors": [
                "kreuzberg", "poppler", "srt", "webvtt", "plist",
                "iniparse", "rdf", "rdf-turtle",
            ],
            "archive_compression": ["rubyzip", "zip"],
        },
        base_gems=["nokogiri"],
        default_invasiveness=2,
        default_abstraction_leak="low",
    ),
    Category(
        slug="document_generation",
        label="Document Generation",
        description=(
            "Generating documents: PDFs, API docs, and syntax highlighting. "
            "Examples: prawn, pdfkit, wicked_pdf, asciidoctor-pdf, rouge."
        ),
        layer="plumbing",
        keywords=[
            "prawn", "pdfkit", "pdf", "wicked", "hexapdf", "asciidoctor-pdf",
            "rouge", "coderay", "pygments", "syntax-highlighting", "rdoc",
            "yard", "sdoc", "apipie", "slodown", "erb-render", "template",
            "renderer", "generate", "writer", "rdoc-rake",
            "yard-sorbet", "decode",
        ],
        sub_categories={
            "pdf_generation": ["prawn", "pdfkit", "wicked_pdf", "asciidoctor-pdf", "hexapdf"],
            "api_doc_generators": ["yard", "rdoc", "sdoc", "apipie", "yard-sorbet", "rdoc-rake", "decode"],
            "syntax_highlighting": ["rouge", "coderay", "pygments.rb"],
        },
        base_gems=["prawn"],
        default_invasiveness=2,
        default_abstraction_leak="low",
    ),
    Category(
        slug="static_site_generation",
        label="Static Site Generation",
        description=(
            "Static site generators and their themes/plugins. Examples: "
            "jekyll, cvless, hacked-jekyll, asciidoctor, beckett."
        ),
        layer="composition",
        keywords=[
            "jekyll", "middleman", "bridgetown", "hugo", "nanoc", "webgen",
            "asciidoctor", "site", "site-generator", "ssg", "blog", "theme",
            "cvless", "hacked-jekyll", "jemoji", "jekyll-feed",
            "jekyll-sitemap", "jekyll-seo-tag", "jekyll-",
        ],
        sub_categories={
            "site_generators": ["jekyll", "nanoc", "middleman", "bridgetown", "asciidoctor", "beckett"],
            "jekyll_plugins": [
                "jekyll-feed", "jekyll-sitemap", "jekyll-seo-tag", "jekyll-compose",
                "jekyll-paginate-v2", "jemoji", "jekyll-asciinema", "jekyll-toc",
                "jekyll-relative-links", "jekyll-figure", "jekyll-gist",
                "jekyll-include-cache", "jekyll-jupyter-notebook", "jekyll-last-modified-at",
                "jekyll-link-attributes", "jekyll-optional-front-matter", "jekyll-pandoc",
                "jekyll-postcss-v2", "jekyll-react-player", "jekyll-redirect-from",
                "jekyll-sass-converter", "jekyll-spaceship", "jekyll-tabs",
                "jekyll-tagging-related_posts", "jekyll-target-blank",
                "jekyll-titles-from-headings", "jekyll-typogrify", "jekyll_picture_tag",
            ],
            "site_themes": ["cvless", "hacked-jekyll", "jekyll-remote-theme"],
        },
        base_gems=["jekyll"],
        default_invasiveness=3,
        default_abstraction_leak="low",
    ),
    Category(
        slug="cli_libraries",
        label="CLI Libraries",
        description=(
            "CLI frameworks and terminal UI tools: command-line frameworks "
            "(thor, gli, drydock, highline), terminal UI (tty-prompt, "
            "bubbletea, cli-ui), terminal styling (pastel, paint, colorize, "
            "ansi_palette), and terminal output (terminal-table, "
            "tty-progressbar, table_tennis)."
        ),
        layer="composition",
        keywords=[
            "thor", "gli", "drydock", "highline", "commander", "clamp",
            "slop", "optimist", "tty", "cli-ui", "cli", "command-line",
            "optparse", "optionparser", "pastel", "paint", "colorize",
            "rainbow", "ansi", "terminal-table", "terminal", "progressbar",
            "progress-bar", "table_tennis", "bubbletea", "curses", "ncurses",
            "readline", "prompt", "spinner", "whirly", "bubblezone",
            "gum", "natty-ui", "rooibos", "lipgloss", "glamour", "harmonica",
            "ntcharts", "reline", "charm", "fastlane", "huh-ruby", "tocer",
            "rubyfca", "unicode-display_width", "tty-which",
        ],
        sub_categories={
            "cli_frameworks": [
                "thor", "gli", "drydock", "highline", "tty-option",
                "commander", "clamp", "slop", "rubyfca", "fastlane",
                "tty-which",
            ],
            "terminal_ui": [
                "tty-prompt", "bubbletea", "cli-ui", "bubbles",
                "bubblezone", "gum", "natty-ui", "rooibos", "huh-ruby",
                "curses", "charm", "rb-readline", "reline", "tty-reader", "tty-cursor",
            ],
            "terminal_styling": [
                "pastel", "paint", "colorize", "ansi_palette",
                "rainbow", "lipgloss", "glamour", "harmonica",
            ],
            "terminal_output": [
                "terminal-table", "tty-progressbar", "table_tennis",
                "ruby-progressbar", "progressbar", "ntcharts", "tty-box",
                "tty-table", "tty-tree", "tty-spinner", "unicode-display_width",
                "tty-pager", "tty-font", "tty-markdown", "tty-link", "tocer",
            ],
        },
        base_gems=["drydock", "thor"],
        default_invasiveness=1,
        default_abstraction_leak="low",
    ),
    Category(
        slug="testing_qa",
        label="Testing & QA",
        description=(
            "Test frameworks, matchers, factories, mocks/stubs, and browser "
            "test drivers. Examples: rspec, capybara, factory_bot, webmock, "
            "selenium-webdriver."
        ),
        layer="quality",
        keywords=[
            "rspec", "minitest", "test-unit", "capybara", "factory_bot",
            "factory-bot", "fabrication", "webmock", "vcr", "selenium",
            "watir", "cucumber", "shoulda", "timecop", "simplecov",
            "simplecov-", "rspec-", "mocha", "rr", "rspec-mocks", "stub",
            "mock", "fake", "test", "spec", "assert", "appraisal", "ci",
            "database_cleaner", "database-cleaner", "fakeredis", "faker",
            "maxitest", "parallel_tests", "parallel-tests", "tests", "test-prof",
            "webdrivers", "html-proofer", "rack-test", "factory_bot_rails",
        ],
        sub_categories={
            "test_frameworks": [
                "rspec", "rspec-core", "minitest", "cucumber-ruby",
                "test-unit", "maxitest",
            ],
            "mocking_stubbing": ["webmock", "vcr", "mocha", "timecop", "fakeredis", "rr", "rspec-mocks"],
            "fixtures_factories": ["factory_bot", "factory_bot_rails", "faker", "fabrication", "database_cleaner"],
            "browser_integration": ["capybara", "selenium-webdriver", "watir", "webdrivers", "rack-test", "html-proofer"],
            "test_tooling_reporting": [
                "simplecov", "test-prof", "parallel_tests", "rspec_junit_formatter",
                "minitest-reporters", "minitest-focus", "minitest-hooks", "minitest-rg",
                "shoulda", "shoulda-matchers", "shoulda-callback-matchers",
                "rspec-expectations", "rspec-github", "rspec-rails", "rspec-retry",
                "rspec-sorbet", "rswag-specs",
            ],
        },
        base_gems=["rspec"],
        default_invasiveness=1,
        default_abstraction_leak="low",
    ),
    Category(
        slug="code_quality_typing",
        label="Code Quality & Typing",
        description=(
            "Linters, static analyzers, type systems, and language servers. "
            "Examples: rubocop, brakeman, sorbet, rbs, ruby-lsp."
        ),
        layer="quality",
        keywords=[
            "rubocop", "rubocop-", "standard", "standardrb", "brakeman",
            "sorbet", "sorbet-", "rbs", "steep", "typeprof", "ruby-lsp",
            "solargraph", "reek", "flog", "flay", "fasterer", "debride",
            "lint", "linter", "analyzer", "static-analysis", "typing",
            "type-check", "language-server", "lsp", "cops", "style",
            "caliber", "lint_roller", "mdl", "spoom", "tapioca", "git-lint",
            "rubydex",
        ],
        sub_categories={
            "linters_formatters": [
                "rubocop", "standard", "standardrb", "mdl",
                "git-lint", "caliber", "lint_roller",
            ],
            "rubocop_extensions": [
                "rubocop-rails", "rubocop-rspec", "rubocop-performance",
                "rubocop-minitest", "rubocop-factory_bot", "rubocop-thread_safety",
                "rubocop-jekyll", "rubocop-packaging", "rubocop-ast", "rubocop-md",
                "rubocop-rails-omakase", "rubocop-rake", "rubocop-sequel",
                "rubocop-shopify", "rubocop-sorbet",
            ],
            "type_checkers": [
                "sorbet-runtime", "sorbet-static-and-runtime", "rbs",
                "steep", "spoom", "tapioca", "rbs-inline",
            ],
            "language_servers": ["ruby-lsp", "solargraph"],
            "security_static_analysis": ["brakeman", "rubydex"],
        },
        base_gems=["rubocop"],
        default_invasiveness=2,
        default_abstraction_leak="low",
    ),
    Category(
        slug="runtime_validation",
        label="Runtime Validation",
        description=(
            "Runtime validation, schemas, and typed structs. Examples: "
            "dry-schema, dry-types, activemodel, schematist, hashie."
        ),
        layer="composition",
        keywords=[
            "dry-schema", "dry-validation", "dry-types", "dry-struct",
            "dry-configurable", "dry-container", "dry-auto_inject",
            "activemodel", "active_model", "schematist", "hashie",
            "json-schema", "json_schemer", "validates", "validator",
            "validation", "schema", "contract", "strong-params", "typed",
            "struct", "form", "attribute", "i18n",
        ],
        sub_categories={
            "schema_validation": ["dry-schema", "dry-validation", "json-schema", "json_schemer", "schematist"],
            "type_containers_structs": [
                "dry-struct", "dry-types", "dry-container",
                "dry-auto_inject", "dry-configurable",
            ],
            "object_mapping_attributes": ["hashie", "activemodel", "i18n"],
        },
        base_gems=["dry-schema"],
        default_invasiveness=2,
        default_abstraction_leak="low",
    ),
    Category(
        slug="developer_tools",
        label="Developer Tools",
        description=(
            "Debuggers, profilers, REPLs, boot speedup, logging, and "
            "gem/bundler tooling. Examples: pry, debug, stackprof, vernier, "
            "bundler-audit, journald-logger."
        ),
        layer="quality",
        keywords=[
            "pry", "byebug", "debug", "debase", "ruby-debug", "stackprof",
            "ruby-prof", "memory_profiler", "vernier", "rbs-", "spring",
            "bootsnap", "bundler-audit", "bundler", "gem-", "gem_open",
            "logger", "log", "logging", "journald", "syslog", "sentry",
            "datadog", "newrelic", "new_relic", "honeybadger", "bugsnag",
            "airbrake", "rollbar", "opentelemetry", "profiler", "profiling",
            "tracer", "benchmark", "irb", "ripl", "repl", "console",
            "backtrace", "error-tracking", "monitoring", "apm", "telemetry",
            "amazing_print", "awesome_print", "better_errors", "binding_of_caller",
            "clipboard", "debug_me", "dry-logger", "gem-skill", "git", "iruby",
            "letter_opener", "listen", "overcommit", "web-console", "uglifier",
            "kamal", "terrapin", "jsonlint", "warning", "warnings", "opentelemetry-exporter",
            "deploy", "docker", "container",
        ],
        sub_categories={
            "debugging_repl": [
                "pry", "debug", "irb", "byebug", "binding_of_caller",
                "amazing_print", "awesome_print", "web-console", "debug_me",
                "pry-byebug", "pry-doc", "pry-rails", "pry-stack_explorer",
                "pry-bond", "irb-kit", "iruby",
            ],
            "profiling_benchmarks": [
                "stackprof", "vernier", "ruby-prof", "benchmark", "memory_profiler",
            ],
            "logging_telemetry": [
                "journald-logger", "logger", "logging", "dry-logger",
                "opentelemetry-sdk", "opentelemetry-exporter-otlp", "tty-logger",
            ],
            "development_workflow": [
                "listen", "bundler-audit", "overcommit", "gem-skill",
                "clipboard", "better_errors", "letter_opener", "kamal",
                "uglifier", "git", "terrapin", "io-console", "warning",
                "tty-command", "tty-config", "tty-editor", "tty-exit",
                "tty-file", "tty-which", "jsonlint",
            ],
        },
        base_gems=["pry", "journald-logger"],
        default_invasiveness=1,
        default_abstraction_leak="low",
    ),
    Category(
        slug="ai_llm",
        label="AI & LLM",
        description=(
            "LLM and AI tooling: LLM clients (ruby_llm, ruby-openai, "
            "rllama), agent frameworks (dspy, sublayer, deepsearch-rb), "
            "embeddings/vector stores (chroma-db, tokenizers), and prompt "
            "tooling (prompt_manager, ruby_llm-template, aigcm)."
        ),
        layer="composition",
        keywords=[
            "llm", "ruby_llm", "ruby-llm", "openai", "anthropic", "mistral",
            "gpt", "claude", "gemini", "dspy", "langchain", "sublayer",
            "deepsearch", "agent", "agentic", "prompt", "embedding",
            "embed", "vector", "vectorsearch", "chroma", "chroma-db",
            "tokenizers", "tiktoken", "transformers", "onnx", "torch",
            "tensorflow", "whisper", "rllama", "rag", "completion",
            "inference", "neural", "ai", "genai", "hugging-face", "huggingface",
            "nexo", "nexo_ai", "aia", "aigcm", "ai_platform", "google-cloud-ai_platform",
            "vertex", "neighbor",
        ],
        sub_categories={
            "llm_clients": [
                "ruby_llm", "ruby-openai", "rllama", "google-cloud-ai_platform-v1",
                "hugging-face", "ruby_llm-claude_cli", "ruby_llm-typesafe", "shared_tools",
            ],
            "agent_frameworks": [
                "dspy", "sublayer", "deepsearch-rb", "nexo_ai", "dspy-code_act",
                "dspy-deep_research", "dspy-deep_search", "aia", "ruby_decision_model",
            ],
            "embeddings_vector": [
                "chroma-db", "tokenizers", "neighbor", "ruby_llm-tokenizer",
                "informers", "rubyllm-semantic_router", "tomoto",
            ],
            "prompt_tooling": [
                "prompt_manager", "ruby_llm-template", "aigcm", "ruby_llm-contract",
                "ruby_llm-instructor", "ruby_llm-tribunal", "agent-context",
                "llm-docs-builder", "ruby_llm-ai_sdk", "ruby_llm-docker",
                "ruby_llm-skills", "ruby_llm-top_secret", "dspy-evals",
                "dspy-gemini", "dspy-gepa", "dspy-o11y-langfuse", "dspy-openai",
                "dspy-ruby_llm", "dspy-schema", "opentelemetry-instrumentation-ruby_llm",
            ],
        },
        base_gems=["ruby_llm"],
        default_invasiveness=3,
        default_abstraction_leak="medium",
    ),
    Category(
        slug="mcp_tooling",
        label="MCP Tooling",
        description=(
            "MCP (Model Context Protocol) clients and servers. Examples: "
            "ruby-mcp-client, ruby_llm-mcp."
        ),
        layer="composition",
        keywords=[
            "mcp", "model-context-protocol", "fast-mcp", "mcp-client",
            "mcp-server", "mcp-", "-mcp", "ruby-mcp-client", "ruby_llm-mcp",
        ],
        sub_categories={
            "mcp_servers": ["fast-mcp"],
            "mcp_clients": ["ruby-mcp-client", "ruby_llm-mcp"],
        },
        base_gems=["fast-mcp"],
        default_invasiveness=2,
        default_abstraction_leak="low",
    ),
    Category(
        slug="text_search",
        label="Text & Search",
        description=(
            "Classical NLP, fuzzy matching, information retrieval, and "
            "search engine integrations. Examples: pragmatic_segmenter, "
            "amatch, elasticsearch, searchkick, bm25f."
        ),
        layer="plumbing",
        keywords=[
            "elasticsearch", "searchkick", "chewy", "meilisearch",
            "typesense", "pg_search", "ransack", "thinking-sphinx", "sunspot",
            "sphinx", "solr", "fuzzy", "fuzz", "amatch", "levenshtein",
            "jaro", "similarity", "string-similarity", "bm25", "bm25f", "tf-idf",
            "tf-idf-similarity", "nlp", "natural-language", "pragma",
            "pragmatic_segmenter", "segmenter", "tokenizer", "tokenize",
            "pragmatic_tokenizer", "stemmer", "lemmatize", "stopwords",
            "stopwords-filter", "ngram", "pos-tag", "engtagger", "wordnet",
            "rwordnet", "wordnet-defaultdb", "spaCy", "ruby-spacy", "text",
            "corpus", "lexical", "linguistics", "ir", "search", "indexer",
            "scalpel", "lingua", "linkparser", "chronic", "hotwater",
            "tomoto", "topic-modeling",
        ],
        sub_categories={
            "nlp_tokenization": [
                "pragmatic_segmenter", "pragmatic_tokenizer", "scalpel",
                "engtagger", "lingua", "linkparser", "ruby-spacy", "chronic",
                "tomoto",
            ],
            "string_similarity_fuzzy": [
                "amatch", "fuzzy-string-match", "fuzzy_tools", "text", "hotwater",
            ],
            "search_ranking_retrieval": [
                "bm25f", "tf-idf-similarity", "pg_search", "stopwords-filter",
                "elasticsearch", "searchkick", "google_custom_search_api",
                "google_search_results",
            ],
            "lexical_knowledge_bases": ["wordnet", "rwordnet", "wordnet-defaultdb"],
        },
        base_gems=["amatch"],
        default_invasiveness=3,
        default_abstraction_leak="medium",
    ),
    Category(
        slug="security_auth",
        label="Security & Auth",
        description=(
            "Authentication/authorization and cryptography/encryption. "
            "Examples: devise, pundit, bcrypt_pbkdf, ed25519, "
            "symmetric-encryption."
        ),
        layer="composition",
        keywords=[
            "devise", "omniauth", "omniauth-google-oauth2", "omniauth-rails_csrf_protection",
            "pundit", "cancan", "cancancan", "warden", "doorkeeper", "oauth",
            "oauth2", "openid", "jwt", "jose", "bcrypt", "pbkdf2", "bcrypt_pbkdf",
            "argon2", "scrypt", "sha", "md5", "hmac", "ed25519", "rsa",
            "openssl", "rbnacl", "sodium", "crypto", "cipher", "encrypt",
            "encryption", "signature", "sign", "auth", "authentication",
            "authorization", "permission", "acl", "secret", "vault",
            "honeypot", "password", "ruby-rc4", "symmetric-encryption",
        ],
        sub_categories={
            "authentication_oauth": [
                "devise", "omniauth", "omniauth-google-oauth2",
                "omniauth-rails_csrf_protection", "warden", "doorkeeper",
            ],
            "authorization_policies": ["pundit", "cancan", "cancancan"],
            "cryptography_encryption": [
                "bcrypt", "bcrypt_pbkdf", "ed25519", "openssl",
                "ruby-rc4", "symmetric-encryption",
            ],
        },
        base_gems=["bcrypt"],
        default_invasiveness=4,
        default_abstraction_leak="medium",
    ),
    Category(
        slug="media_processing",
        label="Media Processing",
        description=(
            "Image, audio, and video processing. Examples: ruby-vips, "
            "wavefile, taglib-ruby, ruby-sox, aubio."
        ),
        layer="plumbing",
        keywords=[
            "vips", "ruby-vips", "minimagick", "mini_magick", "rmagick",
            "image", "image-processing", "image_processing", "fastimage",
            "ffmpeg", "streamio-ffmpeg", "video", "audio", "wavefile",
            "wav-file", "soundfile", "taglib", "taglib-ruby", "sox",
            "ruby-sox", "aubio", "mp3", "mp3info", "ogg", "wav", "flac",
            "exif", "pixel", "thumbnail", "resize", "crop", "photo", "color", "coltrane",
        ],
        sub_categories={
            "image_processing": ["ruby-vips", "image_processing", "mini_magick", "color", "fastimage"],
            "audio_music_processing": [
                "wavefile", "wav-file", "ruby-sox", "taglib-ruby",
                "mp3info", "aubio", "coltrane",
            ],
            "video_transcoding": ["streamio-ffmpeg"],
        },
        base_gems=["ruby-vips"],
        default_invasiveness=2,
        default_abstraction_leak="low",
    ),
    Category(
        slug="gui_desktop",
        label="GUI & Desktop",
        description=(
            "Desktop GUI frameworks and game development libraries. "
            "Examples: glimmer-dsl-libui, tk, gosu."
        ),
        layer="composition",
        keywords=[
            "glimmer", "glimmer-dsl-libui", "libui", "tk", "gosu", "gtk",
            "fxruby", "shoes", "qt", "qml", "sdl", "opengl", "raylib",
            "game", "gosu-", "window", "widget", "desktop", "i3ipc",
        ],
        sub_categories={
            "desktop_gui": ["glimmer", "glimmer-dsl-libui", "tk", "gtk", "shoes", "fxruby", "i3ipc"],
            "game_graphics_2d": ["gosu", "raylib", "sdl", "opengl"],
        },
        base_gems=["glimmer-dsl-libui"],
        default_invasiveness=3,
        default_abstraction_leak="medium",
    ),
    Category(
        slug="native_bindings",
        label="Native Bindings",
        description=(
            "FFI, C extension toolchains, and binary file formats. "
            "Examples: ffi, fiddle, rake-compiler, ruby-macho."
        ),
        layer="substrate",
        keywords=[
            "ffi", "fiddle", "rake-compiler", "rake-compiler-dock",
            "ruby-macho", "macho", "bindata", "cext", "native", "extension",
            "glbinding", "binary", "elf", "patchelf", "elftools", "machO",
            "libarchive-ruby-swig", "mini_portile", "numpy", "pycall", "rugged", "evdev",
        ],
        sub_categories={
            "ffi_interop": ["ffi", "fiddle", "pycall", "numpy", "evdev"],
            "compilation_packaging": ["rake-compiler", "rake-compiler-dock", "mini_portile"],
            "binary_executable_tools": ["ruby-macho", "patchelf", "rugged", "libarchive-ruby-swig", "bindata"],
        },
        base_gems=["ffi"],
        default_invasiveness=3,
        default_abstraction_leak="medium",
    ),
    Category(
        slug="core_extensions",
        label="Core Extensions",
        description=(
            "Core language extensions and stdlib-style utilities. "
            "Examples: activesupport, facets, refinements, bigdecimal, "
            "securerandom, dotenv."
        ),
        layer="substrate",
        keywords=[
            "activesupport", "active_support", "facets", "refinements",
            "bigdecimal", "securerandom", "dotenv", "dotenv-rails", "core_ext",
            "core-ext", "extlib", "backports", "monkey", "hash_ext", "array_ext",
            "string_ext", "date", "time", "uuid", "version", "semantic",
            "utility", "utilities", "helpers", "toolbox", "misc", "stdlib",
            "algorithms", "dry-monads", "fileutils", "finishing_moves",
            "hashery", "mutex_m", "os", "ostruct", "polyglot", "stringio",
            "zeitwerk", "countries", "tzinfo-data", "base64", "fractional",
            "gemoji", "runt", "hocon", "parseconfig", "daru",
        ],
        sub_categories={
            "language_core_extensions": ["activesupport", "facets", "refinements", "finishing_moves"],
            "data_structures_collections": ["algorithms", "hashery", "ostruct", "dry-monads", "daru"],
            "environment_system_utilities": [
                "dotenv", "dotenv-rails", "os", "fileutils",
                "zeitwerk", "polyglot", "stringio", "mutex_m",
            ],
            "data_standards_reference": [
                "countries", "tzinfo-data", "bigdecimal", "securerandom",
                "base64", "fractional", "gemoji", "hocon", "parseconfig", "runt", "osc-ruby",
            ],
        },
        base_gems=["dotenv", "activesupport"],
        default_invasiveness=3,
        default_abstraction_leak="medium",
    ),
]


VALID_CATEGORIES: List[str] = [c.slug for c in CATEGORIES]

CATEGORY_BY_SLUG: Dict[str, Category] = {c.slug: c for c in CATEGORIES}


def category_for(slug: str) -> Category:
    """Return the Category for a slug, raising KeyError for unknown slugs."""
    return CATEGORY_BY_SLUG[slug]
