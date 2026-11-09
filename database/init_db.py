"""
Initialise the CleanCity database (SQLite by default, MySQL if DB_TYPE=mysql).

Usage:
    python database/init_db.py            # create tables + default admin/staff accounts
    python database/init_db.py --demo     # ...and load realistic demo complaints
    python database/init_db.py --reset    # SQLite only: delete the DB file first
"""
import os
import sys

# Allow importing db/config from the project root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from werkzeug.security import generate_password_hash
from config import DB_TYPE, SQLITE_DB_PATH
import db as dbmod


def _is_sqlite_conn(conn):
    return isinstance(conn, dbmod.SQLiteConnectionWrapper)


def initialize_mysql(conn):
    cursor = conn.cursor()
    schema_file = os.path.join(os.path.dirname(__file__), 'schema.sql')
    with open(schema_file, 'r', encoding='utf-8') as f:
        statements = [s.strip() for s in f.read().split(';') if s.strip()]
    print(f"Executing {len(statements)} MySQL schema statements...")
    for stmt in statements:
        try:
            cursor.execute(stmt)
        except Exception as e:
            print(f"Notice on statement: {stmt[:40]}... -> {e}")
    conn.commit()
    cursor.close()


def seed_default_accounts(conn):
    """Create the default admin + staff accounts if missing (works on both DBs)."""
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE email = %s", ('admin@wastemanage.gov',))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (name, email, password_hash, phone, role) VALUES (%s, %s, %s, %s, %s)",
            ('Chief Sanitation Officer', 'admin@wastemanage.gov',
             generate_password_hash("Admin@123"), '9876543210', 'admin'))
        print("Default Admin created: admin@wastemanage.gov / Admin@123")

    cur.execute("SELECT user_id FROM users WHERE email = %s", ('staff@wastemanage.gov',))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users (name, email, password_hash, phone, role) VALUES (%s, %s, %s, %s, %s)",
            ('Ramesh Kumar (Staff)', 'staff@wastemanage.gov',
             generate_password_hash("Staff@123"), '9123456780', 'staff'))
        staff_user_id = cur.lastrowid
        cur.execute(
            "INSERT INTO staff (user_id, department_id, employee_code, designation, assigned_zone, status) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (staff_user_id, 1, 'EMP-SWM-101', 'Field Supervisor', 'Central Ward', 'available'))
        print("Default Staff created: staff@wastemanage.gov / Staff@123 (EMP-SWM-101)")
    conn.commit()
    cur.close()


def initialize_database(reset=False, demo=False):
    if reset and DB_TYPE == "sqlite" and os.path.exists(SQLITE_DB_PATH):
        os.remove(SQLITE_DB_PATH)
        dbmod._SQLITE_INITIALIZED = False
        print("Existing SQLite database removed.")

    conn = dbmod.get_connection()
    if _is_sqlite_conn(conn):
        print(f"Using SQLite database: {SQLITE_DB_PATH}")
    else:
        print("Connected to MySQL.")
        initialize_mysql(conn)

    seed_default_accounts(conn)
    conn.close()

    if demo:
        from seed_demo import seed_demo_data
        seed_demo_data()

    print("\nDatabase initialization complete!")


if __name__ == '__main__':
    initialize_database(reset='--reset' in sys.argv, demo='--demo' in sys.argv)
