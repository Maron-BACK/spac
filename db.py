"""
SQLite 데이터베이스 모듈
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "spac.db"


@contextmanager
def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS spacs (
            ticker TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            listing_date TEXT,
            payment_date TEXT,
            offering_price REAL,
            rate_y1 REAL,
            rate_y2 REAL,
            rate_y3 REAL,
            rate_source TEXT,
            rate_note TEXT,
            current_price REAL,
            delisted_at TEXT,
            last_synced TEXT,
            manual_override INTEGER DEFAULT 0,
            note TEXT
        );

        CREATE TABLE IF NOT EXISTS ksfc_rates (
            asof_date TEXT PRIMARY KEY,
            rate REAL NOT NULL,
            fetched_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)


# ---------- settings ----------

def get_setting(key, default=None):
    with get_conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with get_conn() as c:
        c.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


# ---------- spacs ----------

ALLOWED_COLS = {
    "ticker", "name", "listing_date", "payment_date", "offering_price",
    "rate_y1", "rate_y2", "rate_y3", "rate_source", "rate_note",
    "current_price", "delisted_at", "last_synced", "manual_override", "note",
}


def upsert_spac(data: dict):
    """
    ticker 기준 upsert.
    - 기존 행: 제공된 컬럼만 UPDATE
    - 신규 행: INSERT (name 필수)
    """
    data = {k: v for k, v in data.items() if k in ALLOWED_COLS}
    if "ticker" not in data:
        raise ValueError("ticker is required")

    ticker = data["ticker"]
    with get_conn() as c:
        row = c.execute("SELECT ticker FROM spacs WHERE ticker=?", (ticker,)).fetchone()
        if row:
            update_cols = [k for k in data.keys() if k != "ticker"]
            if not update_cols:
                return
            set_clause = ",".join(f"{c}=?" for c in update_cols)
            params = [data[k] for k in update_cols] + [ticker]
            c.execute(f"UPDATE spacs SET {set_clause} WHERE ticker=?", params)
        else:
            if not data.get("name"):
                raise ValueError(
                    f"신규 종목 {ticker} 등록 시 name이 필요합니다."
                )
            cols = list(data.keys())
            placeholders = ",".join("?" * len(cols))
            col_list = ",".join(cols)
            c.execute(
                f"INSERT INTO spacs({col_list}) VALUES({placeholders})",
                [data[k] for k in cols],
            )


def mark_delisted(ticker: str, asof: str):
    with get_conn() as c:
        c.execute(
            "UPDATE spacs SET delisted_at=? WHERE ticker=? AND delisted_at IS NULL",
            (asof, ticker),
        )


def clear_delisted_if_relisted(ticker: str):
    with get_conn() as c:
        c.execute("UPDATE spacs SET delisted_at=NULL WHERE ticker=?", (ticker,))


def list_active_tickers():
    with get_conn() as c:
        rows = c.execute(
            "SELECT ticker FROM spacs WHERE delisted_at IS NULL"
        ).fetchall()
        return [r["ticker"] for r in rows]


def get_all_spacs(include_delisted=False):
    with get_conn() as c:
        if include_delisted:
            rows = c.execute("SELECT * FROM spacs ORDER BY payment_date").fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM spacs WHERE delisted_at IS NULL ORDER BY payment_date"
            ).fetchall()
        return [dict(r) for r in rows]


# ---------- ksfc rates ----------

def upsert_ksfc_rate(asof_date: str, rate: float):
    with get_conn() as c:
        c.execute(
            "INSERT INTO ksfc_rates(asof_date, rate, fetched_at) VALUES(?,?,?) "
            "ON CONFLICT(asof_date) DO UPDATE SET rate=excluded.rate, fetched_at=excluded.fetched_at",
            (asof_date, rate, datetime.now().isoformat(timespec="seconds")),
        )


def get_ksfc_rate_on(date_str: str):
    with get_conn() as c:
        row = c.execute(
            "SELECT rate FROM ksfc_rates WHERE asof_date<=? ORDER BY asof_date DESC LIMIT 1",
            (date_str,),
        ).fetchone()
        return row["rate"] if row else None


def get_latest_ksfc_rate():
    with get_conn() as c:
        row = c.execute(
            "SELECT asof_date, rate FROM ksfc_rates ORDER BY asof_date DESC LIMIT 1"
        ).fetchone()
        return (row["asof_date"], row["rate"]) if row else (None, None)


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {DB_PATH}")
