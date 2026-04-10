# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RubyGemDB is a Ruby gem analysis and classification tool that categorizes gems into architectural patterns using heuristics and LLM-based analysis. The system provides both command-line and interactive terminal UI (TUI) interfaces for exploring and understanding Ruby gem ecosystems.

## Architecture

### Core Classification System
- **8 Architectural Categories**: runtime_substrate, framework_integration, boundary_interface, application_capability, policy_enforcement, observability, developer_experience, build_delivery
- **Heuristic Analysis**: Pattern-based classification using gem names, dependencies, and metadata
- **LLM Fallback**: Devstral LLM integration for ambiguous cases (confidence < 0.7)
- **Confidence Scoring**: Risk assessment based on invasiveness, coupling, and abstraction leak potential

### Data Flow
1. CSV input → RubyGems API → Heuristic classification → LLM enhancement → YAML output
2. Caching layers for both API responses (`gem_cache.json`) and LLM responses (`llm_cache.json`)
3. Results cached in `classified_gems.json` for TUI persistence

### Key Components
- `gem_classifier.py`: CLI batch processor with progress tracking
- `gem_classifier_tui.py`: Interactive Textual-based TUI with gem explorer
- Robust API helpers with exponential backoff and retry logic
- Context7 integration for generating gem usage cheatsheets

## Common Development Tasks

### Running the CLI Classifier
```bash
python gem_classifier.py <gems.csv> [--out output_directory]
```

### Running the TUI Explorer
```bash
python gem_classifier_tui.py <gems.csv>
```

### Environment Setup
Required environment variables for full functionality:
```bash
export DEVSTRAL_API_KEY="your_key"        # For LLM classification
export CONTEXT7_API_KEY="your_key"        # For cheatsheet generation
```

### Input Data Format
CSV files must contain either:
- `name` column (primary) or `gem` column  
- Optional `group` column for development/test context

## Classification Logic

### Heuristic Patterns
- **Runtime Substrate**: Core libraries (active_support, dry-*, core_ext)
- **Framework Integration**: Rails ecosystem (railties, sidekiq, engines)
- **Boundary Interface**: External service connectors (http, faraday, aws, stripe)
- **Policy Enforcement**: Authentication/authorization (pundit, jwt, auth)
- **Observability**: Monitoring/logging (sentry, datadog, newrelic)
- **Developer Experience**: Development tools (rspec, rubocop, pry, tty-)

### Confidence Thresholds
- High confidence (>0.7): Use heuristic classification
- Low confidence (<0.7): Queue for LLM batch processing
- LLM confidence threshold: 0.6 to override heuristics

## TUI Interface Features

### Navigation
- Tab between gem list and details panels
- Filter by classification category
- Search gems by name
- Batch operations for reclassification

### Context7 Integration
- Generate usage cheatsheets for selected gems
- Saves to `/home/b08x/Workspace/Tools/cheatsheets/`
- Requires CONTEXT7_API_KEY environment variable

## Caching Strategy

### API Caching (`gem_cache.json`)
- Persistent cache for RubyGems API responses
- Automatic cache saving after successful requests
- Handles cache loading/saving with absolute paths

### LLM Caching (`llm_cache.json`) 
- SHA256-hashed prompt caching for LLM responses
- Prevents redundant API calls for identical classification prompts
- Temperature 0 ensures deterministic results

### Results Caching (`classified_gems.json`)
- Persists TUI state between sessions
- Enables incremental classification updates
- Supports bulk operations without re-processing

## Error Handling Patterns

### Robust API Calls
- Exponential backoff with jitter for rate limiting
- Configurable retry attempts (default: 5 for network, 3 for gems)
- Graceful degradation when APIs are unavailable

### Rate Limiting
- `RATE_LIMIT_DELAY = 0.1` seconds between RubyGems requests
- `LLM_RATE_DELAY = 0.2` seconds between LLM requests  
- `LLM_BATCH_SIZE = 10` for efficient bulk processing

## Development Dependencies

Python packages (install via pip):
- `requests` - HTTP client for RubyGems/LLM APIs
- `rich` - Terminal formatting and progress bars
- `textual` - TUI framework for gem_classifier_tui.py
- `pyyaml` - YAML output generation

## Output Structure

### YAML Files
Classification results saved as `{category}.yaml` in output directory:
```yaml
- name: gem_name
  classification:
    primary: category_name
    secondary: null
  role:
    description: "Gem description from RubyGems"
    attaches_to: "category_prefix"
  risks:
    invasiveness: 1-5
    coupling: 1-4  
    abstraction_leak: "low|medium|high"
  signals:
    rails: boolean
    external_io: boolean
    native_ext: boolean
```

## Debugging Tips

### API Issues
- Check `gem_cache.json` for cached responses
- Verify RubyGems API connectivity: `curl "https://rubygems.org/api/v1/gems/{name}.json"`
- Monitor rate limiting with request delays

### LLM Classification Problems  
- Review prompts in `llm_cache.json` using prompt hash
- Check Devstral endpoint configuration and API key
- Validate JSON parsing of LLM responses

### TUI Navigation Issues
- Ensure CSV has proper column headers (`name` or `gem`)
- Check file paths are absolute when loading caches
- Verify Textual event handling for key bindings