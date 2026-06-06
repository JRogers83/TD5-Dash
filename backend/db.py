"""
Configuration database — SQLite storage for runtime settings and page visibility.

Schema design rationale:
    The `settings` table uses a simple key/value approach with TEXT values rather than
    a wide typed schema. This handles the current heterogeneous value types (floats for
    throttle calibration and GPS coordinates, strings for location names, integers for
    brightness levels) without requiring schema migrations when new settings are added.
    Callers convert types as needed via the typed helper `get_float()` / `get_int()`.

    The `pages` table stores per-layer visibility flags. Layers are identified by a
    string key (e.g. 'engine_detail') with an integer enabled flag (0 or 1). Layer
    visibility is read at startup and applied to the nav stack; changes require a
    restart to take effect.

Database location: data/td5dash.db (relative to repo root).
Created automatically on first run with sensible defaults.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Database lives in data/ at the repo root
_REPO_DIR = Path(__file__).resolve().parent.parent
_DB_DIR = _REPO_DIR / "data"
_DB_PATH = _DB_DIR / "td5dash.db"

# ── Default seed values ─────────────────────────────────────────────────────

_SETTINGS_DEFAULTS: dict[str, str] = {
    "throttle_idle": "18.0",
    "throttle_wot": "90.0",
    "brightness_day": "180",
    "brightness_night": "80",
    # weather_lat, weather_lon, weather_location are seeded dynamically
    # from .env at first run (see _seed_weather_defaults)
}

_PAGES_DEFAULTS: dict[str, int] = {
    "engine_detail": 1,
    "engine_stats": 1,
    "engine_raw": 1,
    "settings_diagnostics": 0,
}

_DEV_MODE = os.getenv("DEV_MODE", "0") == "1"


# ── Connection helper ────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    """Return a connection to the database, creating the file if needed."""
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ── Initialisation ───────────────────────────────────────────────────────────

def init_db() -> None:
    """
    Create tables and seed defaults on first run.

    Safe to call on every startup — uses INSERT OR IGNORE so existing values
    are never overwritten (except in DEV_MODE where pages are force-reset).
    """
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pages (
                key     TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS engine_history (
                ts      INTEGER NOT NULL,
                rpm     REAL,
                speed   REAL,
                coolant REAL,
                boost   REAL,
                throttle REAL,
                battery REAL,
                fuel_temp REAL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_ts ON engine_history(ts)"
        )
        conn.commit()

        # Seed settings defaults
        _seed_settings(conn)

        # Seed pages defaults (DEV_MODE forces all enabled)
        _seed_pages(conn)

        conn.commit()
        log.info("Database ready at %s", _DB_PATH)
    finally:
        conn.close()


def _seed_settings(conn: sqlite3.Connection) -> None:
    """Insert default settings if not already present."""
    for key, value in _SETTINGS_DEFAULTS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )

    # Weather location: seed from .env if present, otherwise hardcoded fallback
    weather_lat = os.getenv("WEATHER_LAT", "52.6309")
    weather_lon = os.getenv("WEATHER_LON", "1.2974")
    weather_loc = os.getenv("WEATHER_LOCATION", "Norwich, UK")

    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        ("weather_lat", weather_lat),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        ("weather_lon", weather_lon),
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        ("weather_location", weather_loc),
    )


def _seed_pages(conn: sqlite3.Connection) -> None:
    """Insert default page visibility flags."""
    if _DEV_MODE:
        # DEV_MODE: force all pages enabled on every startup
        for key in _PAGES_DEFAULTS:
            conn.execute(
                "INSERT OR REPLACE INTO pages (key, enabled) VALUES (?, 1)",
                (key,),
            )
        log.info("DEV_MODE: all page toggles set to enabled")
    else:
        for key, enabled in _PAGES_DEFAULTS.items():
            conn.execute(
                "INSERT OR IGNORE INTO pages (key, enabled) VALUES (?, ?)",
                (key, enabled),
            )


# ── Settings helpers ─────────────────────────────────────────────────────────

def get_all_settings() -> dict[str, str]:
    """Return all settings as a {key: value} dict."""
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {k: v for k, v in rows}
    finally:
        conn.close()


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """Return a single setting value, or default if not found."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else default
    finally:
        conn.close()


def get_float(key: str, default: float = 0.0) -> float:
    """Return a setting as a float, with fallback."""
    val = get_setting(key)
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def get_int(key: str, default: int = 0) -> int:
    """Return a setting as an int, with fallback."""
    val = get_setting(key)
    if val is None:
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def set_settings(updates: dict[str, str]) -> None:
    """Write one or more key/value pairs to the settings table."""
    conn = _get_conn()
    try:
        for key, value in updates.items():
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, str(value)),
            )
        conn.commit()
    finally:
        conn.close()


# ── Pages helpers ────────────────────────────────────────────────────────────

def get_all_pages() -> dict[str, int]:
    """Return all page visibility flags as {key: enabled}."""
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT key, enabled FROM pages").fetchall()
        return {k: e for k, e in rows}
    finally:
        conn.close()


def set_pages(updates: dict[str, int]) -> None:
    """Update one or more page visibility flags."""
    conn = _get_conn()
    try:
        for key, enabled in updates.items():
            conn.execute(
                "INSERT OR REPLACE INTO pages (key, enabled) VALUES (?, ?)",
                (key, int(bool(enabled))),
            )
        conn.commit()
    finally:
        conn.close()


# ── Engine history helpers ───────────────────────────────────────────────────

_HISTORY_RETENTION_DAYS = int(os.getenv("HISTORY_RETENTION_DAYS", "365"))


def insert_history(data: dict) -> None:
    """Insert a single engine history row. Called at ~10s cadence."""
    import time
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT INTO engine_history
               (ts, rpm, speed, coolant, boost, throttle, battery, fuel_temp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(time.time()),
                data.get("rpm"),
                data.get("road_speed_kph"),
                data.get("coolant_temp_c"),
                data.get("boost_bar"),
                data.get("throttle_pct"),
                data.get("battery_v"),
                data.get("fuel_temp_c"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def purge_old_history() -> int:
    """Delete history rows older than HISTORY_RETENTION_DAYS. Returns count deleted."""
    import time
    cutoff = int(time.time()) - (_HISTORY_RETENTION_DAYS * 86400)
    conn = _get_conn()
    try:
        cur = conn.execute("DELETE FROM engine_history WHERE ts < ?", (cutoff,))
        conn.commit()
        deleted = cur.rowcount
        if deleted > 0:
            log.info("Purged %d history rows older than %d days", deleted, _HISTORY_RETENTION_DAYS)
        return deleted
    finally:
        conn.close()


def get_history(range_name: str) -> list[dict]:
    """
    Return history rows for the given time range.

    range_name: hour, day, week, month, year, all
    """
    import time
    now = int(time.time())
    ranges = {
        "hour":  3600,
        "day":   86400,
        "week":  604800,
        "month": 2592000,
        "year":  31536000,
    }
    cutoff = now - ranges.get(range_name, 0) if range_name != "all" else 0

    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT ts, rpm, speed, coolant, boost, throttle, battery, fuel_temp "
            "FROM engine_history WHERE ts >= ? ORDER BY ts",
            (cutoff,),
        ).fetchall()
        return [
            {"ts": r[0], "rpm": r[1], "speed": r[2], "coolant": r[3],
             "boost": r[4], "throttle": r[5], "battery": r[6], "fuel_temp": r[7]}
            for r in rows
        ]
    finally:
        conn.close()


def wal_checkpoint() -> None:
    """Flush the SQLite WAL journal to the main database file.

    Safe to call at any time. Used during graceful shutdown to ensure no
    pending writes are left in the WAL when power is cut.
    Uses a separate connection so it does not interfere with in-flight queries.
    """
    try:
        with sqlite3.connect(str(_DB_PATH)) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        log.debug("WAL checkpoint complete")
    except Exception as exc:
        log.warning("WAL checkpoint failed: %s", exc)
