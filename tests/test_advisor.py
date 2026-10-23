"""Advisor session tests with fake LLM and fake search.

Covers: card extraction and display, go -> whole-stack proposal, accept,
add/remove/swap, per-slot retry with slot inference, card edits in both
stages, clarification, restart, reset, free-form chat replies, and pick
survival across many turns.
"""
from rubygemdb.services.advisor import AdvisorSession

BRIEF = "a CLI app that parses PDFs, chunks text with context, and extracts images, tables, and code blocks"

CARD = {
    "card": {
        "deliverable": "a CLI app",
        "inputs": ["PDF documents"],
        "transforms": ["chunk text with context", "extract images, tables, code blocks"],
        "outputs": ["markdown report"],
        "constraints": [],
    },
    "open_questions": [],
}

# Gem pools per phase (slots search through phases), 7 gems so swaps work.
SLOT_GEMS = {
    "skeleton": [f"skeleton-gem-{i}" for i in range(7)],
    "input": [f"input-gem-{i}" for i in range(7)],
    "processing": [f"proc-gem-{i}" for i in range(7)],
    "output": [f"out-gem-{i}" for i in range(7)],
}

PHASE_CATEGORY = {
    "skeleton": "cli_libraries",
    "input": "document_parsing",
    "processing": "text_search",
    "output": "document_generation",
}


def _hit(name, phase, score):
    return {
        "name": name,
        "description": f"{name} does the {phase} work. Well maintained.",
        "category": PHASE_CATEGORY[phase],
        "layer": "composition",
        "score": score,
    }


def fake_search(query, phase, limit):
    gems = SLOT_GEMS.get(phase, [])
    return [_hit(name, phase, 1.0 - i * 0.01) for i, name in enumerate(gems)][:limit]


def make_session(card=None, intent=None, reason=None, ask=None, search=None, fetch_gem=None):
    return AdvisorSession(
        card_llm=card or (lambda brief: dict(CARD)),
        intent_llm=intent or (lambda text, context: None),
        reason_llm=reason or (lambda card_text, gems: {g["name"]: f"{g['name']} fits the card" for g in gems}),
        search=search or fake_search,
        ask=ask or (lambda gem, question: f"answer about {gem}"),
        fetch_gem=fetch_gem,
    )


def at_stack(search=None, **kwargs):
    """A session driven to the stack stage: brief -> card -> go."""
    session = make_session(search=search, **kwargs)
    session.respond(BRIEF)
    session.respond("go")
    assert session.stage == "stack"
    return session


# -- card stage ------------------------------------------------------------


def test_brief_yields_card_then_go_proposes_whole_stack():
    session = make_session()
    reply = session.respond(BRIEF)
    assert "**Requirement card**" in reply
    assert "Deliverable" in reply and "Inputs" in reply and "Transforms" in reply
    assert session.stage == "card"
    assert "Proposed stack" not in reply

    reply = session.respond("go")
    assert "**Proposed stack**" in reply
    assert "**Deliverable**" in reply
    assert "**Inputs**" in reply
    assert "**Transforms**" in reply
    assert "**Outputs**" in reply
    assert session.stage == "stack"
    # All four slots present their own candidates at once.
    assert "skeleton-gem-0" in reply
    assert "input-gem-0" in reply
    assert "proc-gem-0" in reply
    assert "out-gem-0" in reply


def test_card_displays_open_questions():
    card = {
        "card": dict(CARD["card"]),
        "open_questions": ["Which Ruby version?", "Offline only?"],
    }
    session = make_session(card=lambda brief: dict(card))
    reply = session.respond(BRIEF)
    assert "Open questions" in reply
    assert "Which Ruby version?" in reply


def test_fallback_card_when_llm_unavailable():
    session = make_session(card=lambda brief: None)
    reply = session.respond(BRIEF)
    assert "**Requirement card**" in reply
    assert session.card.deliverable, "deliverable must not be empty"
    assert session.card.transforms, "unmatched clauses default to transforms"


def test_vague_brief_returns_one_clarifying_question():
    session = make_session(card=lambda brief: {"clarify": "What kind of application is this for?"})
    reply = session.respond("a thing")
    assert reply == "What kind of application is this for?"
    assert session.clarification
    # The answer restarts extraction with the combined brief.
    combined = []

    def extract(brief):
        combined.append(brief)
        if len(combined) == 1:
            return {"clarify": "What kind of application?"}
        return dict(CARD)

    session = make_session(card=extract)
    session.respond("a thing")
    session.respond("a CLI that reads PDFs")
    assert combined[-1] == "a thing a CLI that reads PDFs"
    assert session.stage == "card"


def test_keyword_edit_updates_card_in_card_stage():
    session = make_session()
    session.respond(BRIEF)
    reply = session.respond("inputs are DOCX files, not PDFs")
    assert "Card updated" in reply
    assert session.card.inputs == ["DOCX files", "not PDFs"]
    assert session.stage == "card", "edits in the card stage do not propose yet"


# -- stack stage -------------------------------------------------------------


def test_accept_confirms_whole_stack():
    session = at_stack()
    reply = session.respond("accept")
    assert "Stack confirmed" in reply
    assert session.stage == "done"
    slots = set(session.stack_by_slot)
    assert slots == {"deliverable", "inputs", "transforms", "outputs"}
    assert session.confirmed_picks
    assert any(p["name"] == "skeleton-gem-0" for p in session.confirmed_picks)


def test_remove_drops_gem_from_proposal():
    session = at_stack()
    reply = session.respond("remove input-gem-0")
    assert "Removed input-gem-0" in reply
    names = [c["name"] for c in session.slot_states["inputs"].candidates]
    assert "input-gem-0" not in names


def test_remove_unknown_gem_reported():
    session = at_stack()
    reply = session.respond("remove totally-made-up-gem")
    assert "not in the current proposal" in reply


def test_swap_replaces_gem_within_slot():
    session = at_stack()
    reply = session.respond("swap skeleton-gem-0 for skeleton-gem-6")
    assert "skeleton-gem-6" in reply
    assert session.slot_states["deliverable"].presented[0]["name"] == "skeleton-gem-6"


def test_swap_replacement_must_be_in_same_slot():
    session = at_stack()
    reply = session.respond("swap skeleton-gem-0 for input-gem-0")
    assert "not among the ranked candidates" in reply


def test_ask_delegates_and_keeps_stage():
    asked = []
    session = make_session(ask=lambda gem, q: asked.append((gem, q)) or f"about {gem}")
    session.respond(BRIEF)
    session.respond("go")
    reply = session.respond("tell me about skeleton-gem-1")
    assert asked == [("skeleton-gem-1", "tell me about skeleton-gem-1")]
    assert "about skeleton-gem-1" in reply
    assert session.stage == "stack"


def test_retry_with_slot_refocuses_that_slot():
    queries = []
    focused = [_hit("chunking-pro", "processing", 0.9), _hit("segment-pro", "processing", 0.8)]

    def focused_search(query, phase, limit):
        queries.append((query, phase))
        if "text segmentation" in query:
            return focused[:limit]
        return fake_search(query, phase, limit)

    session = at_stack(search=focused_search)
    reply = session.respond("retry transforms focus on text segmentation")

    assert any("text segmentation" in q for q, _ in queries), "hint must reach the search"
    assert "chunking-pro" in reply
    assert session.slot_states["transforms"].presented[0]["name"] == "chunking-pro"


def test_retry_without_slot_infers_slot_from_hint():
    """A hint pointing at parsing categories retargets the inputs slot."""
    def targeted_search(query, phase, limit):
        if "pdf extraction" in query and phase == "input":
            return [_hit("kreuzberg", "input", 0.95), _hit("pdf-reader", "input", 0.9)]
        return fake_search(query, phase, limit)

    session = at_stack(search=targeted_search)
    reply = session.respond("retry focus on pdf extraction")

    assert "kreuzberg" in reply
    assert session.slot_states["inputs"].presented[0]["name"] == "kreuzberg"


def test_retry_without_results_keeps_previous_pool():
    def empty_when_hinted(query, phase, limit):
        if "audio transcription" in query:
            return []
        return fake_search(query, phase, limit)

    session = at_stack(search=empty_when_hinted)
    state = session.slot_states["transforms"]
    before = [c["name"] for c in state.candidates]

    reply = session.respond("retry transforms audio transcription")

    assert "No gems found" in reply
    assert [c["name"] for c in state.candidates] == before


def test_edit_in_stack_stage_reproposes_affected_slot():
    queries = []

    def recording_search(query, phase, limit):
        queries.append((query, phase))
        return fake_search(query, phase, limit)

    session = at_stack(search=recording_search)
    session.respond("inputs are DOCX files")
    assert session.card.inputs == ["DOCX files"]
    # Only the inputs slot is re-searched.
    phases = [phase for _, phase in queries[-1:]]
    assert phases == ["input"]
    assert "Card updated: Inputs" in session.transcript[-1]["content"]


def test_add_inserts_named_gem_into_its_slot():
    def inventory_hit(name):
        return {
            "name": name,
            "description": f"{name} from the inventory",
            "category": "document_parsing",
            "layer": "plumbing",
        }

    session = at_stack(fetch_gem=lambda name: inventory_hit(name) if name == "kreuzberg" else None)
    reply = session.respond("add kreuzberg")
    assert "Added kreuzberg" in reply
    state = session.slot_states["inputs"]
    assert state.presented[0]["name"] == "kreuzberg"
    assert state.presented[0]["reason"] == "Manually added."

    session.respond("accept")
    assert any(p["name"] == "kreuzberg" for p in session.stack_by_slot["inputs"])


def test_add_multiple_gems_filters_command_words():
    known = {"kreuzberg", "pdf-reader"}

    def inventory_hit(name):
        return {"name": name, "description": "", "category": "document_parsing", "layer": "plumbing"}

    session = at_stack(fetch_gem=lambda name: inventory_hit(name) if name in known else None)
    session.respond("add kreuzberg and pdf-reader to the picks")
    added = {c["name"] for c in session.slot_states["inputs"].presented[:2]}
    assert added == known


def test_add_reports_unknown_gems():
    def inventory_hit(name):
        return {"name": name, "description": "", "category": "document_parsing", "layer": "plumbing"}

    session = at_stack(fetch_gem=lambda name: inventory_hit(name) if name == "kreuzberg" else None)
    before = [c["name"] for c in session.slot_states["inputs"].candidates]
    reply = session.respond("add totally-made-up-gem")
    assert "totally-made-up-gem" in reply
    assert "are in the database" in reply
    assert [c["name"] for c in session.slot_states["inputs"].candidates] == before


def test_chat_intent_returns_llm_reply():
    def intent(text, context):
        if "maintained" in text.lower():
            return {"action": "chat", "reply": "Fair point — I can re-run a slot with maintenance as the focus."}
        return None

    session = at_stack(intent=intent)
    reply = session.respond("these all look poorly maintained")
    assert "Fair point" in reply
    assert session.stage == "stack"


def test_intent_llm_receives_stage_and_card_context():
    contexts = []

    def intent(text, context):
        contexts.append(context)
        return None

    session = make_session(intent=intent)
    session.respond(BRIEF)
    session.respond("go")
    session.respond("what is going on")
    assert any("stage: card" in c for c in contexts)
    assert any("stage: stack" in c for c in contexts)
    assert any("deliverable" in c for c in contexts), "context carries the card"


def test_empty_search_reports_slot_and_keeps_stage():
    def empty_search(query, phase, limit):
        return [] if phase == "output" else fake_search(query, phase, limit)

    session = at_stack(search=empty_search)
    assert session.stage == "stack"
    reply = session._represent()
    assert "No gems matched: Outputs" in reply


def test_restart_resets_session():
    session = at_stack()
    session.respond("accept")
    assert session.confirmed_picks
    session.respond("start over")
    assert session.stage == "brief"
    assert session.confirmed_picks == []
    assert not session.started
    # The next message is a fresh brief.
    session.respond(BRIEF)
    assert session.stage == "card"


def test_reset_clears_state():
    session = at_stack()
    session.respond("accept")
    session.reset()
    assert session.brief == ""
    assert session.stage == "brief"
    assert session.slot_states == {}
    assert session.stack == {}
    assert session.transcript == []
    assert session.confirmed_picks == []
    assert session.started is False


def test_picks_survive_many_turns_agent_memory():
    """Picks live in the session, not LLM memory: they cannot be trimmed."""
    session = at_stack()
    session.respond("accept")
    for _ in range(10):
        session.respond("ask skeleton-gem-0 is it maintained")
        assert session.stage == "done"
    picks = session.confirmed_picks
    assert any(p["name"].startswith("skeleton-gem") for p in picks)
    assert any(p["name"].startswith("input-gem") for p in picks)
    assert any(p["name"].startswith("proc-gem") for p in picks)
    assert any(p["name"].startswith("out-gem") for p in picks)
