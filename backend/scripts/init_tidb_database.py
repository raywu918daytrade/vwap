"""Create the TiDB database and offline dashboard tables."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from data.tidb_offline_store import init_tidb_database, tidb_config, tidb_schema_statements  # noqa: E402

_ROOT = Path(__file__).parent.parent
load_dotenv(_ROOT / ".env", override=True)


def _schema_sql(include_database: bool = True) -> str:
    return ";\n\n".join(tidb_schema_statements(include_database=include_database)) + ";\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Create TiDB database/tables for offline VWAP query data")
    parser.add_argument("--print-sql", action="store_true", help="Print SQL instead of connecting to TiDB")
    parser.add_argument("--tables-only", action="store_true", help="Do not include CREATE DATABASE/USE in printed SQL")
    args = parser.parse_args()

    if args.print_sql:
        print(_schema_sql(include_database=not args.tables_only), end="")
        return

    cfg = tidb_config()
    if cfg is None:
        raise SystemExit(
            "TiDB direct connection is not configured. Set TIDB_DAY_TRADE_DATABASE_URL "
            "or run this command with --print-sql and execute the SQL in TiDB Cloud."
        )

    init_tidb_database()
    print(f"TiDB database `{cfg.database}` and offline tables are ready.", flush=True)


if __name__ == "__main__":
    main()
