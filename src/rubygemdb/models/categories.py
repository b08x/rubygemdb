"""Single source of truth for the RubyGemDB functional category taxonomy.

21 functional categories, each tagged with a stack layer (substrate,
plumbing, composition, quality) so search, retrieval, and the `stack`
command can present gems as a layered stack for back-end tooling design.

classifier.py, llm.py, tui.py, agent.py, and stack.py all import from here;
no duplicated category lists live anywhere else.
"""

from pydantic import BaseModel, Field
from typing import Dict, List

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
            "web", "framework",
        ],
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
        ],
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
            "thread-pool", "pool", "evented", "reactor",
        ],
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
            "queue", "workflow", "clockwork", "whenever", "cron",
        ],
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
            "ohm", "redis", "pg", "sqlite", "sqlite3", "mysql", "mariadb",
            "mongodb", "dynamodb", "neo4j", "couchdb", "cassandra", "memcache",
            "dalli", "shrine", "activestorage", "active_storage", "paperclip",
            "carrierwave", "orm", "database", "migration", "schema", "repo",
            "repository", "cache", "kv", "leveldb", "lmdb",
        ],
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
            "csv", "roo", "spreadsheet", "caxlsx", "axlsx", "xlsx", "excel",
            "pdf-reader", "pdf_reader", "hexapdf", "commonmarker", "kramdown",
            "redcarpet", "markdown", "md", "subtitle", "srt", "json", "yajl",
            "oj", "msgpack", "yaml", "toml", "tomlrb", "xml", "html", "rss",
            "atom", "feed", "mechanize", "scraping", "scraper", "crawl",
            "spider", "serialize", "deserialize", "parser", "parse", "unpack",
        ],
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
            "renderer", "generate", "writer", "exporter",
        ],
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
        ],
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
            "readline", "prompt", "spinner", "whirly",
        ],
        sub_categories={
            "cli_frameworks": ["thor", "gli", "drydock", "highline"],
            "terminal_ui": ["tty-prompt", "bubbletea", "cli-ui"],
            "terminal_styling": ["pastel", "paint", "colorize", "ansi_palette"],
            "terminal_output": ["terminal-table", "tty-progressbar", "table_tennis"],
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
        ],
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
        ],
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
            "activemodel", "active_model", "schematist", "hashie",
            "json-schema", "json_schemer", "validates", "validator",
            "validation", "schema", "contract", "strong-params", "typed",
            "struct", "form", "attribute",
        ],
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
        ],
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
            "inference", "neural", "ai", "genai",
        ],
        sub_categories={
            "llm_clients": ["ruby_llm", "ruby-openai", "rllama"],
            "agent_frameworks": ["dspy", "sublayer", "deepsearch-rb"],
            "embeddings_vector": ["chroma-db", "tokenizers"],
            "prompt_tooling": ["prompt_manager", "ruby_llm-template", "aigcm"],
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
            "mcp-server", "mcp-", "-mcp",
        ],
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
            "jaro", "similarity", "string-similarity", "bm25", "tf-idf",
            "nlp", "natural-language", "pragma", "pragmatic_segmenter",
            "segmenter", "tokenizer", "tokenize", "stemmer", "lemmatize",
            "stopwords", "ngram", "pos-tag", "wordnet", "spaCy", "text",
            "corpus", "lexical", "linguistics", "ir", "search", "indexer",
        ],
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
            "devise", "omniauth", "pundit", "cancan", "cancancan", "warden",
            "doorkeeper", "oauth", "oauth2", "openid", "jwt", "jose",
            "bcrypt", "pbkdf2", "argon2", "scrypt", "sha", "md5", "hmac",
            "ed25519", "rsa", "openssl", "rbnacl", "sodium", "crypto",
            "cipher", "encrypt", "encryption", "signature", "sign",
            "auth", "authentication", "authorization", "permission", "acl",
            "secret", "vault", "honeypot", "password",
        ],
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
            "vips", "minimagick", "mini_magick", "rmagick", "image",
            "image-processing", "fastimage", "ffmpeg", "streamio-ffmpeg",
            "video", "audio", "wavefile", "soundfile", "taglib", "sox",
            "aubio", "mp3", "ogg", "wav", "flac", "exif", "gimp", "pixel",
            "thumbnail", "resize", "crop", "photo",
        ],
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
            "glimmer", "libui", "tk", "gosu", "gtk", "fxruby", "shoes",
            "qt", "qml", "sdl", "opengl", "raylib", "game", "gosu-",
            "window", "widget", "desktop",
        ],
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
            "glbinding", "binary", "elf", "elftools", "machO",
        ],
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
            "bigdecimal", "securerandom", "dotenv", "core_ext", "core-ext",
            "extlib", "backports", "monkey", "hash_ext", "array_ext",
            "string_ext", "date", "time", "uuid", "version", "semantic",
            "utility", "utilities", "helpers", "toolbox", "misc", "stdlib",
        ],
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
