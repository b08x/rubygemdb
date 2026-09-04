---
name: rubygems-consult
description: >-
  Use this skill when the user asks for recommendations on gems or wants to put together a Gemfile or gemspec based on a query (e.g., "what gems would be best for..."). It involves using a subagent to research, query the RubyGemDB SQLite database, invoke Context7 and DeepWiki MCP tools, and compile a final set of dependencies.
---

# rubygems-consult

This skill guides you through the process of researching and assembling a tailored Gemfile or gemspec based on a user's requirements.

## Workflow Instructions

1.  **Initialize the Scratchpad**:
    Create a file named `research_scratchpad.md` in the user's current workspace directory if it doesn't exist. This file will be used to track findings, potential gems, and justifications.

2.  **Delegate to a Research Subagent**:
    Invoke a subagent using the `invoke_subagent` tool. Assign it the `research` role. Provide the following instructions to the subagent:
    *   **Goal**: Research and identify the best Ruby gems to satisfy the user's query.
    *   **Database Querying**: Run direct SQL queries against `data/rubygemdb.sqlite` (using the `sqlite3` CLI tool via bash) to find relevant classified gems, categories, or overlaps with the user's domain.
    *   **Context7 MCP**: Use the `context7` MCP server (tools: `resolve-library-id`, `query-docs`) to retrieve deep context, API features, and cheatsheets for promising gems found in the database.
    *   **DeepWiki/Web Search**: Query the `deepwikimcp` MCP server to gather broader domain knowledge (e.g., specific algorithms, domain concepts). If `deepwikimcp` is unavailable or fails, fallback to using the `search_web` tool.
    *   **Documentation**: Continuously append your findings to `research_scratchpad.md`. Include the gem names, sources, pros/cons, and how they address specific parts of the user's query.

3.  **Wait for Subagent Completion**:
    Monitor the subagent. When the subagent reports that its research is complete, read the contents of `research_scratchpad.md`.

4.  **Synthesize the Gemfile / Gemspec**:
    Based on the compiled research, generate the final `Gemfile` or `gemspec`. Group gems logically (e.g., by architectural category defined in RubyGemDB, such as `runtime_spine`, `data_processing`, `ai_nlp`).
    *   Provide comments for each gem explaining its role and why it was selected over alternatives.
    *   Ensure any specific version constraints or configuration notes discovered during research are included.

5.  **Present to the User**:
    Output the constructed Gemfile or gemspec. Briefly summarize the research process and ask the user if they'd like to refine the selection or proceed with installing the gems.
