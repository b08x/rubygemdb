"""Card-driven advisor session.

Extracts a structured requirement card from the project brief (deliverable,
inputs, transforms, outputs, constraints), shows it to the user for
editing, then proposes one whole gem stack from slot-driven hybrid
searches. There is no phase walk: the user iterates on the proposal as a
whole with free-form messages and stack actions (add / remove / swap /
retry / edit / ask). State lives in plain Python, not in LLM memory, so
confirmed picks survive memory trimming and the flow works offline via
keyword fallbacks.

LLM calls and searches are injected callables; tests run without a model.
"""

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from rubygemdb.models.categories import CATEGORIES
from rubygemdb.models.phases import PHASE_BY_CATEGORY, categories_for
from rubygemdb.services.hybrid_search import query_targets

# Injected callable contracts.
# card_llm(brief) -> {"card": {...}, "open_questions": [...]} | {"clarify": "..."} | None
CardLLM = Callable[[str], Optional[dict]]
# intent_llm(user_text, context) -> {"action": "...", ...} | None
IntentLLM = Callable[[str, str], Optional[dict]]
# reason_llm(card_text, gems) -> {name: reason} | None
ReasonLLM = Callable[[str, list[dict]], Optional[dict]]
# search(query, phase, limit) -> list of hybrid search hits
SearchFn = Callable[[str, Optional[str], int], list[dict]]
# ask(gem, question) -> answer text
AskFn = Callable[[str, str], str]
# fetch_gem(name) -> inventory hit dict, or None when the name is unknown
FetchGemFn = Callable[[str], Optional[dict]]

CARD_SLOTS = ("deliverable", "inputs", "transforms", "outputs", "constraints")
SEARCHABLE_SLOTS = ("deliverable", "inputs", "transforms", "outputs")
SLOT_LABELS = {
    "deliverable": "Deliverable",
    "inputs": "Inputs",
    "transforms": "Transforms",
    "outputs": "Outputs",
    "constraints": "Constraints",
}
# Slots drive retrieval through the phase filter: the build-phase taxonomy
# survives only as an internal search restriction.
SLOT_PHASE = {
    "deliverable": "skeleton",
    "inputs": "input",
    "transforms": "processing",
    "outputs": "output",
}
PHASE_SLOT = {phase: slot for slot, phase in SLOT_PHASE.items()}

PRESENT_PER_SLOT = 3
SEARCH_LIMIT = 8

_CARD_ACTIONS = (
    "Reply `go` to propose a stack from this card, or tell me what to change "
    '(e.g. "inputs are DOCX files, not PDFs" or "constraints: must run offline").'
)
_STACK_ACTIONS = (
    "Reply `accept` to confirm this stack, `remove <gem>` to drop one, `add <gem>` "
    "to include one that was not offered, `swap <gem> for <gem>` to change one, "
    "`retry <slot> <focus>` to re-search a slot with adjusted focus, `edit <slot> ...` "
    "to change the card, `ask <gem> <question>` for details, or `start over` with a "
    "new brief. Anything else reads as feedback — say what's off and I'll respond."
)


@dataclass
class RequirementCard:
    """Structured requirements gathered from the brief."""

    deliverable: str = ""
    inputs: list[str] = field(default_factory=list)
    transforms: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "deliverable": self.deliverable,
            "inputs": list(self.inputs),
            "transforms": list(self.transforms),
            "outputs": list(self.outputs),
            "constraints": list(self.constraints),
        }

    def apply(self, fields: dict) -> list[str]:
        """Apply a partial card update; returns the names of changed slots."""
        changed: list[str] = []
        for slot in CARD_SLOTS:
            value = fields.get(slot)
            if value is None:
                continue
            if slot == "deliverable":
                if not isinstance(value, str):
                    value = str(value)
                if value.strip() and value.strip() != self.deliverable:
                    self.deliverable = value.strip()
                    changed.append(slot)
                continue
            if isinstance(value, str):
                items = _split_items(value)
            elif isinstance(value, list):
                items = [str(v).strip() for v in value if str(v).strip()]
            else:
                continue
            if items and items != getattr(self, slot):
                setattr(self, slot, items)
                changed.append(slot)
        return changed

    def display(self) -> str:
        lines = [f"- **Deliverable**: {self.deliverable or '(none stated)'}"]
        for slot in ("inputs", "transforms", "outputs", "constraints"):
            items = getattr(self, slot)
            label = SLOT_LABELS[slot]
            lines.append(f"- **{label}**: {'; '.join(items) if items else '(none stated)'}")
        if self.open_questions:
            lines.append("")
            lines.append("**Open questions**:")
            for question in self.open_questions:
                lines.append(f"- {question}")
        return "\n".join(lines)

    def summary(self) -> str:
        parts = [f"deliverable: {self.deliverable}"] if self.deliverable else []
        for slot in ("inputs", "transforms", "outputs", "constraints"):
            items = getattr(self, slot)
            if items:
                parts.append(f"{slot}: {'; '.join(items)}")
        return " | ".join(parts)


@dataclass
class SlotState:
    """Ranked candidate pool for one searchable slot."""

    slot: str
    candidates: list[dict] = field(default_factory=list)

    @property
    def presented(self) -> list[dict]:
        return self.candidates[:PRESENT_PER_SLOT]


def _split_items(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[;,]| and ", text) if item.strip()]


def _first_sentence(text: str) -> str:
    text = (text or "").strip()
    match = re.match(r"(.+?[.!?])", text)
    return match.group(1).strip() if match else text


def _slot_for_clause(clause: str) -> str:
    """Offline clause-to-slot mapping via category keywords."""
    tokens = {t for t in re.findall(r"\w+", clause.lower())}
    for category in CATEGORIES:
        if tokens & {k.lower() for k in category.keywords}:
            phase = PHASE_BY_CATEGORY.get(category.slug)
            return PHASE_SLOT.get(phase or "", "transforms")
    return "transforms"


def _fallback_card(brief: str) -> RequirementCard:
    """Offline card extraction: first sentence is the deliverable, each
    remaining clause lands in the slot its keywords point at."""
    card = RequirementCard(deliverable=_first_sentence(brief))
    clauses = [c.strip() for c in re.split(r"[,;]| and ", brief) if c.strip()]
    for clause in clauses:
        slot = _slot_for_clause(clause)
        if slot == "deliverable":
            continue
        if clause not in getattr(card, slot):
            getattr(card, slot).append(clause)
    return card


class AdvisorSession:
    """State machine for one card-driven advisor chat session.

    Stages: brief -> card -> stack -> done.
    """

    def __init__(
        self,
        card_llm: CardLLM,
        intent_llm: IntentLLM,
        reason_llm: ReasonLLM,
        search: SearchFn,
        ask: AskFn,
        fetch_gem: Optional[FetchGemFn] = None,
    ):
        self.card_llm = card_llm
        self.intent_llm = intent_llm
        self.reason_llm = reason_llm
        self.search = search
        self.ask = ask
        self.fetch_gem = fetch_gem
        self.reset()

    def reset(self) -> None:
        """Clear the brief, card, stack, and confirmed picks."""
        self.brief: str = ""
        self.card = RequirementCard()
        self.stage = "brief"
        self.slot_states: dict[str, SlotState] = {}
        self.stack: dict[str, list[dict]] = {}
        self.transcript: list[dict] = []
        self.clarification: Optional[str] = None
        self.started = False

    # -- transcript ---------------------------------------------------------

    def _record(self, role: str, content: str) -> None:
        self.transcript.append({"role": role, "content": content})

    # -- card gathering -----------------------------------------------------

    def _extract_card(self, brief: str) -> tuple[Optional[RequirementCard], Optional[str]]:
        result = self.card_llm(brief)
        if isinstance(result, dict) and result.get("clarify"):
            return None, str(result["clarify"])

        card = RequirementCard()
        if isinstance(result, dict) and isinstance(result.get("card"), dict):
            raw = result["card"]
            card.apply({slot: raw.get(slot) for slot in CARD_SLOTS})
            questions = result.get("open_questions") or []
            if isinstance(questions, list):
                card.open_questions = [str(q) for q in questions if str(q).strip()]
        if not (card.deliverable or card.inputs or card.transforms or card.outputs):
            card = _fallback_card(brief)
        return card, None

    def start(self, brief: str) -> str:
        """Begin a session from a project brief; returns the first agent message."""
        self.brief = brief
        card, clarify = self._extract_card(brief)
        if card is None:
            self.clarification = clarify
            self._record("agent", clarify or "")
            return clarify or ""

        self.card = card
        self.stage = "card"
        self.started = True
        message = self._present_card()
        self._record("agent", message)
        return message

    def _present_card(self) -> str:
        lines = [
            f"**Project brief**: {self.brief}",
            "",
            "**Requirement card**:",
            self.card.display(),
            "",
            _CARD_ACTIONS,
        ]
        return "\n".join(lines)

    # -- stack proposal -----------------------------------------------------

    def _slot_query(self, slot: str) -> str:
        items = getattr(self.card, slot)
        if slot == "deliverable":
            return self.card.deliverable
        return " ".join(items)

    def _search_slot(self, slot: str, extra: str = "") -> list[dict]:
        """Hybrid search for one slot, restricted to its build phase.

        Each card item is searched separately and merged by best score, so
        one item's keywords cannot drown out another's.
        """
        if slot == "deliverable":
            items = [self.card.deliverable] if self.card.deliverable else []
        else:
            items = list(getattr(self.card, slot))
        queries = [" ".join(part for part in (item, extra) if part) for item in items]
        if not queries:
            return []
        seen: dict[str, dict] = {}
        for query in queries:
            for hit in self.search(query, SLOT_PHASE.get(slot), SEARCH_LIMIT) or []:
                name = hit.get("name")
                if not name:
                    continue
                hit = dict(hit)
                hit.setdefault("description", "")
                if name not in seen or hit.get("score", 0.0) > seen[name].get("score", 0.0):
                    seen[name] = hit
        return sorted(seen.values(), key=lambda h: h.get("score", 0.0), reverse=True)

    def _propose_slots(self, slots: list[str]) -> None:
        """(Re-)search the given slots and refresh their candidate pools."""
        for slot in slots:
            state = self.slot_states.setdefault(slot, SlotState(slot=slot))
            state.candidates = self._search_slot(slot)
        self._attach_reasons([g for s in self.slot_states.values() for g in s.presented])

    def _attach_reasons(self, gems: list[dict]) -> None:
        """One-line reason per gem, one LLM call, first-sentence fallback."""
        reasons: dict[str, str] = {}
        try:
            reasons = self.reason_llm(self.card.summary(), gems) or {}
        except Exception:
            reasons = {}
        for gem in gems:
            gem["reason"] = reasons.get(gem["name"]) or _first_sentence(gem.get("description", ""))
            gem.setdefault("slot", _slot_for_category(gem.get("category")))

    def propose(self) -> str:
        """Search every searchable slot and present the whole stack proposal."""
        slots = [s for s in SEARCHABLE_SLOTS if self._slot_query(s)]
        if not slots:
            message = (
                "The card has nothing to search for yet. Tell me what the tool takes "
                "in, what it does, or what it produces."
            )
            self._record("agent", message)
            return message
        self.stage = "stack"
        self._propose_slots(slots)
        return self._present_stack()

    def _present_stack(self) -> str:
        """Render the proposal. Callers record the message exactly once."""
        lines = [f"**Proposed stack** (brief: {self.brief})", ""]
        empty: list[str] = []
        shown = 0
        for slot in SEARCHABLE_SLOTS:
            if not self._slot_query(slot):
                continue
            state = self.slot_states.get(slot)
            if not state or not state.candidates:
                empty.append(slot)
                continue
            lines.append(f"**{SLOT_LABELS[slot]}**")
            for i, gem in enumerate(state.presented, 1):
                lines.append(f"{i}. **{gem['name']}** ({gem.get('category')}) — {gem.get('reason', '')}")
            lines.append("")
            shown += 1
        if empty:
            lines.append("No gems matched: " + ", ".join(SLOT_LABELS[s] for s in empty) + ".")
            lines.append("")
        if shown == 0:
            lines.append(
                "No gems were found for any slot. Use `edit` to sharpen the card "
                "(shorter, more concrete phrasing helps), or `add <gem>` directly."
            )
            message = "\n".join(lines)
            return message
        lines.append(_STACK_ACTIONS)
        return "\n".join(lines)

    def _represent(self) -> str:
        return self._present_stack()

    def _require_stack(self) -> None:
        if self.stage not in ("stack", "done"):
            raise RuntimeError("No stack proposal is active")

    # -- user responses -----------------------------------------------------

    def respond(self, text: str) -> str:
        """Handle one user message; returns the agent's reply."""
        self._record("user", text)

        if not self.started:
            if self.clarification:
                answer = text
                self.clarification = None
                return self.start(f"{self.brief} {answer}".strip())
            return self.start(text)

        if self.clarification:
            answer = text
            self.clarification = None
            return self.start(f"{self.brief} {answer}".strip())

        intent = self._parse_intent(text)
        reply = self._dispatch(intent, text)
        self._record("agent", reply)
        return reply

    def _parse_intent(self, text: str) -> dict:
        intent = None
        try:
            intent = self.intent_llm(text, self._context())
        except Exception:
            intent = None
        if isinstance(intent, dict) and intent.get("action"):
            return intent
        return _keyword_intent(text, self.stage)

    def _context(self) -> str:
        """Compact state summary handed to the intent LLM."""
        parts = [f"stage: {self.stage}"]
        if self.card.deliverable or self.card.inputs or self.card.transforms or self.card.outputs:
            parts.append(f"card: {self.card.summary()}")
        if self.stage in ("stack", "done"):
            names = [g["name"] for s in SEARCHABLE_SLOTS for g in self.slot_states.get(s, SlotState(slot=s)).presented]
            if names:
                parts.append("proposed stack: " + ", ".join(names))
        return "; ".join(parts)

    def _dispatch(self, intent: dict, text: str) -> str:
        action = intent.get("action")
        if action == "go":
            if self.stage == "card":
                return self.propose()
            return self._present_card()
        if action == "edit":
            return self._edit(intent.get("card") or {})
        if action == "accept":
            return self._accept()
        if action == "add":
            return self._add(intent.get("gem") or text)
        if action == "remove":
            return self._remove(intent.get("gem"))
        if action == "swap":
            return self._swap(intent.get("gem"), intent.get("with"))
        if action == "retry":
            return self._retry(intent.get("slot"), intent.get("hint") or text)
        if action == "ask":
            return self._ask(intent.get("gem"), intent.get("question") or text)
        if action == "restart":
            remainder = re.sub(r"^\s*(start over|new brief|new project)[\s:,-]*", "", text, flags=re.I).strip()
            self.reset()
            if remainder:
                return self.start(remainder)
            message = "Session cleared. Describe the project you want a stack for."
            self._record("agent", message)
            return message
        if action == "chat" and intent.get("reply"):
            return str(intent["reply"]) + "\n\n" + self._stage_actions()
        return self._stage_actions()

    def _stage_actions(self) -> str:
        if self.stage == "card":
            return self._present_card()
        if self.stage in ("stack", "done"):
            return self._represent()
        return "Describe the project you want a stack for."

    # -- card editing -------------------------------------------------------

    def _edit(self, fields: dict) -> str:
        """Apply a partial card update; re-present the card or re-propose."""
        changed = self.card.apply(fields)
        if not changed:
            if self.stage == "card":
                return self._present_card()
            return self._represent()
        if self.stage == "card":
            message = self._present_card()
            return "Card updated.\n\n" + message
        searchable = [s for s in changed if s in SEARCHABLE_SLOTS and self._slot_query(s)]
        if searchable:
            if self.stage == "done":
                self.stage = "stack"
                self.stack = {}
            self._propose_slots(searchable)
        return "Card updated: " + ", ".join(SLOT_LABELS[s] for s in changed) + ".\n\n" + self._represent()

    # -- stack actions ------------------------------------------------------

    def _accept(self) -> str:
        self._require_stack()
        self.stack = {}
        for slot in SEARCHABLE_SLOTS:
            state = self.slot_states.get(slot)
            if state and state.presented:
                self.stack[slot] = [dict(g) for g in state.presented]
        self.stage = "done"
        return "Stack confirmed.\n\n" + self._summary()

    def _summary(self) -> str:
        lines = ["Confirmed stack:"]
        total = 0
        for slot in SEARCHABLE_SLOTS:
            picks = self.stack.get(slot) or []
            if picks:
                total += len(picks)
                lines.append(f"- **{SLOT_LABELS[slot]}**: " + ", ".join(p["name"] for p in picks))
        if total == 0:
            lines.append("- No gems were confirmed.")
        lines.append("")
        lines.append("You can still `ask` about a gem, `edit` the card to re-propose, or `start over`.")
        lines.append("Use Export to write the markdown and JSON files.")
        return "\n".join(lines)

    def _add(self, names: Optional[str]) -> str:
        """Manually include named gems in the proposal, in the slot they belong to."""
        self._require_stack()
        if not names or not names.strip():
            return "Add needs one or more gem names: `add <gem>`."
        if self.fetch_gem is None:
            return "Manual add is not available in this session."
        blocklist = {"add", "and", "the", "a", "gem", "gems", "please", "to", "pick", "picks", "stack", "in", "into", "proposal", "include"}
        wanted = [
            token for token in re.split(r"[,;/]|\s+", names.strip())
            if token and token.lower() not in blocklist
        ]
        if not wanted:
            return "Add needs one or more gem names: `add <gem>`."

        added: list[dict] = []
        missing: list[str] = []
        for name in wanted:
            gem = self.fetch_gem(name)
            if gem is None:
                missing.append(name)
                continue
            gem = dict(gem)
            gem.setdefault("description", "")
            gem["reason"] = "Manually added."
            slot = _slot_for_category(gem.get("category"))
            gem.setdefault("slot", slot)
            state = self.slot_states.setdefault(slot, SlotState(slot=slot))
            state.candidates = [gem] + [c for c in state.candidates if c["name"] != gem["name"]]
            added.append(gem)

        if not added:
            return (
                f"None of those gems are in the database: {', '.join(missing)}. "
                "Only inventoried gems can be added; check the spelling or add "
                "the gem to the inventory first."
            )
        lines = ["Added " + ", ".join(g["name"] for g in added) + " to the proposal."]
        if missing:
            lines.append(f"Not found in the database: {', '.join(missing)}.")
        return "\n".join(lines) + "\n\n" + self._represent()

    def _remove(self, gem: Optional[str]) -> str:
        """Drop a named gem from the proposal."""
        self._require_stack()
        if not gem:
            return "Remove needs a gem name: `remove <gem>`."
        for state in self.slot_states.values():
            before = len(state.candidates)
            state.candidates = [c for c in state.candidates if c["name"] != gem]
            if len(state.candidates) < before:
                return f"Removed {gem} from the proposal.\n\n" + self._represent()
        return f"`{gem}` is not in the current proposal."

    def _swap(self, gem: Optional[str], replacement: Optional[str]) -> str:
        """Replace one proposed gem with another candidate from the same slot."""
        self._require_stack()
        if not gem or not replacement:
            return "Swap needs a gem to replace and its replacement: `swap <gem> for <gem>`."
        for state in self.slot_states.values():
            names = {c["name"] for c in state.candidates}
            if gem in names:
                if replacement not in names:
                    return f"`{replacement}` is not among the ranked candidates for the {SLOT_LABELS[state.slot]} slot."
                presented = state.presented
                for i, candidate in enumerate(presented):
                    if candidate["name"] == gem:
                        presented[i] = next(c for c in state.candidates if c["name"] == replacement)
                        break
                state.candidates[: len(presented)] = presented
                return self._represent()
        return f"`{gem}` is not in the current proposal."

    def _ask(self, gem: Optional[str], question: str) -> str:
        if not gem:
            return "Ask needs a gem: `ask <gem> <question>`."
        try:
            answer = self.ask(gem, question)
        except Exception as e:
            answer = f"Could not answer that: {e}"
        return f"**{gem}** — {answer}"

    def _retry(self, slot: Optional[str], hint: str) -> str:
        """Re-search a slot with the user's focus folded into the query.

        A focus that points at another slot's categories retargets that
        slot instead, so the best gems are not hidden by the wrong filter.
        """
        self._require_stack()
        if slot and slot not in SLOT_LABELS:
            slot = None
        if slot is None:
            targeted = query_targets(hint, list(PHASE_BY_CATEGORY))
            for candidate_slot, phase in SLOT_PHASE.items():
                if set(targeted) & set(categories_for(phase)):
                    slot = candidate_slot
                    break
        if slot is None:
            return (
                "Which slot should I re-search? " + ", ".join(SLOT_LABELS[s] for s in SEARCHABLE_SLOTS)
                + f" — e.g. `retry transforms {hint}`."
            )
        state = self.slot_states.setdefault(slot, SlotState(slot=slot))
        refreshed = self._search_slot(slot, extra=hint)
        if not refreshed:
            return (
                f"No gems found for the {SLOT_LABELS[slot]} slot with that focus. "
                "Try different wording, `edit` the card, or `remove` what you don't need."
            )
        state.candidates = refreshed
        self._attach_reasons(state.presented)
        return self._represent()

    # -- accessors ----------------------------------------------------------

    @property
    def confirmed_picks(self) -> list[dict]:
        """All confirmed picks across slots, in slot order."""
        picks: list[dict] = []
        for slot in SEARCHABLE_SLOTS:
            for pick in self.stack.get(slot) or []:
                picks.append(dict(pick, slot=slot))
        return picks

    @property
    def stack_by_slot(self) -> dict[str, list[dict]]:
        """Confirmed picks grouped by slot, in slot order."""
        return {
            slot: [dict(p) for p in self.stack.get(slot) or []]
            for slot in SEARCHABLE_SLOTS
            if self.stack.get(slot)
        }


def _slot_for_category(category: Optional[str]) -> str:
    """The proposal slot a gem's category lands in; transforms by default."""
    phase = PHASE_BY_CATEGORY.get(category or "")
    return PHASE_SLOT.get(phase or "", "transforms")


def _keyword_intent(text: str, stage: str) -> dict:
    """Offline intent fallback for go / edit / accept / add / remove / swap /
    ask / retry / restart."""
    lowered = text.lower().strip()
    if re.search(r"\bstart over\b|\bnew brief\b|\bnew project\b", lowered):
        return {"action": "restart"}
    if stage == "card":
        edit = _keyword_edit(text)
        if edit:
            return edit
        if re.search(r"\bgo\b|\bproceed\b|\blooks? (right|good|correct)\b|\byes\b|^ok\b|\bsearch\b|\bpropose\b", lowered):
            return {"action": "go"}
        if re.search(r"\bask\b|\bwhy\b|\bwhat\b|\bhow\b|\btell me\b", lowered):
            gem = re.search(r"\b(?:about |ask )(\S+)", lowered)
            return {"action": "ask", "gem": gem.group(1).strip("?.!,") if gem else None, "question": text}
        return {}
    if re.search(r"\baccept\b|\bconfirm\b|\bapprove\b|\bship it\b|\blooks good\b", lowered):
        return {"action": "accept"}
    remove_match = re.search(r"\bremove\s+(\S+)", lowered)
    if remove_match:
        return {"action": "remove", "gem": remove_match.group(1).strip("?.!,")}
    add_match = re.search(r"\badd\s+(.+)", lowered)
    if add_match:
        return {"action": "add", "gem": add_match.group(1)}
    swap_match = re.search(r"\bswap (\S+) (?:for|with) (\S+)", lowered)
    if swap_match:
        return {"action": "swap", "gem": swap_match.group(1), "with": swap_match.group(2)}
    retry_match = re.search(r"\bretry(?:\s+(\w+))?\s*[::]?\s*(.*)", lowered)
    if retry_match:
        slot = retry_match.group(1)
        hint = retry_match.group(2).strip()
        if slot in SLOT_LABELS:
            return {"action": "retry", "slot": slot, "hint": hint or text}
        return {"action": "retry", "hint": text}
    if re.search(r"\bretry\b|\btry again\b|\bsearch again\b|\bnone of these\b|\bno good\b", lowered):
        return {"action": "retry", "hint": text}
    if re.search(r"\bask\b|\bwhy\b|\bwhat\b|\bhow\b|\btell me\b", lowered):
        gem = re.search(r"\b(?:about |ask )(\S+)", lowered)
        return {"action": "ask", "gem": gem.group(1).strip("?.!,") if gem else None, "question": text}
    edit = _keyword_edit(text)
    if edit:
        return edit
    return {}


def _keyword_edit(text: str) -> dict:
    """Offline card editing: `<slot> is/are/: ...` messages."""
    match = re.search(
        r"\b(deliverable|inputs?|transforms?|outputs?|constraints?)\b\s*(?:is|are|:|=)?\s*(.+)",
        text.strip(),
        re.I,
    )
    if not match:
        return {}
    slot = match.group(1).lower()
    slot = "inputs" if slot == "input" else slot.rstrip("s") if slot in ("transforms", "outputs", "constraints") else slot
    if slot not in CARD_SLOTS:
        return {}
    value = match.group(2).strip()
    if not value or len(value) < 2:
        return {}
    return {"action": "edit", "card": {slot: value}}
