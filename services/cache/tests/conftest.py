"""Pytest path bootstrap for the cache service test directory.

The suite uses fakeredis (in-process Redis emulator, dev/test ONLY).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for p in (
    _ROOT,
    _ROOT / "packages",
    _ROOT / "services" / "shard-router",
    _ROOT / "apps",
):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
