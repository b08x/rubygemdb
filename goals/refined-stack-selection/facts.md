# Facts

- The stack feature does not unconditionally dump all base seeds or all preferred gems across all 21 categories into the stack manifest.
- Base seeds and preferred gems only enter the candidate retrieval pool if their category is relevant to the context query or has semantic overlap.
- A second-pass refinement engine analyzes the candidate pool and curates a compact, cohesive stack of 5–12 gems tailored specifically to the workflow.
- The refinement pass limits each relevant category to at most 1–3 gems and completely omits irrelevant categories and unused stack layers.
- The stack manifest assigns contextual, task-specific role descriptions explaining the exact architectural purpose of each gem for the requested workflow.
- Second-pass curation uses LLM synthesis when available and gracefully falls back to local semantic vector and category scoring when offline.
- The CLI supports an optional --max-gems parameter (default 10) to cap the maximum number of gems returned in the stack manifest.
- The generated Gemfile contains only the curated gems grouped by active layers and commented with their contextual workflow roles.
