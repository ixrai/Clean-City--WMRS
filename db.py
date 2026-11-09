import os
import re
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash
from config import DB_CONFIG, SQLITE_DB_PATH, DB_TYPE, BASE_DIR

# MySQL is optional: the app runs fully on SQLite without this package installed.
try:
    import mysql.connector
except ImportError:  # pragma: no cover
    mysql = None

_SQLITE_INITIALIZED = False

class RowDict(dict):
    """A dictionary that also supports integer indexing like a tuple (row[0])."""
    def __init__(self, fields, values):
        super().__init__(zip(fields, values))
        self._values = list(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def _parse_val(field_name, val):
    """Parses date/timestamp strings from SQLite into standard datetime objects."""
    if val and (field_name.endswith('_at') or field_name == 'date') and isinstance(val, str):
        try:
            return datetime.strptime(val[:19], '%Y-%m-%d %H:%M:%S')
        except Exception:
            try:
                return datetime.fromisoformat(val)
            except Exception:
                return val
    return val


def dict_factory(cursor, row):
    """Row factory converting SQLite tuples into RowDict objects with parsed datetimes."""
    fields = [column[0] for column in cursor.description]
    parsed_values = [_parse_val(f, v) for f, v in zip(fields, row)]
    return RowDict(fields, parsed_values)


def _translate_field_func(match):
    """Translates MySQL FIELD(col, 'v1', 'v2'...) to standard SQL CASE col WHEN 'v1' THEN 1 ... END."""
    args = [a.strip() for a in match.group(1).split(',')]
    if len(args) < 2:
        return match.group(0)
    col = args[0]
    cases = ' '.join(f"WHEN {val} THEN {i+1}" for i, val in enumerate(args[1:]))
    return f"CASE {col} {cases} ELSE 999 END"


class SQLiteCursorWrapper:
    """Wrapper around sqlite3.Cursor providing a MySQL-connector compatible interface."""
    def __init__(self, raw_cursor, as_dict=True):
        self._cursor = raw_cursor
        self._as_dict = as_dict

    def execute(self, sql, params=None):
        # 1. Translate MySQL's SELECT DATABASE()
        if "SELECT DATABASE()" in sql.upper():
            sql = "SELECT 'waste_management' AS `DATABASE()`"

        # 2. Translate MySQL FIELD() function
        if "FIELD(" in sql.upper():
            sql = re.sub(r'FIELD\s*\((.*?)\)', _translate_field_func, sql, flags=re.IGNORECASE)

        # 3. Translate MySQL ON DUPLICATE KEY UPDATE (specifically used for feedback)
        if "ON DUPLICATE KEY UPDATE" in sql.upper():
            sql = re.sub(
                r"ON DUPLICATE KEY UPDATE\s+rating\s*=\s*VALUES\s*\(\s*rating\s*\)\s*,\s*comments\s*=\s*VALUES\s*\(\s*comments\s*\)",
                "ON CONFLICT(complaint_id) DO UPDATE SET rating = excluded.rating, comments = excluded.comments",
                sql,
                flags=re.IGNORECASE
            )

        # 4. Translate MySQL NOW() to SQLite CURRENT_TIMESTAMP (SQLite has no NOW())
        if "NOW()" in sql.upper():
            sql = re.sub(r'NOW\s*\(\s*\)', 'CURRENT_TIMESTAMP', sql, flags=re.IGNORECASE)

        # 5. Translate MySQL %s placeholders to SQLite ? placeholders
        if params is not None:
            converted_sql = re.sub(r'(?<!%)%s', '?', sql)
            converted_sql = converted_sql.replace('%%', '%')
            if isinstance(params, list):
                params = tuple(params)
            return self._cursor.execute(converted_sql, params)
        else:
            return self._cursor.execute(sql)

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        if not self._as_dict and isinstance(row, RowDict):
            return tuple(row._values)
        return row

    def fetchall(self):
        rows = self._cursor.fetchall()
        if not self._as_dict:
            return [tuple(r._values) if isinstance(r, RowDict) else r for r in rows]
        return rows

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description


class SQLiteConnectionWrapper:
    """Wrapper around sqlite3.Connection to mimic mysql.connector connection."""
    def __init__(self, raw_conn):
        self._conn = raw_conn
        self._is_closed = False

    def cursor(self, dictionary=True):
        raw_cur = self._conn.cursor()
        return SQLiteCursorWrapper(raw_cur, as_dict=dictionary)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def is_connected(self):
        return not self._is_closed

    def close(self):
        self._is_closed = True
        try:
            self._conn.close()
        except Exception:
            pass


def init_sqlite_database():
    """Initializes the SQLite fallback database schema and default seeds."""
    global _SQLITE_INITIALIZED
    os.makedirs(os.path.dirname(SQLITE_DB_PATH), exist_ok=True)
    
    conn = sqlite3.connect(SQLITE_DB_PATH)
    cur = conn.cursor()

    # Check if users table already exists
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    if not cur.fetchone():
        schema_file = os.path.join(BASE_DIR, 'database', 'schema_sqlite.sql')
        if os.path.exists(schema_file):
            with open(schema_file, 'r', encoding='utf-8') as f:
                conn.executescript(f.read())
        
        # Seed the first administrator and staff accounts. Demo credentials are
        # permitted only when DEMO_MODE is enabled; production must supply
        # unique initial passwords through environment variables.
        from config import DEMO_MODE
        admin_password = os.getenv("INITIAL_ADMIN_PASSWORD")
        staff_password = os.getenv("INITIAL_STAFF_PASSWORD")
        if DEMO_MODE:
            admin_password = admin_password or "Admin@123"
            staff_password = staff_password or "Staff@123"
        elif not admin_password or not staff_password:
            raise RuntimeError(
                "Set INITIAL_ADMIN_PASSWORD and INITIAL_STAFF_PASSWORD before "
                "starting a fresh production database."
            )

        admin_pass = generate_password_hash(admin_password)
        cur.execute("""
            INSERT OR IGNORE INTO users (name, email, password_hash, phone, role)
            VALUES (?, ?, ?, ?, ?)
        """, ('Chief Sanitation Officer', 'admin@wastemanage.gov', admin_pass, '9876543210', 'admin'))

        # Seed the first staff account
        staff_pass = generate_password_hash(staff_password)
        cur.execute("""
            INSERT OR IGNORE INTO users (name, email, password_hash, phone, role)
            VALUES (?, ?, ?, ?, ?)
        """, ('Ramesh Kumar (Staff)', 'staff@wastemanage.gov', staff_pass, '9123456780', 'staff'))
        staff_user_id = cur.lastrowid or 2

        cur.execute("""
            INSERT OR IGNORE INTO staff (user_id, department_id, employee_code, designation, assigned_zone, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (staff_user_id, 1, 'EMP-SWM-101', 'Field Supervisor', 'Central Ward', 'available'))

        conn.commit()

    conn.close()
    _SQLITE_INITIALIZED = True


def get_sqlite_connection():
    """Returns a SQLite connection wrapped with MySQL-connector compatibility."""
    global _SQLITE_INITIALIZED
    if not _SQLITE_INITIALIZED or not os.path.exists(SQLITE_DB_PATH):
        init_sqlite_database()
    
    raw_conn = sqlite3.connect(SQLITE_DB_PATH, timeout=20.0)
    raw_conn.row_factory = dict_factory
    raw_conn.execute("PRAGMA foreign_keys = ON")
    return SQLiteConnectionWrapper(raw_conn)


def get_connection():
    """
    Returns a database connection according to DB_TYPE:
    - "sqlite" (default): local SQLite file, zero setup.
    - "mysql": MySQL only; raises a clear error if it cannot connect.
    - "auto": try MySQL first, fall back to SQLite if it is unreachable.
    """
    if DB_TYPE == "sqlite":
        return get_sqlite_connection()

    if mysql is None:
        if DB_TYPE == "mysql":
            raise RuntimeError(
                "DB_TYPE=mysql but 'mysql-connector-python' is not installed. "
                "Run: pip install mysql-connector-python"
            )
        return get_sqlite_connection()

    try:
        return mysql.connector.connect(**DB_CONFIG)
    except Exception:
        if DB_TYPE == "mysql":
            raise
        # Auto mode: fall back to SQLite seamlessly
        return get_sqlite_connection()


def get_db_cursor(dictionary=True):
    """
    Helper returning both connection and cursor.
    Supports both MySQL and SQLite seamlessly.
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=dictionary)
    return conn, cursor
