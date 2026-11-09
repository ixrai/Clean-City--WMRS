
import os
import re
import sqlite3
from datetime import datetime

from werkzeug.security import generate_password_hash
from config import DB_CONFIG, SQLITE_DB_PATH, DB_TYPE, BASE_DIR

# MySQL is optional.
try:
    import mysql.connector
except ImportError:
    mysql = None


_SQLITE_INITIALIZED = False

ADMIN_EMAIL = "admin@wrms.gov"
STAFF_EMAIL = "staff@wrms.gov"

ADMIN_OLD_EMAIL = "admin@wastemanage.gov"
STAFF_OLD_EMAIL = "staff@wastemanage.gov"


def _get_admin_password():
    return os.getenv("INITIAL_ADMIN_PASSWORD", "Admin@123")


def _get_staff_password():
    return os.getenv("INITIAL_STAFF_PASSWORD", "Staff@123")


class RowDict(dict):
    """Dictionary that also supports integer indexing like a tuple."""

    def __init__(self, fields, values):
        super().__init__(zip(fields, values))
        self._values = list(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def _parse_val(field_name, val):
    """Parse date/timestamp strings from SQLite."""

    if val and (
        field_name.endswith('_at') or field_name == 'date'
    ) and isinstance(val, str):

        try:
            return datetime.strptime(
                val[:19], '%Y-%m-%d %H:%M:%S'
            )
        except Exception:
            try:
                return datetime.fromisoformat(val)
            except Exception:
                return val

    return val


def dict_factory(cursor, row):
    """Convert SQLite rows into RowDict objects."""

    fields = [column[0] for column in cursor.description]

    parsed_values = [
        _parse_val(field, value)
        for field, value in zip(fields, row)
    ]

    return RowDict(fields, parsed_values)


def _translate_field_func(match):
    """Translate MySQL FIELD() into SQLite CASE."""

    args = [
        argument.strip()
        for argument in match.group(1).split(',')
    ]

    if len(args) < 2:
        return match.group(0)

    column = args[0]

    cases = ' '.join(
        f"WHEN {value} THEN {index + 1}"
        for index, value in enumerate(args[1:])
    )

    return f"CASE {column} {cases} ELSE 999 END"


class SQLiteCursorWrapper:
    """SQLite cursor with MySQL-compatible SQL handling."""

    def __init__(self, raw_cursor, as_dict=True):
        self._cursor = raw_cursor
        self._as_dict = as_dict

    def execute(self, sql, params=None):

        # Translate SELECT DATABASE()
        if "SELECT DATABASE()" in sql.upper():
            sql = "SELECT 'waste_management' AS `DATABASE()`"

        # Translate MySQL FIELD()
        if "FIELD(" in sql.upper():
            sql = re.sub(
                r'FIELD\s*\((.*?)\)',
                _translate_field_func,
                sql,
                flags=re.IGNORECASE
            )

        # Translate ON DUPLICATE KEY UPDATE.
        if "ON DUPLICATE KEY UPDATE" in sql.upper():
            sql = re.sub(
                r"ON DUPLICATE KEY UPDATE\s+rating\s*=\s*VALUES\s*\(\s*rating\s*\)\s*,\s*comments\s*=\s*VALUES\s*\(\s*comments\s*\)",
                "ON CONFLICT(complaint_id) DO UPDATE SET "
                "rating = excluded.rating, "
                "comments = excluded.comments",
                sql,
                flags=re.IGNORECASE
            )

        # Translate NOW()
        if "NOW()" in sql.upper():
            sql = re.sub(
                r'NOW\s*\(\s*\)',
                'CURRENT_TIMESTAMP',
                sql,
                flags=re.IGNORECASE
            )

        # Translate MySQL placeholders.
        if params is not None:
            converted_sql = re.sub(r'(?<!%)%s', '?', sql)
            converted_sql = converted_sql.replace('%%', '%')

            if isinstance(params, list):
                params = tuple(params)

            return self._cursor.execute(converted_sql, params)

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
            return [
                tuple(row._values)
                if isinstance(row, RowDict)
                else row
                for row in rows
            ]

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
    """SQLite connection compatible with mysql.connector usage."""

    def __init__(self, raw_conn):
        self._conn = raw_conn
        self._is_closed = False

    def cursor(self, dictionary=True):
        raw_cursor = self._conn.cursor()

        return SQLiteCursorWrapper(
            raw_cursor,
            as_dict=dictionary
        )

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


def _find_account(cur, new_email, old_email):
    """Find new account first, otherwise find the old email."""

    cur.execute(
        "SELECT user_id FROM users WHERE email = ?",
        (new_email,)
    )

    account = cur.fetchone()

    if account:
        return account[0]

    cur.execute(
        "SELECT user_id FROM users WHERE email = ?",
        (old_email,)
    )

    account = cur.fetchone()

    return account[0] if account else None


def _seed_sqlite_accounts(cur):
    """Create or update the default admin and staff accounts."""

    # ---------------- ADMIN ----------------

    admin_id = _find_account(
        cur,
        ADMIN_EMAIL,
        ADMIN_OLD_EMAIL
    )

    admin_hash = generate_password_hash(
        _get_admin_password()
    )

    if admin_id:

        cur.execute(
            """
            UPDATE users
            SET email = ?, password_hash = ?
            WHERE user_id = ?
            """,
            (
                ADMIN_EMAIL,
                admin_hash,
                admin_id
            )
        )

    else:

        cur.execute(
            """
            INSERT INTO users
            (name, email, password_hash, phone, role)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "Chief Sanitation Officer",
                ADMIN_EMAIL,
                admin_hash,
                "9876543210",
                "admin"
            )
        )

    # ---------------- STAFF ----------------

    staff_id = _find_account(
        cur,
        STAFF_EMAIL,
        STAFF_OLD_EMAIL
    )

    staff_hash = generate_password_hash(
        _get_staff_password()
    )

    if staff_id:

        cur.execute(
            """
            UPDATE users
            SET name = ?, email = ?, password_hash = ?
            WHERE user_id = ?
            """,
            (
                "Yogesh Kirar",
                STAFF_EMAIL,
                staff_hash,
                staff_id
            )
        )

    else:

        cur.execute(
            """
            INSERT INTO users
            (name, email, password_hash, phone, role)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "Yogesh Kirar",
                STAFF_EMAIL,
                staff_hash,
                "9123456780",
                "staff"
            )
        )

        staff_id = cur.lastrowid

    # Ensure staff profile exists.
    cur.execute(
        "SELECT user_id FROM staff WHERE user_id = ?",
        (staff_id,)
    )

    if not cur.fetchone():

        cur.execute(
            """
            INSERT INTO staff
            (
                user_id,
                department_id,
                employee_code,
                designation,
                assigned_zone,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                staff_id,
                1,
                "EMP-SWM-101",
                "Field Supervisor",
                "Central Ward",
                "available"
            )
        )


def init_sqlite_database():
    """Initialize SQLite schema and synchronize default accounts."""

    global _SQLITE_INITIALIZED

    os.makedirs(
        os.path.dirname(SQLITE_DB_PATH),
        exist_ok=True
    )

    conn = sqlite3.connect(
        SQLITE_DB_PATH,
        timeout=20.0
    )

    cur = conn.cursor()

    try:

        # Check whether users table exists.
        cur.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            AND name = 'users'
            """
        )

        if not cur.fetchone():

            schema_file = os.path.join(
                BASE_DIR,
                'database',
                'schema_sqlite.sql'
            )

            if not os.path.exists(schema_file):
                raise FileNotFoundError(
                    f"SQLite schema not found: {schema_file}"
                )

            with open(
                schema_file,
                'r',
                encoding='utf-8'
            ) as file:
                conn.executescript(file.read())

        # Synchronize credentials on every initialization.
        _seed_sqlite_accounts(cur)

        conn.commit()

        _SQLITE_INITIALIZED = True

        print("SQLite database initialized.")
        print(f"Admin account: {ADMIN_EMAIL}")
        print(f"Staff account: {STAFF_EMAIL}")
        print("Staff name: Yogesh Kirar")

    except Exception:
        conn.rollback()
        raise

    finally:
        cur.close()
        conn.close()


def get_sqlite_connection():
    """Return a wrapped SQLite connection."""

    global _SQLITE_INITIALIZED

    if (
        not _SQLITE_INITIALIZED
        or not os.path.exists(SQLITE_DB_PATH)
    ):
        init_sqlite_database()

    raw_conn = sqlite3.connect(
        SQLITE_DB_PATH,
        timeout=20.0
    )

    raw_conn.row_factory = dict_factory
    raw_conn.execute("PRAGMA foreign_keys = ON")

    return SQLiteConnectionWrapper(raw_conn)


def get_connection():
    """
    Return a connection according to DB_TYPE.

    sqlite: SQLite database.
    mysql: MySQL database only.
    auto: Try MySQL, then fall back to SQLite.
    """

    if DB_TYPE == "sqlite":
        return get_sqlite_connection()

    if mysql is None:

        if DB_TYPE == "mysql":
            raise RuntimeError(
                "DB_TYPE=mysql but mysql-connector-python "
                "is not installed. Install it using "
                "pip install mysql-connector-python"
            )

        return get_sqlite_connection()

    try:

        return mysql.connector.connect(**DB_CONFIG)

    except Exception:

        if DB_TYPE == "mysql":
            raise

        return get_sqlite_connection()


def get_db_cursor(dictionary=True):
    """Return both database connection and cursor."""

    conn = get_connection()

    cursor = conn.cursor(
        dictionary=dictionary
    )

    return conn, cursor
