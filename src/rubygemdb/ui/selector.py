"""CLI multi-selector for choosing gems to prune.

Supports dual engines:
- Charmbracelet's `gum choose --no-limit` (when available in PATH and interactive)
- Textual `SelectionList` (pure Python native fallback)
"""

import logging
import shutil
import subprocess
import sys
from typing import Any, List, Optional, Sequence, Tuple, Union

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Label, SelectionList
from textual.widgets.selection_list import Selection

from rubygemdb.services.prune import PruneCandidate

logger = logging.getLogger(__name__)


def format_candidate_item(
    item: Union[PruneCandidate, Tuple[str, str, str], dict[str, Any], str],
) -> Tuple[str, str]:
    """Normalize a candidate into (gem_name, display_label)."""
    if isinstance(item, PruneCandidate):
        name = item.name
        reason = item.reason
        detail = item.detail
    elif isinstance(item, tuple):
        name = item[0]
        reason = item[1] if len(item) > 1 else ""
        detail = item[2] if len(item) > 2 else ""
    elif isinstance(item, dict):
        name = item.get("name", "")
        reason = item.get("reason", "")
        detail = item.get("detail", "")
    else:
        name = str(item)
        reason = ""
        detail = ""

    label_parts = [name]
    if reason:
        label_parts.append(f"— {reason}")
    if detail:
        label_parts.append(f"({detail})")

    label = " ".join(label_parts)
    return name, label


class PruneSelectorApp(App[List[str]]):
    """Interactive Textual TUI for multi-selecting prune candidate gems."""

    CSS = """
    Screen {
        background: #1e1e2e;
        layout: vertical;
    }
    #header-container {
        padding: 1 2;
        background: #181825;
        border-bottom: heavy #cba6f7;
    }
    #title-label {
        color: #cba6f7;
        text-style: bold;
    }
    #instructions-label {
        color: #a6adc8;
    }
    #list-container {
        height: 1fr;
        padding: 1 2;
    }
    SelectionList {
        background: #181825;
        border: solid #45475a;
        height: 1fr;
    }
    #button-bar {
        height: 3;
        padding: 0 2;
        background: #181825;
        align: right middle;
    }
    Button {
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("a", "select_all", "Select All"),
        Binding("u", "clear_all", "Clear All"),
        Binding("c", "confirm", "Confirm"),
        Binding("enter", "confirm", "Confirm", priority=True),
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        items: Sequence[Tuple[str, str]],
        title: str = "Select Gems to Prune",
        preselected: Optional[Sequence[str]] = None,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.items = list(items)
        self.title_text = title
        self.preselected_set = set(preselected or [])

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="header-container"):
            yield Label(self.title_text, id="title-label")
            yield Label(
                "Space to toggle selection • 'a' to select all • 'u' to clear all • Enter to confirm • Esc to cancel",
                id="instructions-label",
            )
        with Vertical(id="list-container"):
            selections = [
                Selection(label, name, name in self.preselected_set)
                for name, label in self.items
            ]
            yield SelectionList[str](*selections, id="selection-list")
        with Horizontal(id="button-bar"):
            yield Button("Select All (a)", id="btn-select-all", variant="default")
            yield Button("Clear All (u)", id="btn-clear-all", variant="default")
            yield Button("Cancel (Esc)", id="btn-cancel", variant="error")
            yield Button("Confirm Prune (Enter)", id="btn-confirm", variant="success")
        yield Footer()

    def action_select_all(self) -> None:
        selection_list = self.query_one("#selection-list", SelectionList)
        selection_list.select_all()

    def action_clear_all(self) -> None:
        selection_list = self.query_one("#selection-list", SelectionList)
        selection_list.deselect_all()

    def action_confirm(self) -> None:
        selection_list = self.query_one("#selection-list", SelectionList)
        self.exit(list(selection_list.selected))

    def action_cancel(self) -> None:
        self.exit([])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-select-all":
            self.action_select_all()
        elif event.button.id == "btn-clear-all":
            self.action_clear_all()
        elif event.button.id == "btn-confirm":
            self.action_confirm()
        elif event.button.id == "btn-cancel":
            self.action_cancel()


def _select_with_gum(
    items: Sequence[Tuple[str, str]],
    header: str = "Select gems to prune:",
    preselected: Optional[Sequence[str]] = None,
) -> List[str]:
    """Execute multi-selection via Charmbracelet's `gum choose --no-limit`.

    Passes candidates as CLI arguments so terminal stdin/stderr remain attached
    to the active TTY for interactive navigation, rendering, and confirmation.
    """
    delimiter = "||"
    cmd = [
        "gum",
        "choose",
        "--no-limit",
        f"--label-delimiter={delimiter}",
        f"--header={header}",
        "--height=18",
    ]
    if preselected:
        cmd.append(f"--selected={','.join(preselected)}")

    options = [f"{label}{delimiter}{name}" for name, label in items]
    cmd.extend(options)

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
            return lines
        # Exit code 130 or non-zero indicates user cancelled (Esc or Ctrl+C)
        return []
    except (OSError, Exception) as e:
        logger.warning(f"Error executing gum choose: {e}, falling back to Textual.")
        return _select_with_textual(items, header=header, preselected=preselected)


def _select_with_textual(
    items: Sequence[Tuple[str, str]],
    header: str = "Select Gems to Prune",
    preselected: Optional[Sequence[str]] = None,
) -> List[str]:
    """Execute multi-selection via Textual SelectionList TUI."""
    app = PruneSelectorApp(items=items, title=header, preselected=preselected)
    result = app.run()
    return result or []


def select_gems_to_prune(
    candidates: Sequence[Union[PruneCandidate, Tuple[str, str, str], dict[str, Any], str]],
    header: str = "Select gems to prune (Space to toggle, Enter to confirm, Esc to abort):",
    preselected: Optional[Sequence[str]] = None,
    backend: str = "auto",
) -> List[str]:
    """Prompt the user with an interactive multi-selector to pick gems to prune.

    Args:
        candidates: Sequence of prune candidates (PruneCandidate, tuple, dict, or name).
        header: Header prompt text displayed above selection list.
        preselected: Optional list of gem names to start selected.
        backend: Selection engine: 'auto' (gum if available, else textual), 'gum', or 'textual'.

    Returns:
        List of selected gem names.
    """
    if not candidates:
        return []

    items = [format_candidate_item(c) for c in candidates]

    # Non-interactive fallback: if stdin is not a TTY (CI or pipes)
    if not sys.stdin.isatty():
        logger.debug("Non-interactive terminal detected; skipping multi-selector.")
        return list(preselected or [])

    chosen_backend = backend.lower() if backend else "auto"

    if chosen_backend == "gum":
        return _select_with_gum(items, header=header, preselected=preselected)
    elif chosen_backend == "textual":
        return _select_with_textual(items, header=header, preselected=preselected)
    else:  # "auto"
        if shutil.which("gum"):
            return _select_with_gum(items, header=header, preselected=preselected)
        return _select_with_textual(items, header=header, preselected=preselected)
