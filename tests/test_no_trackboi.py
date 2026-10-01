"""Guard against Trackboi distillation remnants.

The feature was removed; nothing named trackboi or distill may exist in
source, tests, or project docs (the dated .zread wiki snapshot is excluded).
"""
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
SCAN_DIRS = ["src", "tests"]
SCAN_FILES = ["README.md", "INSTALL.md", "CLAUDE.md", "AGENTS.md"]
PATTERN = re.compile(r"trackboi|distill", re.IGNORECASE)


def test_no_trackboi_or_distill_remnants():
    offenders: list[str] = []
    for d in SCAN_DIRS:
        for path in (PROJECT_ROOT / d).rglob("*.py"):
            if path.name == Path(__file__).name:
                continue
            if PATTERN.search(path.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(str(path))
    for name in SCAN_FILES:
        path = PROJECT_ROOT / name
        if path.exists() and PATTERN.search(path.read_text(encoding="utf-8", errors="ignore")):
            offenders.append(name)
    assert not offenders, f"Trackboi/distill text remains in: {offenders}"
