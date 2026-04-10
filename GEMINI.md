# RubyGemDB

RubyGemDB is a specialized tool for analyzing and classifying Ruby gems into architectural patterns. It leverages heuristic-based analysis (name, dependencies, and metadata) and LLM fallback (via Mistral AI/Devstral) to categorize gems, providing insights into their role and potential risks within an ecosystem.

## Project Overview

- **Architectural Categories**: `runtime_substrate`, `framework_integration`, `boundary_interface`, `application_capability`, `policy_enforcement`, `observability`, `developer_experience`, `build_delivery`.
- **Core Engine**: `gem_classifier.py` handles batch processing, RubyGems API integration, and heuristic/LLM classification logic.
- **Interactive UI**: `gem_classifier_tui.py` provides a Textual-based terminal UI for exploring classified gems and re-classifying them interactively.
- **Data persistence**: Results and API responses are cached locally to minimize network calls and API costs.

## Building and Running

### Environment Setup

1.  **Python Dependencies**:
    ```bash
    pip install requests rich textual pyyaml
    ```
2.  **API Keys (Required for full functionality)**:
    ```bash
    export DEVSTRAL_API_KEY="your_mistral_key" # For LLM classification
    export CONTEXT7_API_KEY="your_context7_key" # For cheatsheet generation in TUI
    ```

### Execution Commands

- **Batch Classifier (CLI)**:
  Processes a CSV of gem names and outputs YAML files categorized by architectural role.
  ```bash
  python gem_classifier.py gems-inventory.csv --out output/
  ```
- **Gem Explorer (TUI)**:
  Launches an interactive interface to browse and manage gem classifications.
  ```bash
  python gem_classifier_tui.py gems-inventory.csv
  ```

## Development Conventions

### Caching Strategy
- `gem_cache.json`: Caches raw responses from the RubyGems API.
- `llm_cache.json`: Caches prompts and responses from the LLM classifier ( Mistral/Devstral).
- `classified_gems.json`: Persists the state of classified gems specifically for the TUI.

### Classification Logic
- **Heuristics First**: Gems are first matched against known patterns (e.g., `active_support` -> `runtime_substrate`).
- **LLM Fallback**: If the heuristic confidence is below 0.7, the tool calls the LLM for a more nuanced analysis.
- **Confidence Threshold**: LLM results require a confidence > 0.6 to override initial heuristics.

### Key Data Files
- `data/gems-inventory.csv`: The primary input format (columns: `gem,version,category,description,homepage,context7_id`).
- `data/classified_gems.json`: Central repository of analyzed gems used by the TUI.
