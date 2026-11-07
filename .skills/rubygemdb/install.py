#!/usr/bin/env python3
"""Installer for the rubygemdb agent skill.

Copies or symlinks the rubygemdb skill directory into an agent skills folder
(default: ~/.agents/skills/rubygemdb).
"""

from __future__ import annotations

import argparse
import shutil
import stat
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent


def install_skill(
    target_dir: Path | str = Path.home() / ".agents" / "skills",
    link: bool = False,
) -> Path:
    target_path = Path(target_dir).expanduser().resolve()
    target_path.mkdir(parents=True, exist_ok=True)
    dest_dir = target_path / "rubygemdb"

    # Remove existing destination if present
    if dest_dir.is_symlink() or dest_dir.is_file():
        dest_dir.unlink()
    elif dest_dir.is_dir():
        shutil.rmtree(dest_dir)

    if link:
        dest_dir.symlink_to(SKILL_DIR.resolve(), target_is_directory=True)
        return dest_dir

    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "scripts").mkdir(parents=True, exist_ok=True)

    # Copy SKILL.md
    skill_md_src = SKILL_DIR / "SKILL.md"
    if skill_md_src.exists():
        shutil.copy2(skill_md_src, dest_dir / "SKILL.md")

    # Copy scripts/gem.py
    gem_py_src = SKILL_DIR / "scripts" / "gem.py"
    if gem_py_src.exists():
        dest_gem_py = dest_dir / "scripts" / "gem.py"
        shutil.copy2(gem_py_src, dest_gem_py)
        # Ensure executable permission
        current_perms = dest_gem_py.stat().st_mode
        dest_gem_py.chmod(current_perms | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Copy install.py
    install_py_src = SKILL_DIR / "install.py"
    if install_py_src.exists():
        shutil.copy2(install_py_src, dest_dir / "install.py")

    return dest_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install rubygemdb skill to agent skills folder")
    parser.add_argument(
        "--target",
        default=str(Path.home() / ".agents" / "skills"),
        help="Target directory for installed skills (default: ~/.agents/skills)",
    )
    parser.add_argument(
        "--link",
        action="store_true",
        help="Symlink the skill directory instead of copying",
    )
    args = parser.parse_args(argv)
    dest = install_skill(target_dir=Path(args.target), link=args.link)
    action = "Linked" if args.link else "Installed"
    print(f"{action} rubygemdb skill to {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
