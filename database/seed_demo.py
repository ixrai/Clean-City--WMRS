"""
Load realistic demo data (citizens, extra staff, complaints in every status, feedback,
status history, notifications and sample photos) so every screen has something to show.

Usage:  python database/init_db.py --demo        (or run this file directly)
Safe to run twice: it does nothing if complaints already exist.
Demo password for the sample citizens: Citizen@123
"""
import os
import sys
import struct
import zlib
from datetime import datetime, timedelta

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from werkzeug.security import generate_password_hash
from config import UPLOAD_FOLDER
from db import get_db_cursor

DEMO_CITIZEN_PASSWORD = "Citizen@123"


# --------------------------------------------------------------------------- images
def _solid_png(path, rgb, size=(640, 400)):
    """Dependency-free PNG writer (used when Pillow is not installed)."""
    w, h = size
    row = b'\x00' + bytes(rgb) * w
    raw = row * h

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)

    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) \
        + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')
    with open(path, 'wb') as f:
        f.write(png)


def make_sample_image(filename, label, rgb):
    """Create a clearly-labelled sample photo so the before/after view has real images."""
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    path = os.path.join(UPLOAD_FOLDER, filename)
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (640, 400), rgb)
        d = ImageDraw.Draw(img)
        d.rectangle([0, 150, 640, 250], fill=(255, 255, 255))
        d.text((24, 188), label, fill=(30, 30, 30))
        img.save(path, "PNG")
    except Exception:
        _solid_png(path, rgb)
    return filename


# --------------------------------------------------------------------------- data
STAFF = [
    ("Sunita Devi", "sunita.devi@wastemanage.gov", "9123456781", "EMP-SWM-102", 2, "North Ward", "Sanitation Officer"),
    ("Mohit Yadav", "mohit.yadav@wastemanage.gov", "9123456782", "EMP-SWM-103", 4, "East Ward", "Drainage Supervisor"),
]
CITIZENS = [
    ("Soman Kumar", "soman@gmail.com", "9810000001"),
    ("Priya Sharma", "priya.sharma@example.com", "9810000002"),
    ("Amit Verma", "amit.verma@example.com", "9810000003"),
]
# (title, description, address, lat, lng, category_id, citizen_idx, status, staff_idx, days_ago, rating)
COMPLAINTS = [
    ("Dustbin overflowing near metro station", "Public dustbin has been overflowing for three days and garbage is spilling onto the footpath.", "Gate No. 2, Metro Station, Connaught Place", 28.6315, 77.2167, 1, 0, "Pending", None, 1, None),
    ("Illegal dumping on vacant plot", "Mixed household and shop waste is being dumped on the empty plot behind the market every night.", "Behind Karol Bagh Market, Ajmal Khan Road", 28.6519, 77.1909, 2, 1, "Pending", None, 2, None),
    ("Stray animal carcass on road", "A dead dog is lying near the footpath and has started to smell badly. Urgent removal needed.", "Lajpat Nagar Central Market, Main Road", 28.5677, 77.2433, 3, 2, "Pending", None, 0, None),
    ("Old batteries and electronics dumped", "A pile of discarded batteries, monitors and wires has been left next to the park gate.", "Sector 7 Park Gate, Rohini", 28.7160, 77.1130, 4, 0, "Assigned", 0, 4, None),
    ("Construction debris blocking lane", "Bricks and cement rubble from a renovation have blocked half the lane for over a week.", "C-Block Lane 4, Janakpuri", 28.6219, 77.0878, 5, 1, "Assigned", 1, 5, None),
    ("Clogged drain full of plastic", "The roadside drain is choked with plastic bags and overflows onto the road when it rains.", "Mayur Vihar Phase 1, Pocket B", 28.6078, 77.2958, 6, 2, "Assigned", 1, 3, None),
    ("Overflowing bin outside hospital", "Bins near the hospital entrance are overflowing and attracting stray animals.", "District Centre, Saket", 28.5245, 77.2066, 1, 0, "In Progress", 0, 6, None),
    ("Garbage heap at market corner", "A large garbage heap has built up at the corner of the vegetable market.", "Chandni Chowk, Fountain Side", 28.6506, 77.2303, 2, 1, "In Progress", 0, 7, None),
    ("Dustbin cleared - Nehru Place", "Dustbin outside the office complex was overflowing and has now been cleared.", "Nehru Place Commercial Complex", 28.5491, 77.2530, 1, 2, "Resolved", 0, 12, 5),
    ("Roadside waste dump cleared", "Waste dumped along the service road was removed and the area swept clean.", "Sector 10 Service Road, Dwarka", 28.5823, 77.0500, 2, 0, "Resolved", 1, 14, 4),
    ("Drain cleaned after blockage", "The blocked storm drain was cleared of garbage and water now flows freely.", "TV Tower Road, Pitampura", 28.6980, 77.1320, 6, 1, "Resolved", 1, 10, None),
    ("Duplicate report - not a municipal issue", "Waste inside a private society compound, which is managed by the society itself.", "Vasant Kunj, Sector B (private society)", 28.5200, 77.1590, 1, 2, "Rejected", None, 9, None),
]

COLORS = {"before": (150, 120, 90), "after": (110, 170, 120)}


def seed_demo_data(force=False):
    conn, cur = get_db_cursor()
    try:
        cur.execute("SELECT COUNT(*) AS n FROM complaints")
        if cur.fetchone()['n'] and not force:
            print("Demo data skipped: complaints already exist.")
            return

        # --- users -----------------------------------------------------------
        pw = generate_password_hash(DEMO_CITIZEN_PASSWORD)
        citizen_ids = []
        for name, email, phone in CITIZENS:
            cur.execute("SELECT user_id FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
            if row:
                citizen_ids.append(row['user_id'])
            else:
                cur.execute("INSERT INTO users (name, email, phone, password_hash, role) VALUES (%s,%s,%s,%s,'citizen')",
                            (name, email, phone, pw))
                citizen_ids.append(cur.lastrowid)

        cur.execute("SELECT staff_id, user_id FROM staff WHERE employee_code = %s", ('EMP-SWM-101',))
        base = cur.fetchone()
        staff_ids = [(base['staff_id'], base['user_id'])]
        staff_pw = generate_password_hash("Staff@123")
        for name, email, phone, code, dept, zone, desig in STAFF:
            cur.execute("SELECT user_id FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
            if row:
                uid = row['user_id']
            else:
                cur.execute("INSERT INTO users (name, email, phone, password_hash, role) VALUES (%s,%s,%s,%s,'staff')",
                            (name, email, phone, staff_pw))
                uid = cur.lastrowid
            cur.execute("SELECT staff_id FROM staff WHERE user_id = %s", (uid,))
            srow = cur.fetchone()
            if not srow:
                cur.execute("INSERT INTO staff (user_id, department_id, employee_code, designation, assigned_zone, status) "
                            "VALUES (%s,%s,%s,%s,%s,'available')", (uid, dept, code, desig, zone))
                sid = cur.lastrowid
            else:
                sid = srow['staff_id']
            staff_ids.append((sid, uid))

        cur.execute("SELECT user_id FROM users WHERE role = 'admin' ORDER BY user_id LIMIT 1")
        admin_id = cur.fetchone()['user_id']
        conn.commit()

        # --- complaints ------------------------------------------------------
        fmt = "%Y-%m-%d %H:%M:%S"
        now = datetime.now()
        for n, (title, desc, addr, lat, lng, cat, cidx, status, sidx, days, rating) in enumerate(COMPLAINTS, start=1):
            created = now - timedelta(days=days, hours=2)
            citizen = citizen_ids[cidx]
            staff_id, staff_user = staff_ids[sidx] if sidx is not None else (None, None)
            before = make_sample_image(f"before_demo_{n:02d}.png", f"SAMPLE BEFORE PHOTO #{n}", COLORS["before"])
            after = None
            resolved_at = None
            if status == "Resolved":
                after = make_sample_image(f"after_demo_{n:02d}.png", f"SAMPLE AFTER PHOTO #{n}", COLORS["after"])
                resolved_at = (created + timedelta(days=1, hours=3)).strftime(fmt)
            cur.execute("SELECT default_priority FROM categories WHERE category_id = %s", (cat,))
            priority = cur.fetchone()['default_priority']

            cur.execute("""
                INSERT INTO complaints (complaint_code, citizen_id, category_id, assigned_staff_id, title,
                    description, address, latitude, longitude, before_image, after_image, status, priority,
                    rejection_reason, created_at, updated_at, resolved_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (f"TMP-{n}", citizen, cat, staff_id, title, desc, addr, lat, lng, before, after, status, priority,
                  "Not a municipal responsibility (private property)." if status == "Rejected" else None,
                  created.strftime(fmt), created.strftime(fmt), resolved_at))
            cid = cur.lastrowid
            code = f"WMR-{created.strftime('%Y%m')}-{cid:04d}"
            cur.execute("UPDATE complaints SET complaint_code = %s WHERE complaint_id = %s", (code, cid))

            def hist(old, new, who, remark, when):
                cur.execute("INSERT INTO status_history (complaint_id, changed_by_user_id, old_status, new_status, remarks, changed_at) "
                            "VALUES (%s,%s,%s,%s,%s,%s)", (cid, who, old, new, remark, when.strftime(fmt)))

            hist(None, "Pending", citizen, "Complaint filed by citizen with photographic and GPS evidence.", created)
            if status in ("Assigned", "In Progress", "Resolved"):
                hist("Pending", "Assigned", admin_id, "Assigned to field staff for cleanup.", created + timedelta(hours=5))
            if status in ("In Progress", "Resolved"):
                hist("Assigned", "In Progress", staff_user, "Sanitation staff initiated ground cleaning.", created + timedelta(hours=20))
            if status == "Resolved":
                hist("In Progress", "Resolved", staff_user, "Ground cleaning completed with photo proof.", created + timedelta(days=1, hours=3))
            if status == "Rejected":
                hist("Pending", "Rejected", admin_id, "Not a municipal responsibility (private property).", created + timedelta(hours=8))

            cur.execute("INSERT INTO notifications (user_id, complaint_id, title, message, is_read, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
                        (citizen, cid, f"Ticket Status: {status}",
                         f"Your complaint {code} is currently '{status}'.", 1 if status != "Pending" else 0, created.strftime(fmt)))
            if staff_user and status in ("Assigned", "In Progress"):
                cur.execute("INSERT INTO notifications (user_id, complaint_id, title, message, created_at) VALUES (%s,%s,%s,%s,%s)",
                            (staff_user, cid, "New Task Assigned", f"You have been assigned complaint {code} at {addr}.", created.strftime(fmt)))
            if rating:
                cur.execute("INSERT INTO feedback (complaint_id, citizen_id, rating, comments) VALUES (%s,%s,%s,%s)",
                            (cid, citizen, rating, "Quick response and the area is clean now. Thank you!" if rating == 5 else "Good work, took a little long."))

        conn.commit()
        print(f"Demo data loaded: {len(COMPLAINTS)} complaints, {len(CITIZENS)} citizens, {len(STAFF)} extra staff.")
        print(f"Demo citizen password: {DEMO_CITIZEN_PASSWORD}")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    seed_demo_data(force='--force' in sys.argv)
