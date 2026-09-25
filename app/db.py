"""قاعدة بيانات SQLite بسيطة لتتبّع الزوّار وحصص الاستخدام والاشتراكات."""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS visitors (
    id                     TEXT PRIMARY KEY,
    used                   INTEGER NOT NULL DEFAULT 0,
    plan                   TEXT    NOT NULL DEFAULT 'free',
    stripe_customer_id     TEXT,
    stripe_subscription_id TEXT,
    created_at             TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at             TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_visitors_subscription ON visitors(stripe_subscription_id);
"""

PLAN_FREE, PLAN_PRO = "free", "pro"


@dataclass
class Usage:
    plan: str
    used: int
    limit: int | None  # None = غير محدود

    @property
    def is_pro(self) -> bool:
        return self.plan == PLAN_PRO

    @property
    def remaining(self) -> int | None:
        return None if self.limit is None else max(self.limit - self.used, 0)

    def to_dict(self) -> dict:
        return {
            "plan": self.plan,
            "used": self.used,
            "limit": self.limit,
            "remaining": self.remaining,
        }


_initialized: set[str] = set()


@contextmanager
def _connect():
    path = Path(settings.database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    if str(path) not in _initialized:
        conn.executescript(SCHEMA)
        _initialized.add(str(path))
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with _connect():
        pass  # _connect ينشئ الجداول عند أول اتصال


def _ensure(conn: sqlite3.Connection, visitor_id: str) -> None:
    conn.execute("INSERT OR IGNORE INTO visitors (id) VALUES (?)", (visitor_id,))


def get_usage(visitor_id: str) -> Usage:
    with _connect() as conn:
        _ensure(conn, visitor_id)
        row = conn.execute("SELECT plan, used FROM visitors WHERE id = ?", (visitor_id,)).fetchone()
    is_pro = row["plan"] == PLAN_PRO
    return Usage(plan=row["plan"], used=row["used"], limit=None if is_pro else settings.free_summary_limit)


def try_consume(visitor_id: str) -> bool:
    """يحجز محاولة واحدة ذريًّا. يعيد False إن انتهت الحصة المجانية."""
    with _connect() as conn:
        _ensure(conn, visitor_id)
        cur = conn.execute(
            """UPDATE visitors SET used = used + 1, updated_at = CURRENT_TIMESTAMP
               WHERE id = ? AND (plan = ? OR used < ?)""",
            (visitor_id, PLAN_PRO, settings.free_summary_limit),
        )
        return cur.rowcount == 1


def refund(visitor_id: str) -> None:
    """يعيد المحاولة المحجوزة عند فشل التلخيص، حتى لا تُحتسب على الزائر."""
    with _connect() as conn:
        conn.execute(
            """UPDATE visitors SET used = MAX(used - 1, 0), updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (visitor_id,),
        )


def activate_pro(visitor_id: str, customer_id: str | None, subscription_id: str | None) -> None:
    with _connect() as conn:
        _ensure(conn, visitor_id)
        conn.execute(
            """UPDATE visitors
               SET plan = ?, stripe_customer_id = COALESCE(?, stripe_customer_id),
                   stripe_subscription_id = COALESCE(?, stripe_subscription_id),
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (PLAN_PRO, customer_id, subscription_id, visitor_id),
        )


def set_plan_by_subscription(subscription_id: str, plan: str) -> int:
    """يغيّر خطة الزائر المرتبط باشتراك Stripe (مثلًا عند الإلغاء). يعيد عدد الصفوف المتأثرة."""
    with _connect() as conn:
        cur = conn.execute(
            """UPDATE visitors SET plan = ?, updated_at = CURRENT_TIMESTAMP
               WHERE stripe_subscription_id = ?""",
            (plan, subscription_id),
        )
        return cur.rowcount
