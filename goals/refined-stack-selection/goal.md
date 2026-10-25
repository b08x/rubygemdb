# Goal: Refine Gem Stack Selection with Second-Pass Curation

## Articulated Goal
Refactor the `stack` command and `StackService` to replace the unconditional inclusion of all base seeds and preferred gems across all 21 categories with a two-stage hierarchical retrieval and refinement pipeline. The system filters candidate gems down to a cohesive, compact stack (5–12 gems) tailored specifically to the user's workflow query, with category density limits, contextual role descriptions, and offline fallback.

## Shared Understanding
- **Facts**: See [`facts.md`](file:///home/b08x/WorkspaceV3/rubygemdb/goals/refined-stack-selection/facts.md) for the agreed-upon outcomes and behaviors.

## Execution Plan
- **Plan**: See [`plan.md`](file:///home/b08x/WorkspaceV3/rubygemdb/goals/refined-stack-selection/plan.md) for the implementation steps, files touched, and verification checks.

## Done Condition
1. `rubygemdb stack <query>` produces a curated, compact stack of 5–12 relevant gems without dumping irrelevant categories (e.g. `jekyll`, `prawn`, `ruby-vips` for CLI/NLP queries).
2. Base seeds and preferred gems only enter the candidate pool if their category is relevant to the query.
3. Second-pass curation generates task-specific, contextual role descriptions (with LLM synthesis when available and local fallback when offline).
4. CLI supports `--max-gems` (default 10) to govern the stack density.
5. All automated unit and regression tests pass with zero ruff or mypy errors.
