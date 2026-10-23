# Facts

- `rubygemdb-mcp` is registered as an executable CLI entrypoint in pyproject.toml.
- Running `rubygemdb-mcp` without arguments runs an MCP server communicating over standard input/output (stdio).
- Running `rubygemdb-mcp --daemon start` (or `rubygemdb-mcp --sse`) starts an MCP SSE server in the background, writing logs to data/mcp.log and saving a PID file.
- `rubygemdb-mcp --daemon status` and `rubygemdb-mcp --daemon stop` inspect and terminate the background server process cleanly.
- The MCP server exposes a `search_gems` tool supporting query search with category, phase, and limit filtering.
- The MCP server exposes a `get_gem` tool returning complete architectural metadata, layer, sub-categories, agent description, dependencies, and risks for a named gem.
- The MCP server exposes a `compose_stack` tool generating a layered gem stack manifest and Gemfile snippet for a context query.
- The MCP server exposes a `list_categories` tool returning the 21 architectural categories, stack layers, and curated base gems.
- The MCP server exposes static resources `rubygemdb://taxonomy` and `rubygemdb://stack-seeds`.
- The MCP server exposes a `design-ruby-stack` prompt template guiding agents to formulate queries and layer back-end architectures.
- The MCP server initializes in under 1 second using SQLite FTS5 for instant search readiness, deferring txtai embedding loads without blocking server handshake.
- All MCP server tools operate strictly read-only on the SQLite database without risking data mutation.
- Automated tests in tests/test_mcp.py verify server startup, tool calls, resources, prompts, and daemon lifecycle commands.
