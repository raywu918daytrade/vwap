"""Runtime profile helpers shared by deployment entry points."""

from __future__ import annotations

import os


def is_render_reader() -> bool:
    """Return whether this process is the lightweight Render reader."""
    return os.environ.get("RUNTIME_PROFILE", "").strip().lower() == "render-reader"


def uses_on_demand_hf() -> bool:
    """Return whether historical monthly shards are fetched on demand."""
    return os.environ.get("HF_DATA_MODE", "").strip().lower() == "on-demand"
