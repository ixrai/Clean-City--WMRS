"""
CleanCity automated test-suite.

Run from the project root:
    python -m unittest discover -s tests -v

The tests use a throw-away SQLite database and upload folder (your real data is never
touched) and a fake Firebase service (no internet needed, no real accounts created).
"""
import io
import os
import re
import sys
import shutil
import tempfile
import unittest

# ---- isolate the test run BEFORE the app is imported ------------------------
_TMP = tempfile.mkdtemp(prefix="cleancity_test_")
os.environ["SQLITE_DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(_TMP, "uploads")
os.environ["DB_TYPE"] = "sqlite"
os.environ["SECRET_KEY"] = "unit-test-secret"
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app as app_module                    # noqa: E402
import firebase_service                     # noqa: E402
from app import app                         # noqa: E402
from db import get_db_cursor                # noqa: E402

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000001e221bc3300000000"
    "49454e44ae426082"
)
ADMIN = ("admin@wastemanage.gov", "Admin@123")
STAFF = ("staff@wastemanage.gov", "Staff@123")

# ---- fake Firebase (controllable) -------------------------------------------
FAKE_FIREBASE = {"accounts": {}, "tokens": {}}   # email->password, token->email


def _fake_firebase_request(endpoint, payload):
    if endpoint == "signInWithPassword":
        email = payload["email"]
        if FAKE_FIREBASE["accounts"].get(email) == payload["password"]:
            return {"success": True, "email": email, "localId": "uid-" + email}
        return {"success": False, "error": "Incorrect password.", "code": "INVALID_PASSWORD"}
    if endpoint == "signUp":
        FAKE_FIREBASE["accounts"][payload["email"]] = payload["password"]
        return {"success": True, "email": payload["email"], "localId": "uid-" + payload["email"]}
    if endpoint == "lookup":
        email = FAKE_FIREBASE["tokens"].get(payload["idToken"])
        if email:
            return {"success": True, "users": [{"localId": "uid-" + email, "email": email}]}
        return {"success": False, "error": "invalid token", "code": "INVALID_ID_TOKEN"}
    if endpoint == "sendOobCode":
        return {"success": True}
    return {"success": False, "error": "unsupported"}


def setUpModule():
    firebase_service._make_firebase_request = _fake_firebase_request


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


# ---- helpers -----------------------------------------------------------------
def db_one(sql, params=()):
    conn, cur = get_db_cursor()
    try:
        cur.execute(sql, params)
        return cur.fetchone()
    finally:
        cur.close()
        conn.close()


def db_exec(sql, params=()):
    conn, cur = get_db_cursor()
    try:
        cur.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        cur.close()
        conn.close()


class Base(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.config["CSRF_ENABLED"] = False
        app_module._FAILED_LOGINS.clear()
        FAKE_FIREBASE["accounts"].clear()
        FAKE_FIREBASE["tokens"].clear()
        self.client = app.test_client()

    def login(self, client, creds):
        return client.post("/login", data={"email": creds[0], "password": creds[1]}, follow_redirects=True)

    def new_client(self, creds):
        c = app.test_client()
        res = self.login(c, creds)
        self.assertEqual(res.status_code, 200)
        return c

    def register_citizen(self, email, name="Test Citizen", password="Password@123"):
        c = app.test_client()
        c.post("/register", data={"name": name, "email": email, "phone": "9876543210",
                                  "password": password, "confirm_password": password})
        res = self.login(c, (email, password))
        self.assertIn(b"Welcome back", res.data)
        return c

    def file_complaint(self, client, **over):
        data = {"category_id": "1", "title": "Overflowing bin", "description": "Bin is overflowing.",
                "address": "Gate 2, Metro Station", "latitude": "28.6315", "longitude": "77.2167",
                "before_image": (io.BytesIO(PNG), "photo.png")}
        data.update(over)
        res = client.post("/report", data=data, content_type="multipart/form-data", follow_redirects=True)
        code = re.search(rb"WMR-\d{6}-\d{4}", res.data)
        return res, (code.group(0).decode() if code else None)


# =============================================================================
class TestPublicAndAuth(Base):
    def test_public_pages(self):
        for path, text in (("/", b"CleanCity"), ("/awareness", b"Waste Segregation"),
                           ("/login", b"Sign In"), ("/register", b"Register")):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200, path)
            self.assertIn(text, res.data, path)

    def test_404_page_and_unknown_tracking_code(self):
        self.assertEqual(self.client.get("/does-not-exist").status_code, 404)
        res = self.client.get("/track?complaint_code=WMR-000000-9999", follow_redirects=True)
        self.assertIn(b"No complaint found", res.data)

    def test_register_validation(self):
        r = self.client.post("/register", data={"name": "X", "email": "not-an-email", "password": "Password@1",
                                                "confirm_password": "Password@1"})
        self.assertIn(b"valid name, email", r.data)
        r = self.client.post("/register", data={"name": "X", "email": "ok1@example.com", "password": "abc",
                                                "confirm_password": "abc"})
        self.assertIn(b"at least 6", r.data)
        r = self.client.post("/register", data={"name": "X", "email": "ok2@example.com", "password": "Password@1",
                                                "confirm_password": "Different@1"})
        self.assertIn(b"do not match", r.data)

    def test_register_login_and_duplicate(self):
        self.register_citizen("citizen.one@example.com")
        res = self.client.post("/register", data={"name": "Again", "email": "citizen.one@example.com",
                                                   "password": "Password@123", "confirm_password": "Password@123"},
                               follow_redirects=True)
        self.assertIn(b"already exists", res.data)

    def test_wrong_password_rejected(self):
        res = self.login(self.client, (ADMIN[0], "wrong-password"))
        self.assertIn(b"Invalid email or password", res.data)
        self.assertEqual(self.client.get("/admin/dashboard").status_code, 302)

    def test_login_rate_limit(self):
        for _ in range(5):
            self.login(self.client, (ADMIN[0], "bad"))
        res = self.login(self.client, ADMIN)           # correct password, but locked out
        self.assertEqual(res.status_code, 429)
        self.assertEqual(self.client.get("/admin/dashboard").status_code, 302)

    def test_logout(self):
        c = self.new_client(ADMIN)
        c.get("/logout")
        self.assertEqual(c.get("/admin/dashboard").status_code, 302)


class TestSecurityRegressions(Base):
    """One test per vulnerability found in the original submission."""

    def test_firebase_login_without_proof_is_rejected(self):
        # Original bug: {"email": "admin@..."} alone produced a full admin session.
        r = self.client.post("/api/auth/firebase-login", json={"email": ADMIN[0]})
        self.assertIn(r.status_code, (400, 401))
        self.assertFalse(r.get_json()["success"])
        self.assertEqual(self.client.get("/admin/dashboard").status_code, 302)

    def test_firebase_login_with_bad_token_is_rejected(self):
        r = self.client.post("/api/auth/firebase-login", json={"idToken": "forged", "email": ADMIN[0]})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.client.get("/admin/dashboard").status_code, 302)

    def test_firebase_signup_cannot_take_over_admin(self):
        # Anyone can create a Firebase account for ANY e-mail; it must not unlock local admin.
        FAKE_FIREBASE["accounts"][ADMIN[0]] = "attacker-password"
        res = self.login(self.client, (ADMIN[0], "attacker-password"))
        self.assertIn(b"Invalid email or password", res.data)
        r = self.client.post("/api/auth/firebase-login", json={"email": ADMIN[0], "password": "attacker-password"})
        self.assertEqual(r.status_code, 401)
        FAKE_FIREBASE["tokens"]["tok-admin"] = ADMIN[0]
        r = self.client.post("/api/auth/firebase-login", json={"idToken": "tok-admin"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get("/admin/dashboard").status_code, 302)

    def test_firebase_token_login_works_for_citizens(self):
        FAKE_FIREBASE["tokens"]["tok-1"] = "fb.citizen@example.com"
        r = self.client.post("/api/auth/firebase-login", json={"idToken": "tok-1", "name": "FB Citizen"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["role"], "citizen")
        self.assertEqual(self.client.get("/citizen/dashboard").status_code, 200)

    def test_firebase_password_login_for_citizen_and_local_admin(self):
        self.register_citizen("pw.citizen@example.com")
        r = app.test_client().post("/api/auth/firebase-login", json={"email": "pw.citizen@example.com",
                                                                     "password": "Password@123"})
        self.assertTrue(r.get_json()["success"])
        r = app.test_client().post("/api/auth/firebase-login", json={"email": ADMIN[0], "password": ADMIN[1]})
        self.assertEqual(r.get_json()["role"], "admin")

    def test_firebase_register_endpoint(self):
        r = self.client.post("/api/auth/firebase-register",
                             json={"name": "API User", "email": "api.user@example.com",
                                   "phone": "9812345678", "password": "FirebasePass@123"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["success"])
        # password too short / invalid e-mail
        r = self.client.post("/api/auth/firebase-register",
                             json={"name": "A", "email": "short@example.com", "password": "123"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/auth/firebase-register",
                             json={"name": "A", "email": "bad-email", "password": "Password@123"})
        self.assertEqual(r.status_code, 400)
        # forged token cannot register
        r = self.client.post("/api/auth/firebase-register",
                             json={"name": "A", "email": "forged@example.com", "idToken": "nope"})
        self.assertEqual(r.status_code, 401)

    def test_forgot_password_does_not_leak_accounts(self):
        r = self.client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
        self.assertTrue(r.get_json()["success"])

    def test_open_redirect_blocked(self):
        for bad in ("//evil.com", "/\\evil.com", "https://evil.com", "javascript:alert(1)"):
            c = app.test_client()
            res = c.post("/login", query_string={"next": bad}, data={"email": ADMIN[0], "password": ADMIN[1]})
            self.assertEqual(res.status_code, 302)
            self.assertNotIn("evil", res.headers["Location"], bad)
            self.assertTrue(res.headers["Location"].endswith("/admin/dashboard"), bad)
        res = app.test_client().post("/login", query_string={"next": "/notifications"},
                                     data={"email": ADMIN[0], "password": ADMIN[1]})
        self.assertTrue(res.headers["Location"].endswith("/notifications"))

    def test_db_test_endpoint_is_admin_only(self):
        self.assertEqual(self.client.get("/db-test").status_code, 302)
        citizen = self.register_citizen("dbtest.citizen@example.com")
        self.assertEqual(citizen.get("/db-test").status_code, 302)
        admin = self.new_client(ADMIN)
        res = admin.get("/db-test")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["status"], "success")

    def test_map_api_requires_admin(self):
        self.assertEqual(self.client.get("/api/complaints-map").status_code, 401)
        citizen = self.register_citizen("map.citizen@example.com")
        self.assertEqual(citizen.get("/api/complaints-map").status_code, 403)
        self.assertEqual(self.new_client(ADMIN).get("/api/complaints-map").status_code, 200)

    def test_secret_key_and_debug_not_hardcoded(self):
        import config
        self.assertNotEqual(config.SECRET_KEY, "waste_mgmt_secure_app_key_2026_xyz")
        self.assertFalse(config.DEBUG)

    def test_csrf_protection(self):
        app.config["CSRF_ENABLED"] = True
        c = app.test_client()
        c.get("/login")                                  # session + token created
        res = c.post("/login", data={"email": ADMIN[0], "password": ADMIN[1]}, follow_redirects=True)
        self.assertIn(b"form was invalid", res.data)     # no token -> blocked
        self.assertEqual(c.get("/admin/dashboard").status_code, 302)
        html = c.get("/login").data.decode()
        token = re.search(r'name="csrf_token" value="([0-9a-f]+)"', html).group(1)
        res = c.post("/login", data={"email": ADMIN[0], "password": ADMIN[1], "csrf_token": token})
        self.assertEqual(res.status_code, 302)           # valid token -> allowed
        # JSON API must send the token in a header
        c2 = app.test_client()
        c2.get("/login")
        self.assertEqual(c2.post("/api/auth/forgot-password", json={"email": "a@b.co"}).status_code, 400)


# =============================================================================
class TestComplaintLifecycle(Base):
    def test_full_lifecycle_report_assign_progress_resolve_feedback(self):
        citizen = self.register_citizen("life.citizen@example.com")
        res, code = self.file_complaint(citizen)
        self.assertIsNotNone(code, "tracking code should be shown")
        row = db_one("SELECT * FROM complaints WHERE complaint_code = %s", (code,))
        cid = row["complaint_id"]
        self.assertEqual(row["status"], "Pending")
        self.assertTrue(code.endswith(f"{cid:04d}"))

        admin = self.new_client(ADMIN)
        staff_row = db_one("SELECT staff_id FROM staff WHERE employee_code = 'EMP-SWM-101'")
        res = admin.post(f"/admin/update-complaint/{cid}", data={
            "assigned_staff_id": staff_row["staff_id"], "priority": "High", "status": "Pending",
            "remarks": "Please clear today"}, follow_redirects=True)
        self.assertIn(b"updated successfully", res.data)
        self.assertEqual(db_one("SELECT status FROM complaints WHERE complaint_id = %s", (cid,))["status"], "Assigned")

        staff = self.new_client(STAFF)
        res = staff.post(f"/staff/update-status/{cid}", data={"status": "In Progress"}, follow_redirects=True)
        self.assertIn(b"In Progress", res.data)

        # completing WITHOUT a photo must fail
        res = staff.post(f"/staff/complete/{cid}", data={"remarks": "done"}, follow_redirects=True)
        self.assertIn(b"photographic proof", res.data)
        self.assertEqual(db_one("SELECT status FROM complaints WHERE complaint_id = %s", (cid,))["status"], "In Progress")

        # Original bug: NOW() crashed on SQLite so no complaint could ever be resolved.
        res = staff.post(f"/staff/complete/{cid}", data={"remarks": "cleaned",
                                                         "after_image": (io.BytesIO(PNG), "after.png")},
                         content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(b"marked as Resolved", res.data)
        row = db_one("SELECT * FROM complaints WHERE complaint_id = %s", (cid,))
        self.assertEqual(row["status"], "Resolved")
        self.assertIsNotNone(row["resolved_at"])
        self.assertTrue(row["after_image"].startswith("after_"))

        res = citizen.post(f"/feedback/{cid}", data={"rating": "5", "comments": "Great"}, follow_redirects=True)
        self.assertIn(b"Thank you for your feedback", res.data)
        # re-submitting updates (ON DUPLICATE KEY translation)
        citizen.post(f"/feedback/{cid}", data={"rating": "4", "comments": "Good"}, follow_redirects=True)
        fb = db_one("SELECT COUNT(*) AS n, MAX(rating) AS r FROM feedback WHERE complaint_id = %s", (cid,))
        self.assertEqual((fb["n"], fb["r"]), (1, 4))

        hist = db_one("SELECT COUNT(*) AS n FROM status_history WHERE complaint_id = %s", (cid,))["n"]
        self.assertEqual(hist, 4)   # Pending, Assigned, In Progress, Resolved
        self.assertIn(b"Resolved", citizen.get(f"/track?complaint_code={code}").data)
        notes = citizen.get("/notifications")
        self.assertEqual(notes.status_code, 200)

    def test_admin_can_resolve_and_reject(self):
        citizen = self.register_citizen("admin.flow@example.com")
        _, code = self.file_complaint(citizen)
        cid = db_one("SELECT complaint_id FROM complaints WHERE complaint_code = %s", (code,))["complaint_id"]
        admin = self.new_client(ADMIN)
        staff_id = db_one("SELECT staff_id FROM staff LIMIT 1")["staff_id"]
        res = admin.post(f"/admin/update-complaint/{cid}", data={
            "assigned_staff_id": staff_id, "priority": "Low", "status": "Resolved", "remarks": "ok"}, follow_redirects=True)
        self.assertIn(b"updated successfully", res.data)
        self.assertIsNotNone(db_one("SELECT resolved_at FROM complaints WHERE complaint_id = %s", (cid,))["resolved_at"])
        admin.post(f"/admin/update-complaint/{cid}", data={
            "priority": "Low", "status": "Rejected", "remarks": "Not municipal"}, follow_redirects=True)
        row = db_one("SELECT status, resolved_at, rejection_reason FROM complaints WHERE complaint_id = %s", (cid,))
        self.assertEqual(row["status"], "Rejected")
        self.assertIsNone(row["resolved_at"])
        self.assertEqual(row["rejection_reason"], "Not municipal")

    def test_tracking_codes_unique_after_deletion(self):
        # Original bug: code = COUNT(*)+1 produced duplicates after a delete.
        citizen = self.register_citizen("codes@example.com")
        _, c1 = self.file_complaint(citizen)
        _, c2 = self.file_complaint(citizen)
        db_exec("DELETE FROM complaints WHERE complaint_code = %s", (c1,))
        _, c3 = self.file_complaint(citizen)
        self.assertEqual(len({c1, c2, c3}), 3)

    def test_report_validation(self):
        citizen = self.register_citizen("validate@example.com")
        before = db_one("SELECT COUNT(*) AS n FROM complaints")["n"]
        upload_dir = os.environ["UPLOAD_FOLDER"]
        files_before = sorted(os.listdir(upload_dir)) if os.path.isdir(upload_dir) else []
        cases = [
            ({"latitude": "abc"}, b"not valid"),
            ({"latitude": "95"}, b"out of range"),
            ({"longitude": "400"}, b"out of range"),
            ({"category_id": "9999"}, b"valid waste category"),
            ({"title": "x" * 300}, b"too long"),
            ({"before_image": (io.BytesIO(b"<?php evil ?>"), "shell.jpg")}, b"not a valid image"),
            ({"before_image": (io.BytesIO(PNG), "doc.pdf")}, b"Invalid file format"),
            ({"before_image": (io.BytesIO(b""), "")}, b"attach a photograph"),
        ]
        for over, expected in cases:
            res, code = self.file_complaint(citizen, **over)
            self.assertIn(expected, res.data, over.keys())
            self.assertIsNone(code)
        self.assertEqual(db_one("SELECT COUNT(*) AS n FROM complaints")["n"], before)
        # nothing was written to the upload folder by the rejected requests
        files_after = sorted(os.listdir(upload_dir)) if os.path.isdir(upload_dir) else []
        self.assertEqual(files_before, files_after)

    def test_report_requires_login(self):
        self.assertEqual(self.client.get("/report").status_code, 302)
        self.assertEqual(self.client.post("/report", data={}).status_code, 302)


class TestAuthorization(Base):
    def _make_assigned(self, email):
        citizen = self.register_citizen(email)
        _, code = self.file_complaint(citizen)
        cid = db_one("SELECT complaint_id FROM complaints WHERE complaint_code = %s", (code,))["complaint_id"]
        return citizen, code, cid

    def test_role_walls(self):
        citizen = self.register_citizen("walls@example.com")
        for path in ("/admin/dashboard", "/admin/complaints", "/admin/map", "/admin/staff",
                     "/admin/export-report-pdf", "/staff/dashboard"):
            self.assertEqual(citizen.get(path).status_code, 302, path)
        staff = self.new_client(STAFF)
        for path in ("/admin/dashboard", "/admin/staff", "/citizen/dashboard"):
            self.assertEqual(staff.get(path).status_code, 302, path)
        anon = app.test_client()
        for path in ("/citizen/dashboard", "/notifications", "/staff/dashboard", "/admin/dashboard"):
            self.assertEqual(anon.get(path).status_code, 302, path)

    def test_staff_cannot_touch_unassigned_complaint(self):
        _, code, cid = self._make_assigned("unassigned@example.com")
        staff = self.new_client(STAFF)
        res = staff.post(f"/staff/update-status/{cid}", data={"status": "Resolved"}, follow_redirects=True)
        self.assertIn(b"not assigned to you", res.data)
        res = staff.post(f"/staff/complete/{cid}", data={"after_image": (io.BytesIO(PNG), "a.png")},
                         content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(b"not assigned to you", res.data)
        self.assertEqual(db_one("SELECT status FROM complaints WHERE complaint_id = %s", (cid,))["status"], "Pending")

    def test_staff_cannot_set_arbitrary_status(self):
        _, code, cid = self._make_assigned("arbitrary@example.com")
        staff_id = db_one("SELECT staff_id FROM staff WHERE employee_code = 'EMP-SWM-101'")["staff_id"]
        db_exec("UPDATE complaints SET assigned_staff_id = %s, status = 'Assigned' WHERE complaint_id = %s", (staff_id, cid))
        staff = self.new_client(STAFF)
        staff.post(f"/staff/update-status/{cid}", data={"status": "Resolved"}, follow_redirects=True)
        self.assertEqual(db_one("SELECT status FROM complaints WHERE complaint_id = %s", (cid,))["status"], "In Progress")

    def test_admin_input_validation(self):
        _, code, cid = self._make_assigned("adminval@example.com")
        admin = self.new_client(ADMIN)
        res = admin.post(f"/admin/update-complaint/{cid}", data={"status": "Banana", "priority": "High"}, follow_redirects=True)
        self.assertIn(b"Invalid status", res.data)
        res = admin.post(f"/admin/update-complaint/{cid}", data={"status": "Assigned", "priority": "High"}, follow_redirects=True)
        self.assertIn(b"select a staff member", res.data)
        res = admin.post(f"/admin/update-complaint/{cid}", data={"status": "Pending", "priority": "High",
                                                                 "assigned_staff_id": "99999"}, follow_redirects=True)
        self.assertIn(b"does not exist", res.data)
        self.assertEqual(db_one("SELECT status FROM complaints WHERE complaint_id = %s", (cid,))["status"], "Pending")

    def test_feedback_rules(self):
        owner, code, cid = self._make_assigned("fb.owner@example.com")
        other = self.register_citizen("fb.other@example.com")
        res = owner.post(f"/feedback/{cid}", data={"rating": "5"}, follow_redirects=True)
        self.assertIn(b"after the complaint has been resolved", res.data)         # not resolved yet
        db_exec("UPDATE complaints SET status = 'Resolved' WHERE complaint_id = %s", (cid,))
        res = other.post(f"/feedback/{cid}", data={"rating": "5"}, follow_redirects=True)
        self.assertIn(b"only submit feedback for complaints you submitted", res.data)
        res = owner.post(f"/feedback/{cid}", data={"rating": "9"}, follow_redirects=True)
        self.assertIn(b"between 1 and 5", res.data)
        self.assertEqual(db_one("SELECT COUNT(*) AS n FROM feedback WHERE complaint_id = %s", (cid,))["n"], 0)


class TestAdminTools(Base):
    def test_staff_registration_and_login(self):
        admin = self.new_client(ADMIN)
        base = {"name": "New Staff", "email": "new.staff@wastemanage.gov", "phone": "9000000000",
                "employee_code": "emp-swm-900", "assigned_zone": "West", "department_id": "1",
                "designation": "Officer", "password": "StrongPass@1"}
        res = admin.post("/admin/staff", data={**base, "password": "short"}, follow_redirects=True)
        self.assertIn(b"at least 8 characters", res.data)
        res = admin.post("/admin/staff", data={**base, "department_id": "999"}, follow_redirects=True)
        self.assertIn(b"department does not exist", res.data)
        res = admin.post("/admin/staff", data={**base, "name": ""}, follow_redirects=True)
        self.assertIn(b"required", res.data)
        res = admin.post("/admin/staff", data=base, follow_redirects=True)
        self.assertIn(b"successfully registered", res.data)
        res = admin.post("/admin/staff", data=base, follow_redirects=True)
        self.assertIn(b"already registered", res.data)
        res = self.login(app.test_client(), ("new.staff@wastemanage.gov", "StrongPass@1"))
        self.assertIn(b"Welcome back, New Staff", res.data)
        self.assertEqual(admin.get("/admin/staff").status_code, 200)

    def test_admin_pages_and_filters(self):
        citizen = self.register_citizen("admin.pages@example.com")
        self.file_complaint(citizen)
        admin = self.new_client(ADMIN)
        for path in ("/admin/dashboard", "/admin/complaints", "/admin/map",
                     "/admin/complaints?status=Pending&category_id=1&search=bin"):
            self.assertEqual(admin.get(path).status_code, 200, path)
        data = admin.get("/api/complaints-map").get_json()
        self.assertTrue(len(data) >= 1)
        self.assertIsInstance(data[0]["latitude"], float)

    def test_pdf_report_valid_and_survives_special_characters(self):
        citizen = self.register_citizen("pdf@example.com")
        self.file_complaint(citizen, address="Plot <b>7 & </i>Sector 9 <unclosed", title="Tag <script>")
        admin = self.new_client(ADMIN)
        res = admin.get("/admin/export-report-pdf")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "application/pdf")
        self.assertTrue(res.data.startswith(b"%PDF"))

    def test_html_is_escaped(self):
        citizen = self.register_citizen("xss@example.com")
        _, code = self.file_complaint(citizen, title="<script>alert(1)</script>")
        page = self.new_client(ADMIN).get("/admin/complaints").data
        self.assertNotIn(b"<script>alert(1)</script>", page)


class TestDemoSeed(Base):
    def _render_everything(self):
        """Every page must render (HTTP 200) for every role against realistic data."""
        codes = [r for r in self._all("SELECT complaint_code FROM complaints")]
        clients = {"anon": app.test_client(), "admin": self.new_client(ADMIN), "staff": self.new_client(STAFF),
                   "citizen": self.new_client(("soman@gmail.com", "Citizen@123"))}
        for who, c in clients.items():
            for code in codes:
                res = c.get(f"/track?complaint_code={code}")
                self.assertEqual(res.status_code, 200, (who, code))
        for path in ("/admin/dashboard", "/admin/complaints", "/admin/staff", "/admin/map"):
            self.assertEqual(clients["admin"].get(path).status_code, 200, path)
        self.assertEqual(clients["staff"].get("/staff/dashboard").status_code, 200)
        self.assertEqual(clients["citizen"].get("/citizen/dashboard").status_code, 200)
        self.assertEqual(clients["citizen"].get("/notifications").status_code, 200)
        self.assertEqual(clients["citizen"].get("/report").status_code, 200)
        pdf = clients["admin"].get("/admin/export-report-pdf")
        self.assertTrue(pdf.data.startswith(b"%PDF"))

    def _all(self, sql):
        conn, cur = get_db_cursor()
        try:
            cur.execute(sql)
            return [r["complaint_code"] for r in cur.fetchall()]
        finally:
            cur.close()
            conn.close()

    def test_seed_demo_data_is_complete_and_repeatable(self):
        from database import seed_demo
        seed_demo.seed_demo_data(force=True)
        statuses = {r: db_one("SELECT COUNT(*) AS n FROM complaints WHERE status = %s", (r,))["n"]
                    for r in ("Pending", "Assigned", "In Progress", "Resolved", "Rejected")}
        self.assertTrue(all(v >= 1 for v in statuses.values()), statuses)
        self.assertGreaterEqual(db_one("SELECT COUNT(*) AS n FROM feedback")["n"], 1)
        self.assertEqual(db_one("SELECT COUNT(*) AS n FROM complaints WHERE complaint_code LIKE 'TMP-%'")["n"], 0)
        self._render_everything()
        res = self.login(app.test_client(), ("soman@gmail.com", "Citizen@123"))
        self.assertIn(b"Welcome back", res.data)
        before = db_one("SELECT COUNT(*) AS n FROM complaints")["n"]
        seed_demo.seed_demo_data()                     # second run is a no-op
        self.assertEqual(db_one("SELECT COUNT(*) AS n FROM complaints")["n"], before)


if __name__ == "__main__":
    unittest.main()
