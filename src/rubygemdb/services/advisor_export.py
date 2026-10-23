"""Export the confirmed advisor session as markdown and JSON.

Pure functions over session state: the markdown file carries the brief,
the requirement card, the confirmed stack grouped by slot with a
rationale per gem, a Gemfile snippet, and the full Q&A transcript.
The JSON file carries the brief, the card, the confirmed stack by slot,
and the transcript.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from rubygemdb.services.advisor import SLOT_LABELS


def _card_dict(session: Any) -> dict:
    card = getattr(session, "card", None)
    if card is None:
        return {}
    return {
        "deliverable": getattr(card, "deliverable", ""),
        "inputs": list(getattr(card, "inputs", []) or []),
        "transforms": list(getattr(card, "transforms", []) or []),
        "outputs": list(getattr(card, "outputs", []) or []),
        "constraints": list(getattr(card, "constraints", []) or []),
        "open_questions": list(getattr(card, "open_questions", []) or []),
    }


def _stack_by_slot(session: Any) -> dict[str, list[dict]]:
    """Confirmed picks grouped by slot, in slot order."""
    stack = getattr(session, "stack_by_slot", None)
    if isinstance(stack, dict):
        return {
            slot: [
                {
                    "name": pick.get("name"),
                    "rationale": pick.get("reason", ""),
                    "category": pick.get("category"),
                }
                for pick in picks
            ]
            for slot, picks in stack.items()
        }
    return {}


def build_export(session: Any) -> tuple[str, dict]:
    """Build (markdown, json_dict) from an advisor session."""
    brief = getattr(session, "brief", "")
    card = _card_dict(session)
    stack = _stack_by_slot(session)
    transcript = list(getattr(session, "transcript", []))

    lines = [
        "# RubyGemDB Advisor Export",
        "",
        "## Project Brief",
        "",
        brief or "(no brief)",
        "",
        "## Requirement Card",
        "",
    ]
    if card:
        lines.append(f"- **Deliverable**: {card.get('deliverable') or '(none stated)'}")
        for slot in ("inputs", "transforms", "outputs", "constraints"):
            items = card.get(slot) or []
            lines.append(f"- **{SLOT_LABELS[slot]}**: {'; '.join(items) if items else '(none stated)'}")
        questions = card.get("open_questions") or []
        if questions:
            lines.append("")
            lines.append("**Open questions**:")
            for question in questions:
                lines.append(f"- {question}")
    else:
        lines.append("(no card)")
    lines.append("")

    lines.append("## Confirmed Stack")
    lines.append("")
    if stack:
        for slot, picks in stack.items():
            lines.append(f"### {SLOT_LABELS.get(slot, slot.title())}")
            lines.append("")
            for pick in picks:
                lines.append(f"- **{pick['name']}** — {pick['rationale']}")
            lines.append("")
        lines.append("## Gemfile")
        lines.append("")
        lines.append("```ruby")
        for picks in stack.values():
            for pick in picks:
                lines.append(f'gem "{pick["name"]}"')
        lines.append("```")
    else:
        lines.append("No stack was confirmed.")
    lines.append("")

    lines.append("## Transcript")
    lines.append("")
    if transcript:
        for message in transcript:
            role = message.get("role", "system").title()
            lines.append(f"**{role}**:")
            lines.append("")
            lines.append(str(message.get("content", "")))
            lines.append("")
    else:
        lines.append("(no messages)")

    markdown = "\n".join(lines)

    json_dict = {
        "brief": brief,
        "card": card,
        "stack": stack,
        "transcript": transcript,
    }
    return markdown, json_dict


def write_export(session: Any, out_dir: Path | None = None) -> tuple[Path, Path]:
    """Write chat_<ts>.md and chat_<ts>.json under output/chat_exports/."""
    out_dir = out_dir or (Path("output") / "chat_exports")
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    markdown, json_dict = build_export(session)
    md_path = out_dir / f"chat_{timestamp}.md"
    json_path = out_dir / f"chat_{timestamp}.json"
    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(json_dict, indent=2), encoding="utf-8")
    return md_path, json_path
