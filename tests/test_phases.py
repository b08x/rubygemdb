"""Phase taxonomy tests: every category mapped to exactly one phase, order fixed."""
from rubygemdb.models.categories import CATEGORIES
from rubygemdb.models.phases import PHASES, PHASE_BY_CATEGORY, categories_for, phase_for


def test_phase_order_is_fixed():
    assert PHASES == ["skeleton", "input", "processing", "output"]


def test_every_category_mapped_to_exactly_one_phase():
    slugs = {c.slug for c in CATEGORIES}
    assert set(PHASE_BY_CATEGORY) == slugs, "mapped slugs must match taxonomy exactly"
    for slug, phase in PHASE_BY_CATEGORY.items():
        assert phase in PHASES, f"unknown phase {phase!r} for {slug}"


def test_helpers_round_trip():
    for phase in PHASES:
        cats = categories_for(phase)
        assert cats, f"phase {phase} has no categories"
        for slug in cats:
            assert phase_for(slug) == phase
