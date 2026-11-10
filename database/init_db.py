
"""
Initialize the CleanCity database.

Usage:
    python database/init_db.py
    python database/init_db.py --demo
    python database/init_db.py --reset
"""

import os
import sys

# Allow importing db/config from the project root.
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..')
)

sys.path.insert(0, PROJECT_ROOT)

from config import DB_TYPE, SQLITE_DB_PATH
import db as dbmod


ADMIN_EMAIL = "admin@wmrs.gov"
STAFF_EMAIL = "staff@wmrs.gov"

ADMIN_OLD_EMAIL = "admin@wastemanage.gov"
STAFF_OLD_EMAIL = "staff@wastemanage.gov"


def _is_sqlite_conn(conn):
    return isinstance(
        conn,
        dbmod.SQLiteConnectionWrapper
    )


def initialize_mysql(conn):
    """Create MySQL tables from schema.sql."""

    cursor = conn.cursor()

    schema_file = os.path.join(
        os.path.dirname(__file__),
        'schema.sql'
    )

    with open(
        schema_file,
        'r',
        encoding='utf-8'
    ) as file:
        statements = [
            statement.strip()
            for statement in file.read().split(';')
            if statement.strip()
        ]

    print(
        f"Executing {len(statements)} MySQL schema statements..."
    )

    for statement in statements:

        try:
            cursor.execute(statement)

        except Exception as error:
            print(
                f"Notice on statement: "
                f"{statement[:40]}... -> {error}"
            )

    conn.commit()
    cursor.close()


def _find_account(cursor, new_email, old_email):
    """Find the new account or its previous email address."""

    cursor.execute(
        "SELECT user_id FROM users WHERE email = %s",
        (new_email,)
    )

    account = cursor.fetchone()

    if account:
        return account[0]

    cursor.execute(
        "SELECT user_id FROM users WHERE email = %s",
        (old_email,)
    )

    account = cursor.fetchone()

    return account[0] if account else None


def seed_default_accounts(conn):
    """
    Create or update default accounts.

    Passwords are read from environment variables.
    """

    from werkzeug.security import generate_password_hash

    admin_password = os.getenv(
        "INITIAL_ADMIN_PASSWORD",
        "Admin@123"
    )

    staff_password = os.getenv(
        "INITIAL_STAFF_PASSWORD",
        "Staff@123"
    )

    cursor = conn.cursor()

    try:

        # ---------------- ADMIN ACCOUNT ----------------

        admin_id = _find_account(
            cursor,
            ADMIN_EMAIL,
            ADMIN_OLD_EMAIL
        )

        admin_hash = generate_password_hash(
            admin_password
        )

        if admin_id:

            cursor.execute(
                """
                UPDATE users
                SET email = %s, password_hash = %s
                WHERE user_id = %s
                """,
                (
                    ADMIN_EMAIL,
                    admin_hash,
                    admin_id
                )
            )

            print(
                f"Admin account updated: {ADMIN_EMAIL}"
            )

        else:

            cursor.execute(
                """
                INSERT INTO users
                (name, email, password_hash, phone, role)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    "Chief Sanitation Officer",
                    ADMIN_EMAIL,
                    admin_hash,
                    "9876543210",
                    "admin"
                )
            )

            print(
                f"Admin account created: {ADMIN_EMAIL}"
            )

        # ---------------- STAFF ACCOUNT ----------------

        staff_id = _find_account(
            cursor,
            STAFF_EMAIL,
            STAFF_OLD_EMAIL
        )

        staff_hash = generate_password_hash(
            staff_password
        )

        if staff_id:

            cursor.execute(
                """
                UPDATE users
                SET name = %s,
                    email = %s,
                    password_hash = %s
                WHERE user_id = %s
                """,
                (
                    "Yogesh Kirar",
                    STAFF_EMAIL,
                    staff_hash,
                    staff_id
                )
            )

            print(
                f"Staff account updated: {STAFF_EMAIL}"
            )

        else:

            cursor.execute(
                """
                INSERT INTO users
                (name, email, password_hash, phone, role)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    "Yogesh Kirar",
                    STAFF_EMAIL,
                    staff_hash,
                    "9123456780",
                    "staff"
                )
            )

            staff_id = cursor.lastrowid

            print(
                f"Staff account created: {STAFF_EMAIL}"
            )

        # Ensure staff profile exists.
        cursor.execute(
            "SELECT user_id FROM staff WHERE user_id = %s",
            (staff_id,)
        )

        if not cursor.fetchone():

            cursor.execute(
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
                VALUES (%s, %s, %s, %s, %s, %s)
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

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()


def initialize_database(reset=False, demo=False):
    """Initialize the selected database."""

    if reset and DB_TYPE == "sqlite":

        if os.path.exists(SQLITE_DB_PATH):
            os.remove(SQLITE_DB_PATH)
            print("Existing SQLite database removed.")

        dbmod._SQLITE_INITIALIZED = False

    conn = dbmod.get_connection()

    try:

        if _is_sqlite_conn(conn):

            print(
                f"Using SQLite database: {SQLITE_DB_PATH}"
            )

            # get_connection() initializes the SQLite schema
            # and synchronizes default accounts through db.py.

        else:

            print("Connected to MySQL.")

            initialize_mysql(conn)
            seed_default_accounts(conn)

    finally:
        conn.close()

    if demo:

        from seed_demo import seed_demo_data

        seed_demo_data()

    print("\nDatabase initialization complete!")


if __name__ == '__main__':

    initialize_database(
        reset='--reset' in sys.argv,
        demo='--demo' in sys.argv
    )
