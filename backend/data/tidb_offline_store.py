"""Optional TiDB backing store for precomputed offline dashboard data.

The parquet shards remain the portable source of truth, but a TiDB copy lets
small Render/Oracle runtimes query only the requested date/symbol instead of
materializing monthly parquet files in process memory.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import queue
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, unquote, urlparse

import pandas as pd
from dotenv import load_dotenv

_ROOT = Path(__file__).parent.parent
load_dotenv(_ROOT / ".env", override=False)

DATASET_PATTERN_SCAN = "pattern_scan"
DATASET_VWAP_ACTIVITY = "vwap_activity"
DATASET_VWAP_SIGNALS = "vwap_signals"
DATASET_CHART_DAY = "chart_day"
DATASET_CHART_M1 = "chart_m1"
KNOWN_DATASETS = {DATASET_PATTERN_SCAN, DATASET_VWAP_ACTIVITY, DATASET_VWAP_SIGNALS}
KNOWN_CHART_DATASETS = {DATASET_CHART_DAY, DATASET_CHART_M1}

PATTERN_SCAN_COLUMNS = [
    "scan_date",
    "stock_id",
    "stock_name",
    "pattern_type",
    "pattern_name",
    "timeframe",
    "score",
    "event_date",
    "payload_json",
]
PATTERN_SCAN_SUMMARY_COLUMNS = [column for column in PATTERN_SCAN_COLUMNS if column != "payload_json"]
VWAP_ACTIVITY_COLUMNS = ["scan_date", "stock_id", "day_atr", "open5_rng", "vol5_pr"]
VWAP_SIGNAL_COLUMNS = ["scan_date", "kind", "stock_id", "time", "value", "payload_json"]
CHART_DAY_COLUMNS = ["stock_id", "bar_date", "open", "high", "low", "close", "volume"]
CHART_M1_COLUMNS = ["stock_id", "bar_time", "bar_date", "open", "high", "low", "close", "volume"]
EVENT_KINDS = {"vwap", "sr"}
MAP_KINDS = {"macd", "obv"}

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_READ_POOL: queue.LifoQueue[tuple[Any, float]] | None = None
_READ_POOL_LOCK = threading.Lock()
_VERSION_CACHE: dict[tuple[Any, ...], tuple[float, str | None]] = {}
_VERSION_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True)
class TidbTables:
    pattern_scan: str
    vwap_activity: str
    vwap_signals: str
    vwap_bundle: str
    chart_day: str
    chart_m1: str
    shard_syncs: str


@dataclass(frozen=True)
class TidbConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    ssl_ca: str | None = None
    ssl_disabled: bool = False


def _strip_wrapping_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _env_value(name: str, default: str = "") -> str:
    return _strip_wrapping_quotes(os.environ.get(name, default))


def _table_name(env_name: str, default: str) -> str:
    value = _env_value(env_name, default).strip()
    if not _IDENT_RE.match(value):
        raise RuntimeError(f"{env_name} must be a simple SQL identifier, got {value!r}")
    return value


def tidb_tables() -> TidbTables:
    prefix = _env_value("TIDB_DAY_TRADE_TABLE_PREFIX", "vwap").strip()
    if prefix and not _IDENT_RE.match(prefix):
        raise RuntimeError("TIDB_DAY_TRADE_TABLE_PREFIX must be a simple SQL identifier")
    prefix = f"{prefix}_" if prefix else ""
    return TidbTables(
        pattern_scan=_table_name("TIDB_DAY_TRADE_PATTERN_SCAN_TABLE", f"{prefix}pattern_scan"),
        vwap_activity=_table_name("TIDB_DAY_TRADE_ACTIVITY_TABLE", f"{prefix}activity"),
        vwap_signals=_table_name("TIDB_DAY_TRADE_SIGNAL_TABLE", f"{prefix}signals"),
        vwap_bundle=_table_name("TIDB_DAY_TRADE_BUNDLE_TABLE", f"{prefix}signal_bundles"),
        chart_day=_table_name("TIDB_DAY_TRADE_CHART_DAY_TABLE", f"{prefix}chart_day"),
        chart_m1=_table_name("TIDB_DAY_TRADE_CHART_M1_TABLE", f"{prefix}chart_m1"),
        shard_syncs=_table_name("TIDB_DAY_TRADE_SHARD_SYNC_TABLE", f"{prefix}offline_shard_syncs"),
    )


def _q(identifier: str) -> str:
    if not _IDENT_RE.match(identifier):
        raise RuntimeError(f"Unsafe SQL identifier: {identifier!r}")
    return f"`{identifier}`"


def _database_name(value: str) -> str:
    if not _IDENT_RE.match(value):
        raise RuntimeError(f"TIDB database name must be a simple SQL identifier, got {value!r}")
    return value


def _bool_env(name: str, default: bool = False) -> bool:
    raw = _env_value(name) if name in os.environ else None
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    raw = _env_value(name) if name in os.environ else None
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def _parse_database_url(raw: str) -> TidbConfig:
    parsed = urlparse(raw)
    if parsed.scheme not in {"mysql", "mysql+pymysql"}:
        raise RuntimeError("TIDB_DAY_TRADE_DATABASE_URL must use mysql:// or mysql+pymysql://")
    if not parsed.hostname or not parsed.username or parsed.password is None:
        raise RuntimeError("TIDB_DAY_TRADE_DATABASE_URL must include host, user, and password")
    database = unquote(parsed.path.lstrip("/"))
    if not database:
        raise RuntimeError("TIDB_DAY_TRADE_DATABASE_URL must include a database name")
    database = _database_name(database)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    ssl_disabled = str(query.get("ssl_disabled", "")).strip().lower() in {"1", "true", "yes"}
    ssl_ca = query.get("ssl_ca") or query.get("ssl-ca") or os.environ.get("TIDB_DAY_TRADE_SSL_CA")
    return TidbConfig(
        host=parsed.hostname,
        port=int(parsed.port or 4000),
        user=unquote(parsed.username),
        password=unquote(parsed.password),
        database=database,
        ssl_ca=ssl_ca or None,
        ssl_disabled=ssl_disabled,
    )


def tidb_config() -> TidbConfig | None:
    raw_url = _env_value("TIDB_DAY_TRADE_DATABASE_URL").strip()
    if raw_url:
        return _parse_database_url(raw_url)

    host = (_env_value("TIDB_DAY_TRADE_HOST") or _env_value("DB_HOST")).strip()
    user = (_env_value("TIDB_DAY_TRADE_USER") or _env_value("DB_USERNAME")).strip()
    password = _env_value("TIDB_DAY_TRADE_PASSWORD") or _env_value("DB_PASSWORD")
    database = (_env_value("TIDB_DAY_TRADE_DATABASE") or _env_value("DB_DATABASE")).strip()
    if host and user and password and database:
        database = _database_name(database)
        port = _env_value("TIDB_DAY_TRADE_PORT") or _env_value("DB_PORT") or "4000"
        return TidbConfig(
            host=host,
            port=int(port),
            user=user,
            password=password,
            database=database,
            ssl_ca=_env_value("TIDB_DAY_TRADE_SSL_CA") or None,
            ssl_disabled=_bool_env("TIDB_DAY_TRADE_SSL_DISABLED", False),
        )
    return None


def tidb_configured() -> bool:
    return tidb_config() is not None


def tidb_reads_enabled() -> bool:
    raw = _env_value("TIDB_DAY_TRADE_READ", "auto").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return tidb_configured()


def tidb_chart_reads_enabled() -> bool:
    raw = _env_value("TIDB_DAY_TRADE_CHART_READ") if "TIDB_DAY_TRADE_CHART_READ" in os.environ else None
    if raw is None or raw == "":
        raw = _env_value("TIDB_DAY_TRADE_READ", "auto")
    raw = raw.strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return tidb_configured()


def _missing_config_message() -> str:
    if os.environ.get("TIDB_DAY_TRADE_PUBLIC_KEY") and os.environ.get("TIDB_DAY_TRADE_PRIVATE_KEY"):
        return (
            "Found TIDB_DAY_TRADE_PUBLIC_KEY/TIDB_DAY_TRADE_PRIVATE_KEY, but bulk parquet sync "
            "needs a direct TiDB MySQL-compatible connection. Set TIDB_DAY_TRADE_DATABASE_URL "
            "or TIDB_DAY_TRADE_HOST/USER/PASSWORD/DATABASE. Data Service keys alone need a "
            "deployed custom endpoint URL and are not enough for arbitrary bulk upserts."
        )
    return (
        "Set TIDB_DAY_TRADE_DATABASE_URL or "
        "TIDB_DAY_TRADE_HOST/USER/PASSWORD/DATABASE before syncing TiDB."
    )


def connect_tidb(use_database: bool = True, *, autocommit: bool = False):
    cfg = tidb_config()
    if cfg is None:
        raise RuntimeError(_missing_config_message())
    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError("Install pymysql to use TiDB sync: pip install pymysql") from exc

    ssl_args: dict[str, Any] | None = None
    if not cfg.ssl_disabled:
        ssl_args = {}
        if cfg.ssl_ca:
            ssl_args["ca"] = cfg.ssl_ca

    kwargs: dict[str, Any] = {
        "host": cfg.host,
        "port": cfg.port,
        "user": cfg.user,
        "password": cfg.password,
        "charset": "utf8mb4",
        "autocommit": autocommit,
        "cursorclass": pymysql.cursors.DictCursor,
        "ssl": ssl_args,
        "connect_timeout": max(1, _int_env("TIDB_DAY_TRADE_CONNECT_TIMEOUT", 8)),
        "read_timeout": max(
            1,
            _int_env(
                "TIDB_DAY_TRADE_READ_TIMEOUT" if autocommit else "TIDB_DAY_TRADE_COMMIT_TIMEOUT",
                20 if autocommit else 120,
            ),
        ),
        "write_timeout": max(1, _int_env("TIDB_DAY_TRADE_WRITE_TIMEOUT", 20)),
    }
    if use_database:
        kwargs["database"] = cfg.database
    return pymysql.connect(**kwargs)


def _read_pool() -> queue.LifoQueue[tuple[Any, float]]:
    global _READ_POOL
    with _READ_POOL_LOCK:
        if _READ_POOL is None:
            _READ_POOL = queue.LifoQueue(maxsize=max(1, _int_env("TIDB_DAY_TRADE_POOL_SIZE", 4)))
        return _READ_POOL


@contextmanager
def _read_connection():
    """Reuse TLS connections for short runtime reads.

    TiDB Cloud connection setup is much slower than these indexed queries. A
    small LIFO pool keeps the hot path fast without retaining query results or
    monthly market data in process memory.
    """
    pool = _read_pool()
    conn = None
    last_used = 0.0
    try:
        try:
            conn, last_used = pool.get_nowait()
        except queue.Empty:
            conn = connect_tidb(autocommit=True)

        idle_ping_seconds = max(1, _int_env("TIDB_DAY_TRADE_POOL_PING_SECONDS", 60))
        if last_used and time.monotonic() - last_used >= idle_ping_seconds:
            conn.ping(reconnect=True)
        yield conn
    except Exception:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        conn = None
        raise
    finally:
        if conn is not None:
            try:
                pool.put_nowait((conn, time.monotonic()))
            except queue.Full:
                conn.close()


def _version_cache_get(key: tuple[Any, ...]) -> tuple[bool, str | None]:
    now = time.monotonic()
    with _VERSION_CACHE_LOCK:
        cached = _VERSION_CACHE.get(key)
        if cached is None:
            return False, None
        expires_at, value = cached
        if expires_at <= now:
            _VERSION_CACHE.pop(key, None)
            return False, None
        return True, value


def _version_cache_put(key: tuple[Any, ...], value: str | None) -> str | None:
    ttl = max(1, _int_env("TIDB_DAY_TRADE_VERSION_CACHE_SECONDS", 60))
    with _VERSION_CACHE_LOCK:
        _VERSION_CACHE[key] = (time.monotonic() + ttl, value)
    return value


def _clear_version_cache() -> None:
    with _VERSION_CACHE_LOCK:
        _VERSION_CACHE.clear()


def warm_tidb_read_pool() -> bool:
    """Open one reusable read connection before the first user request."""
    if not tidb_reads_enabled() and not tidb_chart_reads_enabled():
        return False
    with _read_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()
    return True


def tidb_schema_statements(include_database: bool = False) -> list[str]:
    cfg = tidb_config()
    tables = tidb_tables()
    statements: list[str] = []
    if include_database:
        if cfg is None:
            statements.append("CREATE DATABASE IF NOT EXISTS `daytrade` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            statements.append("USE `daytrade`")
        else:
            statements.append(
                f"CREATE DATABASE IF NOT EXISTS {_q(cfg.database)} "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            statements.append(f"USE {_q(cfg.database)}")
    statements.extend(
        [
            f"""
CREATE TABLE IF NOT EXISTS {_q(tables.pattern_scan)} (
    scan_date DATE NOT NULL,
    stock_id VARCHAR(16) NOT NULL,
    stock_name VARCHAR(80) NOT NULL DEFAULT '',
    pattern_type VARCHAR(64) NOT NULL,
    pattern_name VARCHAR(80) NOT NULL DEFAULT '',
    timeframe VARCHAR(16) NOT NULL DEFAULT 'day',
    score DOUBLE NULL,
    event_date DATE NULL,
    payload_json LONGTEXT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (scan_date, stock_id, pattern_type, timeframe),
    KEY idx_scan_score (scan_date, score),
    KEY idx_stock_date (stock_id, scan_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            f"""
CREATE TABLE IF NOT EXISTS {_q(tables.vwap_activity)} (
    scan_date DATE NOT NULL,
    stock_id VARCHAR(16) NOT NULL,
    day_atr DOUBLE NULL,
    open5_rng DOUBLE NULL,
    vol5_pr DOUBLE NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (scan_date, stock_id),
    KEY idx_activity_stock (stock_id, scan_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            f"""
CREATE TABLE IF NOT EXISTS {_q(tables.vwap_signals)} (
    scan_date DATE NOT NULL,
    kind VARCHAR(24) NOT NULL,
    stock_id VARCHAR(16) NOT NULL DEFAULT '',
    event_time VARCHAR(16) NOT NULL DEFAULT '',
    value DOUBLE NULL,
    payload_json LONGTEXT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (scan_date, kind, stock_id, event_time),
    KEY idx_signal_stock (stock_id, scan_date),
    KEY idx_signal_kind_date (kind, scan_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            f"""
CREATE TABLE IF NOT EXISTS {_q(tables.vwap_bundle)} (
    scan_date DATE NOT NULL,
    payload_gzip LONGBLOB NOT NULL,
    source_row_count INT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (scan_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            f"""
CREATE TABLE IF NOT EXISTS {_q(tables.chart_day)} (
    stock_id VARCHAR(16) NOT NULL,
    bar_date DATE NOT NULL,
    open DOUBLE NOT NULL,
    high DOUBLE NOT NULL,
    low DOUBLE NOT NULL,
    close DOUBLE NOT NULL,
    volume BIGINT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_id, bar_date),
    KEY idx_chart_day_date (bar_date, stock_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            f"""
CREATE TABLE IF NOT EXISTS {_q(tables.chart_m1)} (
    stock_id VARCHAR(16) NOT NULL,
    bar_time DATETIME NOT NULL,
    bar_date DATE NOT NULL,
    open DOUBLE NOT NULL,
    high DOUBLE NOT NULL,
    low DOUBLE NOT NULL,
    close DOUBLE NOT NULL,
    volume BIGINT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_id, bar_time),
    KEY idx_chart_m1_date_stock (bar_date, stock_id, bar_time),
    KEY idx_chart_m1_time (bar_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
            f"""
CREATE TABLE IF NOT EXISTS {_q(tables.shard_syncs)} (
    source_path VARCHAR(255) NOT NULL,
    dataset VARCHAR(64) NOT NULL,
    month_key VARCHAR(7) NOT NULL,
    date_count INT NOT NULL,
    row_count INT NOT NULL,
    sha256 CHAR(64) NOT NULL,
    synced_at DATETIME NOT NULL,
    PRIMARY KEY (source_path),
    KEY idx_dataset_month (dataset, month_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
""",
        ]
    )
    return [statement.strip().rstrip(";") for statement in statements]


def init_tidb_database() -> None:
    cfg = tidb_config()
    if cfg is None:
        raise RuntimeError(_missing_config_message())
    with connect_tidb(use_database=False) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS {_q(cfg.database)} "
                    "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
                cur.execute(f"USE {_q(cfg.database)}")
            ensure_tidb_schema(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def ensure_tidb_schema(conn) -> None:
    with conn.cursor() as cur:
        for statement in tidb_schema_statements(include_database=False):
            cur.execute(statement)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value)


def _date_key(value: Any) -> str:
    return _clean_text(value)[:10]


def _float_or_none(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _int_or_zero(value: Any) -> int:
    try:
        if pd.isna(value):
            return 0
        return int(round(float(value)))
    except Exception:
        return 0


def _json_loads(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _empty_bundle() -> dict[str, Any]:
    return {
        "vwap": [],
        "sr": [],
        "macd": {},
        "obv": {},
        "chg": {},
        "sr_levels": {},
        "m1_bars": 0,
    }


def _bundle_from_signal_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    bundle = _empty_bundle()
    for row in rows:
        kind = _clean_text(row.get("kind"))
        stock_id = _clean_text(row.get("stock_id"))
        payload = _json_loads(row.get("payload_json"))
        if kind in EVENT_KINDS:
            if not stock_id:
                continue
            payload["stock_id"] = _clean_text(payload.get("stock_id") or stock_id)
            payload["time"] = _clean_text(payload.get("time") or row.get("event_time") or row.get("time"))
            bundle[kind].append(payload)
        elif kind in MAP_KINDS:
            if stock_id:
                bundle[kind][stock_id] = payload
        elif kind == "chg":
            if stock_id:
                value = _float_or_none(row.get("value"))
                if value is not None:
                    bundle["chg"][stock_id] = round(value, 2)
        elif kind == "sr_level":
            if stock_id:
                bundle["sr_levels"][stock_id] = {
                    "resistance": _float_or_none(payload.get("resistance")),
                    "support": _float_or_none(payload.get("support")),
                }
        elif kind == "meta":
            try:
                bundle["m1_bars"] = int(payload.get("m1_bars") or 0)
            except Exception:
                bundle["m1_bars"] = 0

    bundle["vwap"].sort(
        key=lambda item: (_clean_text(item.get("time")), _clean_text(item.get("stock_id"))),
        reverse=True,
    )
    bundle["sr"].sort(
        key=lambda item: (_clean_text(item.get("time")), _clean_text(item.get("stock_id"))),
        reverse=True,
    )
    return bundle


def _month_stem(date: str) -> str:
    return date[:7].replace("-", "_")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_label(path: Path) -> str:
    return str(path.relative_to(_ROOT)) if path.is_relative_to(_ROOT) else str(path)


def _chunked(values: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _delete_dates(conn, table: str, dates: list[str]) -> None:
    _delete_table_dates(conn, table, "scan_date", dates)


def _delete_table_dates(conn, table: str, date_column: str, dates: list[str]) -> None:
    if not dates:
        return
    with conn.cursor() as cur:
        for chunk in _chunked(dates, 400):
            placeholders = ", ".join(["%s"] * len(chunk))
            cur.execute(f"DELETE FROM {_q(table)} WHERE {_q(date_column)} IN ({placeholders})", chunk)


def _insert_many(conn, table: str, columns: list[str], records: list[tuple[Any, ...]]) -> int:
    if not records:
        return 0
    placeholders = ", ".join(["%s"] * len(columns))
    column_sql = ", ".join(_q(column) for column in columns)
    sql = f"INSERT INTO {_q(table)} ({column_sql}) VALUES ({placeholders})"
    with conn.cursor() as cur:
        for chunk in _chunked(records, 1000):
            cur.executemany(sql, chunk)
    return len(records)


def _insert_many_iter(conn, table: str, columns: list[str], records: Iterable[tuple[Any, ...]]) -> int:
    placeholders = ", ".join(["%s"] * len(columns))
    column_sql = ", ".join(_q(column) for column in columns)
    sql = f"INSERT INTO {_q(table)} ({column_sql}) VALUES ({placeholders})"
    total = 0
    with conn.cursor() as cur:
        buffer: list[tuple[Any, ...]] = []
        for record in records:
            buffer.append(record)
            if len(buffer) >= 1000:
                cur.executemany(sql, buffer)
                total += len(buffer)
                buffer = []
        if buffer:
            cur.executemany(sql, buffer)
            total += len(buffer)
    return total


def _filter_df_dates(df: pd.DataFrame, dates: list[str]) -> pd.DataFrame:
    if df.empty or "scan_date" not in df.columns:
        return df
    out = df.copy()
    out["scan_date"] = out["scan_date"].astype(str).str[:10]
    if dates:
        out = out[out["scan_date"].isin(dates)]
    return out


def _dataset_for_path(path: Path) -> str:
    parts = path.resolve().parts
    if len(parts) >= 4 and parts[-4:-1] == ("db", "pattern_scan", "d1"):
        return DATASET_PATTERN_SCAN
    if len(parts) >= 3 and parts[-3:-1] == ("db", "vwap_activity"):
        return DATASET_VWAP_ACTIVITY
    if len(parts) >= 3 and parts[-3:-1] == ("db", "vwap_signals"):
        return DATASET_VWAP_SIGNALS
    raise RuntimeError(f"Unsupported offline shard path: {path}")


def _path_month_dates(path: Path, dates: list[str] | None) -> list[str]:
    if dates is None:
        return []
    stem = path.stem
    return sorted(date for date in {_date_key(value) for value in dates} if date and _month_stem(date) == stem)


def _sync_pattern_scan(conn, df: pd.DataFrame, dates: list[str]) -> int:
    tables = tidb_tables()
    _delete_dates(conn, tables.pattern_scan, dates)
    if df.empty:
        return 0
    records = []
    for row in df.itertuples(index=False):
        records.append(
            (
                _date_key(row.scan_date),
                _clean_text(row.stock_id),
                _clean_text(row.stock_name),
                _clean_text(row.pattern_type),
                _clean_text(row.pattern_name),
                _clean_text(row.timeframe) or "day",
                _float_or_none(row.score),
                _date_key(row.event_date) or None,
                _clean_text(row.payload_json),
            )
        )
    return _insert_many(conn, tables.pattern_scan, PATTERN_SCAN_COLUMNS, records)


def _sync_vwap_activity(conn, df: pd.DataFrame, dates: list[str]) -> int:
    tables = tidb_tables()
    _delete_dates(conn, tables.vwap_activity, dates)
    if df.empty:
        return 0
    records = []
    for row in df.itertuples(index=False):
        records.append(
            (
                _date_key(row.scan_date),
                _clean_text(row.stock_id),
                _float_or_none(row.day_atr),
                _float_or_none(row.open5_rng),
                _float_or_none(row.vol5_pr),
            )
        )
    return _insert_many(conn, tables.vwap_activity, VWAP_ACTIVITY_COLUMNS, records)


def _sync_vwap_signals(conn, df: pd.DataFrame, dates: list[str]) -> int:
    tables = tidb_tables()
    _delete_dates(conn, tables.vwap_signals, dates)
    _delete_dates(conn, tables.vwap_bundle, dates)
    if df.empty:
        return 0
    records = []
    for row in df.itertuples(index=False):
        records.append(
            (
                _date_key(row.scan_date),
                _clean_text(row.kind),
                _clean_text(row.stock_id),
                _clean_text(row.time),
                _float_or_none(row.value),
                _clean_text(row.payload_json),
            )
        )
    row_count = _insert_many(
        conn,
        tables.vwap_signals,
        ["scan_date", "kind", "stock_id", "event_time", "value", "payload_json"],
        records,
    )
    bundle_records: list[tuple[Any, ...]] = []
    date_series = df["scan_date"].map(_date_key)
    for scan_date, group in df.groupby(date_series, sort=True):
        signal_rows = (
            {
                "kind": row.kind,
                "stock_id": row.stock_id,
                "event_time": row.time,
                "value": row.value,
                "payload_json": row.payload_json,
            }
            for row in group.itertuples(index=False)
        )
        bundle = _bundle_from_signal_rows(signal_rows)
        payload = json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        bundle_records.append((scan_date, gzip.compress(payload, compresslevel=4), len(group)))
    _insert_many(
        conn,
        tables.vwap_bundle,
        ["scan_date", "payload_gzip", "source_row_count"],
        bundle_records,
    )
    return row_count


def _read_parquet(path: Path, dataset: str) -> pd.DataFrame:
    columns = {
        DATASET_PATTERN_SCAN: PATTERN_SCAN_COLUMNS,
        DATASET_VWAP_ACTIVITY: VWAP_ACTIVITY_COLUMNS,
        DATASET_VWAP_SIGNALS: VWAP_SIGNAL_COLUMNS,
    }[dataset]
    if not path.exists():
        raise RuntimeError(f"Shard does not exist: {path}")
    try:
        return pd.read_parquet(path, columns=columns)
    except Exception:
        return pd.read_parquet(path)


def sync_offline_paths_to_tidb(paths: Iterable[Path | str], dates: Iterable[str] | None = None) -> dict[str, int]:
    """Replace selected shard dates in TiDB and insert current parquet rows."""
    has_date_filter = dates is not None
    date_list = sorted(date for date in {_date_key(value) for value in dates or []} if date)
    path_list = sorted({Path(path).resolve() for path in paths})
    if not path_list:
        return {}

    summary: dict[str, int] = {dataset: 0 for dataset in KNOWN_DATASETS}
    with connect_tidb() as conn:
        ensure_tidb_schema(conn)
        try:
            for path in path_list:
                dataset = _dataset_for_path(path)
                df = _read_parquet(path, dataset)
                if has_date_filter:
                    replacement_dates = _path_month_dates(path, date_list)
                    if not replacement_dates:
                        continue
                    df = _filter_df_dates(df, replacement_dates)
                else:
                    df = _filter_df_dates(df, [])
                    replacement_dates = sorted(
                        date for date in {_date_key(value) for value in df.get("scan_date", [])} if date
                    )

                if dataset == DATASET_PATTERN_SCAN:
                    row_count = _sync_pattern_scan(conn, df, replacement_dates)
                elif dataset == DATASET_VWAP_ACTIVITY:
                    row_count = _sync_vwap_activity(conn, df, replacement_dates)
                elif dataset == DATASET_VWAP_SIGNALS:
                    row_count = _sync_vwap_signals(conn, df, replacement_dates)
                else:
                    raise RuntimeError(f"Unsupported dataset: {dataset}")

                _record_shard_sync(conn, path, dataset, replacement_dates, row_count)
                summary[dataset] += row_count
                print(
                    f"TiDB sync {_path_label(path)}: dates={len(replacement_dates)} rows={row_count}",
                    flush=True,
                )
            conn.commit()
            _clear_version_cache()
        except Exception:
            conn.rollback()
            raise
    return {key: value for key, value in summary.items() if value}


def _record_shard_sync(conn, path: Path, dataset: str, dates: list[str], row_count: int) -> None:
    tables = tidb_tables()
    source_path = _path_label(path)
    month_key = path.stem.replace("_", "-")
    record = (
        source_path,
        dataset,
        month_key,
        len(dates),
        int(row_count),
        _sha256(path),
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )
    sql = f"""
        INSERT INTO {_q(tables.shard_syncs)}
            (source_path, dataset, month_key, date_count, row_count, sha256, synced_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            dataset = VALUES(dataset),
            month_key = VALUES(month_key),
            date_count = VALUES(date_count),
            row_count = VALUES(row_count),
            sha256 = VALUES(sha256),
            synced_at = VALUES(synced_at)
    """
    with conn.cursor() as cur:
        cur.execute(sql, record)


def _normalize_chart_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["stock_id"] = out["stock_id"].astype(str)
    out["date"] = pd.to_datetime(out["date"], format="mixed")
    for column in ("open", "high", "low", "close"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0).round().astype("int64")
    return out.dropna(subset=["date", "open", "high", "low", "close"])


def _sync_chart_day(conn, df: pd.DataFrame, dates: list[str]) -> int:
    tables = tidb_tables()
    _delete_table_dates(conn, tables.chart_day, "bar_date", dates)
    if df.empty:
        return 0
    out = _normalize_chart_df(df)
    if out.empty:
        return 0
    out["bar_date"] = out["date"].dt.strftime("%Y-%m-%d")
    if dates:
        out = out[out["bar_date"].isin(dates)]
    out = out.drop_duplicates(subset=["stock_id", "bar_date"], keep="last").sort_values(["stock_id", "bar_date"])

    def records() -> Iterable[tuple[Any, ...]]:
        for row in out.itertuples(index=False):
            yield (
                _clean_text(row.stock_id),
                _clean_text(row.bar_date),
                float(row.open),
                float(row.high),
                float(row.low),
                float(row.close),
                _int_or_zero(row.volume),
            )

    return _insert_many_iter(conn, tables.chart_day, CHART_DAY_COLUMNS, records())


def _sync_chart_m1(conn, df: pd.DataFrame, dates: list[str]) -> int:
    tables = tidb_tables()
    _delete_table_dates(conn, tables.chart_m1, "bar_date", dates)
    if df.empty:
        return 0
    out = _normalize_chart_df(df)
    if out.empty:
        return 0
    out["bar_date"] = out["date"].dt.strftime("%Y-%m-%d")
    if dates:
        out = out[out["bar_date"].isin(dates)]
    out = out.drop_duplicates(subset=["stock_id", "date"], keep="last").sort_values(["stock_id", "date"])

    def records() -> Iterable[tuple[Any, ...]]:
        for row in out.itertuples(index=False):
            bar_time = pd.Timestamp(row.date).strftime("%Y-%m-%d %H:%M:%S")
            yield (
                _clean_text(row.stock_id),
                bar_time,
                _clean_text(row.bar_date),
                float(row.open),
                float(row.high),
                float(row.low),
                float(row.close),
                _int_or_zero(row.volume),
            )

    return _insert_many_iter(conn, tables.chart_m1, CHART_M1_COLUMNS, records())


def sync_chart_date_to_tidb(
    date: str,
    *,
    day_df: pd.DataFrame | None = None,
    m1_df: pd.DataFrame | None = None,
    day_source_path: Path | str | None = None,
    m1_source_path: Path | str | None = None,
) -> dict[str, int]:
    """Replace one trading date's adjusted chart rows in TiDB."""
    date = _date_key(date)
    if not date:
        return {}

    summary: dict[str, int] = {}
    with connect_tidb() as conn:
        ensure_tidb_schema(conn)
        try:
            if day_df is not None:
                row_count = _sync_chart_day(conn, day_df, [date])
                if day_source_path is not None:
                    _record_shard_sync(conn, Path(day_source_path).resolve(), DATASET_CHART_DAY, [date], row_count)
                summary[DATASET_CHART_DAY] = row_count
            if m1_df is not None:
                row_count = _sync_chart_m1(conn, m1_df, [date])
                if m1_source_path is not None:
                    _record_shard_sync(conn, Path(m1_source_path).resolve(), DATASET_CHART_M1, [date], row_count)
                summary[DATASET_CHART_M1] = row_count
            conn.commit()
            _clear_version_cache()
        except Exception:
            conn.rollback()
            raise
    return summary


def _chart_rows_to_df(rows: list[dict[str, Any]], time_column: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["stock_id", "date", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows)
    df = df.rename(columns={time_column: "date"})
    df["stock_id"] = df["stock_id"].astype(str)
    df["date"] = pd.to_datetime(df["date"], format="mixed")
    for column in ("open", "high", "low", "close"):
        df[column] = pd.to_numeric(df[column], errors="coerce").astype("float32")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).round().astype("int64")
    return df[["stock_id", "date", "open", "high", "low", "close", "volume"]].sort_values("date").reset_index(drop=True)


def tidb_read_chart_day(
    stock_id: str,
    start_date: str | None,
    end_date: str,
    limit: int | None = 120,
) -> pd.DataFrame | None:
    if not tidb_chart_reads_enabled():
        return None
    end_date = _date_key(end_date)
    if not end_date:
        return None
    where = ["stock_id = %s", "bar_date <= %s"]
    params: list[Any] = [str(stock_id), end_date]
    if start_date:
        where.append("bar_date >= %s")
        params.append(_date_key(start_date))
    sql = (
        "SELECT stock_id, bar_date, open, high, low, close, volume "
        f"FROM {_q(tidb_tables().chart_day)} "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY bar_date DESC"
    )
    if limit:
        sql += " LIMIT %s"
        params.append(int(limit))
    with _read_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    if not rows:
        return None
    return _chart_rows_to_df(list(rows), "bar_date")


def tidb_read_chart_m1(
    stock_id: str,
    date: str,
    limit: int | None = 120,
    full_day: bool = False,
) -> pd.DataFrame | None:
    if not tidb_chart_reads_enabled():
        return None
    date = _date_key(date)
    if not date:
        return None
    where = ["stock_id = %s"]
    params: list[Any] = [str(stock_id)]
    if full_day:
        where.append("bar_date = %s")
        params.append(date)
        order = "ORDER BY bar_time ASC"
    else:
        where.append("bar_time <= %s")
        params.append(f"{date} 23:59:59")
        order = "ORDER BY bar_time DESC"
    sql = (
        "SELECT stock_id, bar_time, open, high, low, close, volume "
        f"FROM {_q(tidb_tables().chart_m1)} "
        f"WHERE {' AND '.join(where)} {order}"
    )
    if limit and not full_day:
        sql += " LIMIT %s"
        params.append(int(limit))
    with _read_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    if not rows:
        return None
    return _chart_rows_to_df(list(rows), "bar_time")


def tidb_chart_version(timeframe: str, date: str, pattern_type: str | None = None) -> str | None:
    """Return a compact version token for historical chart detail caches."""
    if not tidb_chart_reads_enabled():
        return None
    date = _date_key(date)
    if not date:
        return None
    timeframe = str(timeframe or "day")
    tables = tidb_tables()
    if timeframe == "day":
        chart_table = tables.chart_day
        date_column = "bar_date"
        chart_dataset = DATASET_CHART_DAY
    elif timeframe in {"1m", "3m", "5m"}:
        chart_table = tables.chart_m1
        date_column = "bar_date"
        chart_dataset = DATASET_CHART_M1
    else:
        return None

    cache_key = ("chart", timeframe, date, str(pattern_type or "none"))
    found, cached = _version_cache_get(cache_key)
    if found:
        return cached

    month_key = date[:7]
    datasets = [chart_dataset]
    if pattern_type not in (None, "", "none"):
        datasets.append(DATASET_PATTERN_SCAN)
    with _read_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT updated_at FROM {_q(chart_table)} "
            f"WHERE {_q(date_column)} = %s LIMIT 1",
            [date],
        )
        chart_row = cur.fetchone()
        if not chart_row:
            return _version_cache_put(cache_key, None)
        placeholders = ", ".join(["%s"] * len(datasets))
        cur.execute(
            f"SELECT dataset, row_count, sha256, synced_at "
            f"FROM {_q(tables.shard_syncs)} "
            f"WHERE month_key = %s AND dataset IN ({placeholders}) "
            "ORDER BY dataset",
            [month_key, *datasets],
        )
        sync_rows = cur.fetchall()

    token = {
        "date": date,
        "timeframe": timeframe,
        "chart_dataset": chart_dataset,
        "chart_updated_at": _clean_text(chart_row.get("updated_at")),
        "syncs": [
            {
                "dataset": _clean_text(row.get("dataset")),
                "row_count": int(row.get("row_count") or 0),
                "sha256": _clean_text(row.get("sha256")),
                "synced_at": _clean_text(row.get("synced_at")),
            }
            for row in sync_rows
        ],
    }
    raw = json.dumps(token, sort_keys=True, ensure_ascii=True, default=str, separators=(",", ":"))
    return _version_cache_put(cache_key, "tidb:" + hashlib.sha1(raw.encode("utf-8")).hexdigest())


def tidb_available_dates(dataset: str) -> list[str] | None:
    if not tidb_reads_enabled():
        return None
    tables = tidb_tables()
    table = {
        DATASET_PATTERN_SCAN: tables.pattern_scan,
        DATASET_VWAP_ACTIVITY: tables.vwap_activity,
        DATASET_VWAP_SIGNALS: tables.vwap_signals,
    }.get(dataset)
    if not table:
        raise RuntimeError(f"Unsupported dataset: {dataset}")
    with _read_connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT DISTINCT scan_date FROM {_q(table)} ORDER BY scan_date")
        rows = cur.fetchall()
    return [_date_key(row.get("scan_date")) for row in rows if _date_key(row.get("scan_date"))]


def tidb_dataset_version(dataset: str, date: str) -> str | None:
    """Return a compact version token for date-scoped offline datasets."""
    if not tidb_reads_enabled():
        return None
    date = _date_key(date)
    if not date:
        return None
    tables = tidb_tables()
    table = {
        DATASET_PATTERN_SCAN: tables.pattern_scan,
        DATASET_VWAP_ACTIVITY: tables.vwap_activity,
        DATASET_VWAP_SIGNALS: tables.vwap_signals,
    }.get(dataset)
    if not table:
        raise RuntimeError(f"Unsupported dataset: {dataset}")
    cache_key = ("dataset", dataset, date)
    found, cached = _version_cache_get(cache_key)
    if found:
        return cached
    month_key = date[:7]
    with _read_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT updated_at FROM {_q(table)} WHERE scan_date = %s LIMIT 1",
            [date],
        )
        data_row = cur.fetchone()
        if not data_row:
            return _version_cache_put(cache_key, None)
        cur.execute(
            f"SELECT row_count, sha256, synced_at "
            f"FROM {_q(tables.shard_syncs)} "
            "WHERE month_key = %s AND dataset = %s "
            "ORDER BY synced_at DESC LIMIT 1",
            [month_key, dataset],
        )
        sync_row = cur.fetchone() or {}
    token = {
        "dataset": dataset,
        "date": date,
        "updated_at": _clean_text(data_row.get("updated_at")),
        "sync_rows": int(sync_row.get("row_count") or 0),
        "sync_sha256": _clean_text(sync_row.get("sha256")),
        "synced_at": _clean_text(sync_row.get("synced_at")),
    }
    raw = json.dumps(token, sort_keys=True, ensure_ascii=True, default=str, separators=(",", ":"))
    return _version_cache_put(cache_key, "tidb:" + hashlib.sha1(raw.encode("utf-8")).hexdigest())


def _payload_from_pattern_row(row: dict[str, Any]) -> dict[str, Any]:
    payload = _json_loads(row.get("payload_json"))
    stock_name = _clean_text(row.get("stock_name") or payload.get("stock_name") or payload.get("name"))
    payload["scan_date"] = _date_key(row.get("scan_date", payload.get("scan_date", "")))
    payload["stock_id"] = _clean_text(row.get("stock_id", payload.get("stock_id", "")))
    payload["stock_name"] = stock_name
    payload["name"] = stock_name
    payload["pattern_type"] = _clean_text(row.get("pattern_type", payload.get("pattern_type", "")))
    payload["pattern_name"] = _clean_text(row.get("pattern_name", payload.get("pattern_name", "")))
    payload["timeframe"] = _clean_text(row.get("timeframe", payload.get("timeframe", "day"))) or "day"
    payload["score"] = float(row.get("score", payload.get("score", 0)) or 0)
    payload["event_date"] = _date_key(row.get("event_date", payload.get("event_date", "")))
    payload["in_tick_universe"] = True
    return payload


def _summary_from_pattern_row(row: dict[str, Any]) -> dict[str, Any]:
    stock_name = _clean_text(row.get("stock_name", ""))
    return {
        "scan_date": _date_key(row.get("scan_date", "")),
        "stock_id": _clean_text(row.get("stock_id", "")),
        "stock_name": stock_name,
        "name": stock_name,
        "pattern_type": _clean_text(row.get("pattern_type", "")),
        "pattern_name": _clean_text(row.get("pattern_name", "")),
        "timeframe": _clean_text(row.get("timeframe", "day")) or "day",
        "score": float(row.get("score", 0) or 0),
        "event_date": _date_key(row.get("event_date", "")),
        "in_tick_universe": True,
    }


def tidb_read_pattern_scan(
    date: str,
    pattern_types: Iterable[str],
    min_score: float = 60.0,
    *,
    include_payload: bool = True,
) -> list[dict[str, Any]] | None:
    if not tidb_reads_enabled():
        return None
    date = _date_key(date)
    if not date:
        return []
    selected = [_clean_text(value) for value in pattern_types if _clean_text(value)]
    columns = PATTERN_SCAN_COLUMNS if include_payload else PATTERN_SCAN_SUMMARY_COLUMNS
    where = ["scan_date = %s"]
    params: list[Any] = [date]
    if selected:
        where.append(f"pattern_type IN ({', '.join(['%s'] * len(selected))})")
        params.extend(selected)
    if min_score is not None:
        where.append("COALESCE(score, 0) >= %s")
        params.append(float(min_score))
    sql = (
        f"SELECT {', '.join(_q(column) for column in columns)} "
        f"FROM {_q(tidb_tables().pattern_scan)} "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY score DESC, stock_id ASC, pattern_type ASC"
    )
    with _read_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    if not rows:
        return None
    factory = _payload_from_pattern_row if include_payload else _summary_from_pattern_row
    return [factory(row) for row in rows]


def tidb_read_pattern_for_stock(
    stock_id: str,
    pattern_type: str,
    date: str,
    min_score: float = 60.0,
) -> dict[str, Any] | None:
    if not tidb_reads_enabled():
        return None
    date = _date_key(date)
    if not date:
        return None
    where = [
        "scan_date = %s",
        "stock_id = %s",
        "pattern_type = %s",
    ]
    params: list[Any] = [date, str(stock_id), str(pattern_type)]
    if min_score is not None:
        where.append("COALESCE(score, 0) >= %s")
        params.append(float(min_score))
    sql = (
        f"SELECT {', '.join(_q(column) for column in PATTERN_SCAN_COLUMNS)} "
        f"FROM {_q(tidb_tables().pattern_scan)} "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY score DESC LIMIT 1"
    )
    with _read_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return _payload_from_pattern_row(row) if row else None


def tidb_read_vwap_activity(date: str, stock_ids: set[str] | None = None) -> dict[str, dict] | None:
    if not tidb_reads_enabled():
        return None
    date = _date_key(date)
    if not date:
        return {}
    where = ["scan_date = %s"]
    params: list[Any] = [date]
    if stock_ids:
        ids = sorted(str(stock_id) for stock_id in stock_ids)
        where.append(f"stock_id IN ({', '.join(['%s'] * len(ids))})")
        params.extend(ids)
    sql = (
        "SELECT scan_date, stock_id, day_atr, open5_rng, vol5_pr "
        f"FROM {_q(tidb_tables().vwap_activity)} "
        f"WHERE {' AND '.join(where)}"
    )
    with _read_connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    if not rows:
        return None
    out: dict[str, dict] = {}
    for row in rows:
        sid = _clean_text(row.get("stock_id"))
        out[sid] = {
            "day_atr": _float_or_none(row.get("day_atr")),
            "open5_rng": _float_or_none(row.get("open5_rng")),
            "vol5_pr": _float_or_none(row.get("vol5_pr")),
        }
    return out


def _filter_bundle(bundle: dict[str, Any], stock_ids: set[str] | None) -> dict[str, Any]:
    if not stock_ids:
        return bundle
    allowed = {str(stock_id) for stock_id in stock_ids}
    return {
        "vwap": [row for row in bundle.get("vwap", []) if str(row.get("stock_id")) in allowed],
        "sr": [row for row in bundle.get("sr", []) if str(row.get("stock_id")) in allowed],
        "macd": {sid: row for sid, row in bundle.get("macd", {}).items() if str(sid) in allowed},
        "obv": {sid: row for sid, row in bundle.get("obv", {}).items() if str(sid) in allowed},
        "chg": {sid: row for sid, row in bundle.get("chg", {}).items() if str(sid) in allowed},
        "sr_levels": {sid: row for sid, row in bundle.get("sr_levels", {}).items() if str(sid) in allowed},
        "m1_bars": int(bundle.get("m1_bars") or 0),
    }


def tidb_read_vwap_signals(date: str, stock_ids: set[str] | None = None) -> dict[str, Any] | None:
    if not tidb_reads_enabled():
        return None
    date = _date_key(date)
    if not date:
        return _empty_bundle()
    tables = tidb_tables()
    bundle: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []
    with _read_connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT payload_gzip FROM {_q(tables.vwap_bundle)} WHERE scan_date = %s",
            [date],
        )
        bundle_row = cur.fetchone()
        if bundle_row and bundle_row.get("payload_gzip"):
            try:
                raw = gzip.decompress(bytes(bundle_row["payload_gzip"]))
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    bundle = loaded
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
                bundle = None
        if bundle is None:
            cur.execute(
                "SELECT scan_date, kind, stock_id, event_time, value, payload_json "
                f"FROM {_q(tables.vwap_signals)} WHERE scan_date = %s",
                [date],
            )
            rows = list(cur.fetchall())
    if bundle is None:
        if not rows:
            return None
        bundle = _bundle_from_signal_rows(rows)
    return _filter_bundle(bundle, stock_ids)
