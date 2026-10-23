"""Advisor export tests: markdown sections, JSON shape, Gemfile lines, empty stack."""
from rubygemdb.services.advisor import AdvisorSession
from rubygemdb.services.advisor_export import build_export

BRIEF = "a CLI app that parses PDFs and chunks text"

CARD = {
    "card": {
        "deliverable": "a CLI app",
        "inputs": ["PDF documents"],
        "transforms": ["chunk text"],
        "outputs": ["markdown report"],
        "constraints": ["no native extensions"],
    },
    "open_questions": [],
}


def _search(query, phase, limit):
    return [
        {
            "name": f"{phase}-gem-{i}",
            "description": f"Handles {phase}. Well maintained.",
            "category": "cli_libraries",
            "layer": "composition",
            "score": 1.0 - i * 0.1,
        }
        for i in range(5)
    ][:limit]


def _session_with_picks():
    session = AdvisorSession(
        card_llm=lambda brief: dict(CARD),
        intent_llm=lambda text, context: None,
        reason_llm=lambda card_text, gems: {g["name"]: f"{g['name']} fits the card" for g in gems},
        search=_search,
        ask=lambda gem, q: "answer",
    )
    session.respond(BRIEF)
    session.respond("go")
    session.respond("accept")
    return session


def test_markdown_has_brief_card_stack_gemfile_transcript():
    session = _session_with_picks()
    markdown, json_dict = build_export(session)
    assert "## Project Brief" in markdown
    assert BRIEF in markdown
    assert "## Requirement Card" in markdown
    assert "no native extensions" in markdown
    assert "## Confirmed Stack" in markdown
    assert "### Deliverable" in markdown
    assert "### Inputs" in markdown
    assert "skeleton-gem-0 fits the card" in markdown
    assert "## Gemfile" in markdown
    assert 'gem "skeleton-gem-0"' in markdown
    assert "## Transcript" in markdown
    assert json_dict["brief"] == BRIEF


def test_json_shape():
    session = _session_with_picks()
    _, json_dict = build_export(session)
    assert set(json_dict) == {"brief", "card", "stack", "transcript"}
    assert set(json_dict["card"]) == {
        "deliverable", "inputs", "transforms", "outputs", "constraints", "open_questions",
    }
    assert set(json_dict["stack"]) == {"deliverable", "inputs", "transforms", "outputs"}
    for picks in json_dict["stack"].values():
        for pick in picks:
            assert set(pick) == {"name", "rationale", "category"}
    assert json_dict["transcript"]
    roles = {m["role"] for m in json_dict["transcript"]}
    assert {"user", "agent"} <= roles


def test_empty_stack_still_writes_transcript():
    session = AdvisorSession(
        card_llm=lambda brief: {"clarify": "What kind of app?"},
        intent_llm=lambda text, context: None,
        reason_llm=lambda card_text, gems: {},
        search=_search,
        ask=lambda gem, q: "",
    )
    session.respond("a thing")
    markdown, json_dict = build_export(session)
    assert "No stack was confirmed." in markdown
    assert json_dict["stack"] == {}
    assert json_dict["transcript"]
