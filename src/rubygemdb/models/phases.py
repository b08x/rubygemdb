"""Build phase taxonomy for the advisor chat.

Four ordered phases — skeleton, input, processing, output — with a single
table mapping each of the 21 functional categories to exactly one phase.
The advisor session walks phases in this order, one at a time.
"""

PHASES = ["skeleton", "input", "processing", "output"]

PHASE_BY_CATEGORY: dict[str, str] = {
    # skeleton: the shape of the application and the tools that hold it up
    "cli_libraries": "skeleton",
    "web_frameworks": "skeleton",
    "gui_desktop": "skeleton",
    "servers_concurrency": "skeleton",
    "core_extensions": "skeleton",
    "developer_tools": "skeleton",
    "testing_qa": "skeleton",
    "code_quality_typing": "skeleton",
    # input: getting data and requests into the system
    "http_networking": "input",
    "document_parsing": "input",
    "native_bindings": "input",
    "security_auth": "input",
    "mcp_tooling": "input",
    # processing: transforming, validating, and moving data through
    "ai_llm": "processing",
    "text_search": "processing",
    "media_processing": "processing",
    "runtime_validation": "processing",
    "background_jobs": "processing",
    "persistence": "processing",
    # output: generating rendered artifacts
    "document_generation": "output",
    "static_site_generation": "output",
}


def phase_for(category_slug: str) -> str | None:
    """Return the build phase for a category slug, or None if unknown."""
    return PHASE_BY_CATEGORY.get(category_slug)


def categories_for(phase: str) -> list[str]:
    """Return the category slugs assigned to a phase, in taxonomy order."""
    return [slug for slug, ph in PHASE_BY_CATEGORY.items() if ph == phase]
