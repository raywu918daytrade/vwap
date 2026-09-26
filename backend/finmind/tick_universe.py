"""Read the stock universe mirrored from the Hugging Face dataset."""

from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]


def _universe_file_path() -> Path:
    return _ROOT / "db/tickers/tick_universe.parquet"


def load_tick_universe() -> list[str]:
    """Return stock ids from the HF-synchronized universe snapshot."""
    path = _universe_file_path()
    if not path.exists():
        raise FileNotFoundError(
            f"{path} does not exist; synchronize db/tickers from HF first"
        )
    return (
        pd.read_parquet(path, columns=["stock_id"])["stock_id"]
        .astype(str)
        .drop_duplicates()
        .tolist()
    )
