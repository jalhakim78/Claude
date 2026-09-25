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
    paypal_subscription_id TEXT,
    email                  TEXT,
    created_at             TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at             TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_visitors_subscription ON visitors(stripe_subscription_id);

-- رموز الدخول المؤقتة (تُخزَّن مجزّأة، لا كنص صريح)
CREATE TABLE IF NOT EXISTS login_codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT    NOT NULL,
    code_hash  TEXT    NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    expires_at REAL    NOT NULL,
    created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_codes_email ON login_codes(email, created_at);

-- سجل طلبات الرموز لتحديد معدل الإرسال لكل بريد
CREATE TABLE IF NOT EXISTS code_requests (
    email      TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_code_requests_email ON code_requests(email, created_at);

-- جلسات الدخول: الكوكي يحمل رمزًا عشوائيًا، ونحفظ هنا مجزّأه فقط
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    visitor_id TEXT NOT NULL REFERENCES visitors(id),
    expires_at REAL NOT NULL,
    created_at REAL NOT NULL
);
"""

# أعمدة أُضيفت بعد الإصدار الأول؛ تُضاف تلقائيًا لقواعد البيانات الموجودة
MIGRATIONS = {
    "paypal_subscription_id": "ALTER TABLE visitors ADD COLUMN paypal_subscription_id TEXT",
    "email": "ALTER TABLE visitors ADD COLUMN email TEXT",
}
POST_MIGRATION_SQL = """
CREATE INDEX IF NOT EXISTS idx_visitors_paypal ON visitors(paypal_subscription_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_visitors_email ON visitors(email) WHERE email IS NOT NULL;
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
        _migrate(conn)
        _initialized.add(str(path))
    try:
        yield conn
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(visitors)")}
    for column, sql in MIGRATIONS.items():
        if column not in columns:
            conn.execute(sql)
    conn.executescript(POST_MIGRATION_SQL)


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


def activate_pro_paypal(visitor_id: str, subscription_id: str) -> None:
    with _connect() as conn:
        _ensure(conn, visitor_id)
        conn.execute(
            """UPDATE visitors SET plan = ?, paypal_subscription_id = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (PLAN_PRO, subscription_id, visitor_id),
        )


def set_plan_by_paypal_subscription(subscription_id: str, plan: str) -> int:
    """يغيّر خطة الزائر المرتبط باشتراك PayPal. يعيد عدد الصفوف المتأثرة."""
    with _connect() as conn:
        cur = conn.execute(
            """UPDATE visitors SET plan = ?, updated_at = CURRENT_TIMESTAMP
               WHERE paypal_subscription_id = ?""",
            (plan, subscription_id),
        )
        return cur.rowcount


# ---------- الحسابات (زائر مرتبط ببريد إلكتروني) ----------

def get_email(visitor_id: str) -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT email FROM visitors WHERE id = ?", (visitor_id,)).fetchone()
    return row["email"] if row else None


def find_by_email(email: str) -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT id FROM visitors WHERE email = ?", (email,)).fetchone()
    return row["id"] if row else None


def claim_email(visitor_id: str, email: str) -> None:
    """يحوّل الزائر المجهول إلى حساب مرتبط بالبريد."""
    with _connect() as conn:
        _ensure(conn, visitor_id)
        conn.execute(
            "UPDATE visitors SET email = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (email, visitor_id),
        )


def create_account(visitor_id: str, email: str, used: int) -> None:
    with _connect() as conn:
        conn.execute("INSERT INTO visitors (id, email, used) VALUES (?, ?, ?)", (visitor_id, email, used))


def merge_into(source_id: str, target_id: str) -> None:
    """يدمج استخدام واشتراكات زائر مجهول في حساب موجود.

    - عدد المحاولات المستخدمة = الأكبر بينهما (حتى لا يُصفَّر العداد بتسجيل الدخول).
    - إن كان الزائر المجهول مدفوعًا تنتقل الخطة واشتراكاته للحساب.
    """
    with _connect() as conn:
        src = conn.execute("SELECT * FROM visitors WHERE id = ?", (source_id,)).fetchone()
        if src is None or source_id == target_id:
            return
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """UPDATE visitors SET
                 used = MAX(used, ?),
                 plan = CASE WHEN ? = 'pro' THEN 'pro' ELSE plan END,
                 stripe_customer_id = COALESCE(?, stripe_customer_id),
                 stripe_subscription_id = COALESCE(?, stripe_subscription_id),
                 paypal_subscription_id = COALESCE(?, paypal_subscription_id),
                 updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (src["used"], src["plan"], src["stripe_customer_id"], src["stripe_subscription_id"],
             src["paypal_subscription_id"], target_id),
        )
        # نزيل الاشتراكات من السجل المجهول حتى تصل أحداث الـ Webhook للحساب وحده
        conn.execute(
            """UPDATE visitors SET plan = 'free', stripe_customer_id = NULL,
                 stripe_subscription_id = NULL, paypal_subscription_id = NULL,
                 updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (source_id,),
        )
        conn.execute("COMMIT")


def get_used(visitor_id: str) -> int:
    with _connect() as conn:
        row = conn.execute("SELECT used FROM visitors WHERE id = ?", (visitor_id,)).fetchone()
    return row["used"] if row else 0


# ---------- رموز الدخول ----------

def add_login_code(email: str, code_hash: str, expires_at: float, now: float) -> None:
    with _connect() as conn:
        # رمز واحد صالح لكل بريد: الرمز الجديد يُبطل ما قبله
        conn.execute("DELETE FROM login_codes WHERE email = ?", (email,))
        conn.execute(
            "INSERT INTO login_codes (email, code_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (email, code_hash, expires_at, now),
        )


def count_codes_since(email: str, since: float) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM code_requests WHERE email = ? AND created_at >= ?",
            (email, since),
        ).fetchone()
    return row["n"]


def log_code_request(email: str, now: float) -> None:
    with _connect() as conn:
        conn.execute("INSERT INTO code_requests (email, created_at) VALUES (?, ?)", (email, now))
        conn.execute("DELETE FROM code_requests WHERE created_at < ?", (now - 86400,))


def get_login_code(email: str):
    with _connect() as conn:
        return conn.execute(
            "SELECT id, code_hash, attempts, expires_at FROM login_codes WHERE email = ?", (email,)
        ).fetchone()


def add_code_attempt(code_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE login_codes SET attempts = attempts + 1 WHERE id = ?", (code_id,))


def delete_login_codes(email: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM login_codes WHERE email = ?", (email,))


# ---------- الجلسات ----------

def create_session(token_hash: str, visitor_id: str, expires_at: float, now: float) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, visitor_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token_hash, visitor_id, expires_at, now),
        )
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))


def session_visitor(token_hash: str, now: float) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT visitor_id FROM sessions WHERE token_hash = ? AND expires_at > ?", (token_hash, now)
        ).fetchone()
    return row["visitor_id"] if row else None


def delete_session(token_hash: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
