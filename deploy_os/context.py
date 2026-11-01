"""Explicit binding between reusable code and an operator's private state."""
from __future__ import annotations

import os
from pathlib import Path


def state_root(explicit: str | None = None) -> Path:
    raw = explicit if explicit is not None else os.environ.get("DEPLOY_OS_ROOT")
    if not raw:
        raise ValueError("Set DEPLOY_OS_ROOT or --root to the private state checkout containing AGENTS.md")
    root = Path(raw).expanduser().resolve()
    if not (root / "AGENTS.md").is_file():
        raise ValueError("Private state root must contain AGENTS.md; verify the selected checkout")
    if root == Path(__file__).resolve().parents[1]:
        raise ValueError("Select a private state checkout; the toolkit checkout contains reusable code")
    return root
