import os
import re
import time
import uuid
import secrets
import logging
from functools import wraps
from datetime import datetime, timedelta
from urllib.parse import urlparse
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, jsonify, abort, send_file
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from config import (
    SECRET_KEY, UPLOAD_FOLDER, ALLOWED_EXTENSIONS, MAX_CONTENT_LENGTH,
    FIREBASE_CONFIG, FIREBASE_ENABLED, DEBUG, DEMO_MODE, SESSION_COOKIE_SECURE
)
from db import get_db_cursor, get_connection
import report_generator
import firebase_service

# Initialize Flask App
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=SESSION_COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    CSRF_ENABLED=True,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("cleancity")

# Allowed values (mirror the database constraints)
STATUSES = ('Pending', 'Assigned', 'In Progress', 'Resolved', 'Rejected')
PRIORITIES = ('Low', 'Medium', 'High')
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Ensure upload directory exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def detect_image_type(header):
    """Identify a real image from its first bytes (never trust the file extension)."""
    if header.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if header[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if header[:4] == b'RIFF' and header[8:12] == b'WEBP':
        return 'webp'
    return None


def check_image_upload(file):
    """
    Validate an uploaded image WITHOUT saving it.
    Returns (image_type, error_message). error_message is None when valid.
    """
    if not file or not file.filename:
        return None, "Please attach a photograph."
    if not allowed_file(file.filename):
        return None, "Invalid file format. Please upload a JPG, PNG, GIF or WEBP image."
    header = file.stream.read(16)
    file.stream.seek(0)
    kind = detect_image_type(header)
    if not kind:
        return None, "The uploaded file is not a valid image."
    return kind, None


def save_image(file, prefix, kind):
    """Save a validated image under a unique, server-generated name."""
    name = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.{kind}"
    file.save(os.path.join(app.config['UPLOAD_FOLDER'], name))
    return name


def remove_upload(filename):
    """Best-effort cleanup of an uploaded file if the database step fails."""
    if not filename:
        return
    try:
        os.remove(os.path.join(app.config['UPLOAD_FOLDER'], filename))
    except OSError:
        pass


def flash_error(message, exc):
    """Log the real exception for developers; show the user a safe, generic message."""
    logger.error("%s: %s", message, exc, exc_info=True)
    flash(f"{message}. Please try again, or contact the administrator if it keeps happening.", "danger")


def is_safe_next_url(target):
    """Only allow same-site relative redirects (blocks //evil.com and /\\evil.com)."""
    if not target or not isinstance(target, str):
        return False
    if any(ch in target for ch in ('\\', '\r', '\n', '\t')):
        return False
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return False
    return target.startswith('/') and not target.startswith('//')


# ---- Simple in-memory brute-force protection for login endpoints ------------
_FAILED_LOGINS = {}
MAX_FAILED_LOGINS = 5
LOGIN_WINDOW_SECONDS = 15 * 60


def _login_key(email):
    return f"{request.remote_addr}|{email}"


def _recent_failures(email):
    now = time.time()
    attempts = [t for t in _FAILED_LOGINS.get(_login_key(email), []) if now - t < LOGIN_WINDOW_SECONDS]
    _FAILED_LOGINS[_login_key(email)] = attempts
    return attempts


def is_rate_limited(email):
    return len(_recent_failures(email)) >= MAX_FAILED_LOGINS


def record_failed_login(email):
    _recent_failures(email).append(time.time())


def clear_failed_logins(email):
    _FAILED_LOGINS.pop(_login_key(email), None)


# ---- CSRF protection --------------------------------------------------------
def get_csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_hex(16)
        session['_csrf_token'] = token
    return token


@app.before_request
def csrf_protect():
    if not app.config.get('CSRF_ENABLED', True):
        return None
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        sent = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token', '')
        expected = session.get('_csrf_token', '')
        if not expected or not sent or not secrets.compare_digest(str(expected), str(sent)):
            if request.path.startswith('/api/'):
                return jsonify({"success": False, "error": "Security token missing or expired. Refresh the page and try again."}), 400
            flash("Your session expired or the form was invalid. Please try again.", "warning")
            return redirect(url_for('home'))
    return None


@app.after_request
def add_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    return response


# ---- Friendly error pages ---------------------------------------------------
@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="The page you are looking for does not exist."), 404


@app.errorhandler(413)
def too_large(e):
    flash("That file is too large. Maximum upload size is 16 MB.", "danger")
    return redirect(request.referrer if request.referrer and is_safe_next_url(urlparse(request.referrer).path) else url_for('home'))


@app.errorhandler(500)
def server_error(e):
    logger.error("Unhandled server error: %s", e, exc_info=True)
    return render_template("error.html", code=500, message="Something went wrong on our side. Please try again."), 500


# -----------------------------------------------------------------------------
# Authentication & Role Decorators
# -----------------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please sign in to access this page.", "warning")
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                flash("Please sign in first.", "warning")
                return redirect(url_for('login'))
            if session.get('role') not in allowed_roles:
                flash("Access Denied: You do not have permission to access that module.", "danger")
                return redirect(url_for('home'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# -----------------------------------------------------------------------------
# Context Processor for Global Navbar Variables & Firebase Config
# -----------------------------------------------------------------------------
@app.context_processor
def inject_global_data():
    unread_count = 0
    if 'user_id' in session:
        conn = None
        try:
            conn, cur = get_db_cursor()
            cur.execute("""
                SELECT COUNT(*) AS unread 
                FROM notifications 
                WHERE user_id = %s AND is_read = FALSE
            """, (session['user_id'],))
            row = cur.fetchone()
            if row:
                unread_count = row['unread']
            cur.close()
        except Exception:
            pass
        finally:
            if conn and conn.is_connected():
                conn.close()
    return {
        'unread_notifications_count': unread_count,
        'firebase_config': FIREBASE_CONFIG if FIREBASE_ENABLED else {},
        'firebase_enabled': FIREBASE_ENABLED,
        'csrf_token': get_csrf_token,
        'demo_mode': DEMO_MODE,
    }


def setup_user_session(user_dict, cur):
    """Initializes user session in Flask and handles staff metadata if applicable."""
    session.clear()
    session['user_id'] = user_dict['user_id']
    session['name'] = user_dict['name']
    session['email'] = user_dict['email']
    session['role'] = user_dict['role']
    session['auth_provider'] = user_dict.get('auth_provider', 'firebase')

    if user_dict['role'] == 'staff':
        cur.execute("SELECT staff_id, employee_code, assigned_zone FROM staff WHERE user_id = %s", (user_dict['user_id'],))
        staff_info = cur.fetchone()
        if staff_info:
            session['staff_id'] = staff_info['staff_id']
            session['employee_code'] = staff_info['employee_code']
            session['assigned_zone'] = staff_info['assigned_zone']


def get_role_redirect_url(role, next_url=None):
    """Computes destination redirect based on role."""
    if is_safe_next_url(next_url):
        return next_url
    if role == 'admin':
        return url_for('admin_dashboard')
    elif role == 'staff':
        return url_for('staff_dashboard')
    else:
        return url_for('citizen_dashboard')


# -----------------------------------------------------------------------------
# Public Routes (Home, Awareness, Login, Register, Logout)
# -----------------------------------------------------------------------------
@app.route("/")
def home():
    """Landing page with live project statistics and overview."""
    stats = {
        'total_complaints': 0,
        'resolved_complaints': 0,
        'in_progress': 0,
        'total_staff': 0
    }
    conn = None
    try:
        conn, cur = get_db_cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM complaints")
        stats['total_complaints'] = cur.fetchone()['cnt']

        cur.execute("SELECT COUNT(*) AS cnt FROM complaints WHERE status = 'Resolved'")
        stats['resolved_complaints'] = cur.fetchone()['cnt']

        cur.execute("SELECT COUNT(*) AS cnt FROM complaints WHERE status IN ('Assigned', 'In Progress')")
        stats['in_progress'] = cur.fetchone()['cnt']

        cur.execute("SELECT COUNT(*) AS cnt FROM staff")
        stats['total_staff'] = cur.fetchone()['cnt']
        cur.close()
    except Exception as e:
        logger.error("Error fetching home stats: %s", e)
    finally:
        if conn and conn.is_connected():
            conn.close()

    return render_template("index.html", stats=stats)


@app.route("/awareness")
def awareness():
    """Waste Segregation Education and Citizen Guidelines."""
    return render_template("awareness.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    """Citizen registration route with Firebase Authentication."""
    if 'user_id' in session:
        return redirect(url_for('home'))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not name or not email or not password:
            flash("Please fill in all required fields.", "danger")
            return render_template("register.html")

        if not EMAIL_RE.match(email) or len(email) > 150 or len(name) > 100 or len(phone) > 15:
            flash("Please enter a valid name, email address and phone number.", "danger")
            return render_template("register.html")

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("register.html")

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "warning")
            return render_template("register.html")

        # 1. Attempt registration in Firebase Authentication
        fb_res = firebase_service.sign_up_with_email_password(email, password, display_name=name)
        if not fb_res.get("success") and not fb_res.get("offline"):
            if fb_res.get("code") == "EMAIL_EXISTS":
                flash("An account with this email already exists. Please log in.", "warning")
                return redirect(url_for('login'))
            # Log notice if operation not allowed or other configuration notice
            print(f"Firebase sign_up notice: {fb_res.get('error')}")

        conn = None
        try:
            conn, cur = get_db_cursor()
            cur.execute("SELECT user_id FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                flash("An account with this email already exists. Please login.", "warning")
                return redirect(url_for('login'))

            hashed_password = generate_password_hash(password)
            cur.execute("""
                INSERT INTO users (name, email, phone, password_hash, role)
                VALUES (%s, %s, %s, %s, 'citizen')
            """, (name, email, phone, hashed_password))
            conn.commit()

            new_user_id = cur.lastrowid
            cur.execute("""
                INSERT INTO notifications (user_id, title, message)
                VALUES (%s, %s, %s)
            """, (new_user_id, 'Welcome to CleanCity!', 'Thank you for registering. You can now report waste problems in your neighborhood and track them to resolution.'))
            conn.commit()

            cur.close()
            flash("Account created successfully with Firebase Auth! You can now log in.", "success")
            return redirect(url_for('login'))

        except Exception as e:
            if conn:
                conn.rollback()
            flash_error("Registration failed due to a system error", e)
        finally:
            if conn and conn.is_connected():
                conn.close()

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Unified login: local credentials for every role, Firebase fallback for citizens only."""
    if 'user_id' in session:
        role = session.get('role')
        return redirect(get_role_redirect_url(role))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            flash("Please enter both email and password.", "danger")
            return render_template("login.html")

        if is_rate_limited(email):
            flash("Too many failed sign-in attempts. Please wait 15 minutes and try again.", "danger")
            return render_template("login.html"), 429

        conn = None
        try:
            conn, cur = get_db_cursor()

            # Check 1: local database password hash (works for every role)
            cur.execute("SELECT * FROM users WHERE email = %s", (email,))
            user = cur.fetchone()
            authenticated = bool(user and check_password_hash(user['password_hash'], password))

            # Check 2: Firebase Auth - CITIZEN accounts only.
            # Staff/admin accounts must never be unlocked by a Firebase sign-up, because
            # anyone can create a Firebase account using any e-mail address.
            if not authenticated and (user is None or user['role'] == 'citizen'):
                fb_res = firebase_service.sign_in_with_email_password(email, password)
                if fb_res.get("success"):
                    authenticated = True
                    if not user:
                        cur.execute("""
                            INSERT INTO users (name, email, password_hash, role)
                            VALUES (%s, %s, %s, 'citizen')
                        """, (email.split('@')[0].capitalize(), email, generate_password_hash(password)))
                        conn.commit()
                        cur.execute("SELECT * FROM users WHERE user_id = %s", (cur.lastrowid,))
                        user = cur.fetchone()

            if authenticated and user:
                clear_failed_logins(email)
                setup_user_session(user, cur)
                flash(f"Welcome back, {user['name']}!", "success")
                cur.close()
                return redirect(get_role_redirect_url(user['role'], request.args.get('next')))

            record_failed_login(email)
            flash("Invalid email or password. Please verify your credentials.", "danger")
            cur.close()

        except Exception as e:
            flash_error("Login failed", e)
        finally:
            if conn and conn.is_connected():
                conn.close()

    return render_template("login.html")


@app.route("/logout")
def logout():
    """Logout and clear user session."""
    session.clear()
    flash("You have been signed out successfully.", "info")
    return redirect(url_for('login'))


# -----------------------------------------------------------------------------
# Firebase Authentication JSON API Endpoints (Used by client-side Firebase Auth)
# -----------------------------------------------------------------------------
@app.route("/api/auth/firebase-config", methods=["GET"])
def api_firebase_config():
    """Returns the Firebase Web SDK configuration."""
    return jsonify(FIREBASE_CONFIG)


def _start_session_for_email(email, display_name, next_url):
    """
    Create the Flask session for a Firebase-verified e-mail address.
    Only CITIZEN accounts may be opened this way; auto-provisions a new citizen if needed.
    """
    conn = None
    try:
        conn, cur = get_db_cursor()
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cur.fetchone()

        if user and user['role'] != 'citizen':
            cur.close()
            return jsonify({
                "success": False,
                "error": "Staff and administrator accounts must sign in with their official password."
            }), 403

        if not user:
            name = (display_name or "").strip() or email.split('@')[0].capitalize()
            cur.execute("""
                INSERT INTO users (name, email, password_hash, role)
                VALUES (%s, %s, %s, 'citizen')
            """, (name[:100], email, generate_password_hash(uuid.uuid4().hex)))
            new_id = cur.lastrowid
            cur.execute("""
                INSERT INTO notifications (user_id, title, message)
                VALUES (%s, %s, %s)
            """, (new_id, 'Welcome to CleanCity!', 'Your account has been authenticated with Firebase. You can now submit and track waste reports.'))
            conn.commit()
            cur.execute("SELECT * FROM users WHERE user_id = %s", (new_id,))
            user = cur.fetchone()

        setup_user_session(user, cur)
        cur.close()
        return jsonify({
            "success": True,
            "redirect": get_role_redirect_url(user['role'], next_url),
            "role": user['role'],
            "user": {"user_id": user['user_id'], "name": user['name'], "email": user['email']}
        })
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error("Firebase session sync failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": "Could not start your session. Please try again."}), 500
    finally:
        if conn and conn.is_connected():
            conn.close()


@app.route("/api/auth/firebase-login", methods=["POST"])
def api_firebase_login():
    """
    Session synchronisation endpoint. Two ways to prove identity (never just an e-mail):
      1. idToken  - a Firebase ID token, verified server-side with Google.
      2. email + password - checked against the local DB, then (citizens only) Firebase.
    """
    data = request.get_json(silent=True) or request.form.to_dict()
    id_token = data.get("idToken")
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    next_url = data.get("next")

    # --- 1. Firebase ID token ------------------------------------------------
    if id_token:
        verify_res = firebase_service.verify_id_token(id_token)
        verified_email = (verify_res.get("email") or "").strip().lower()
        if not verify_res.get("success") or not verified_email:
            return jsonify({"success": False, "error": "Firebase sign-in could not be verified."}), 401
        return _start_session_for_email(verified_email, data.get("name") or verify_res.get("display_name"), next_url)

    # --- 2. E-mail + password ------------------------------------------------
    if not email or not password:
        return jsonify({"success": False, "error": "Email and password are required."}), 400

    if is_rate_limited(email):
        return jsonify({"success": False, "error": "Too many failed attempts. Please wait 15 minutes."}), 429

    conn = None
    try:
        conn, cur = get_db_cursor()
        cur.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cur.fetchone()

        if user and check_password_hash(user['password_hash'], password):
            clear_failed_logins(email)
            setup_user_session(user, cur)
            cur.close()
            return jsonify({
                "success": True,
                "redirect": get_role_redirect_url(user['role'], next_url),
                "role": user['role'],
                "user": {"name": user['name'], "email": user['email']}
            })
        cur.close()
        local_role = user['role'] if user else None
    except Exception as e:
        logger.error("Local login check failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": "Could not check your credentials. Please try again."}), 500
    finally:
        if conn and conn.is_connected():
            conn.close()

    # Firebase fallback is only for citizens (or brand-new users)
    if local_role in (None, 'citizen'):
        fb_auth = firebase_service.sign_in_with_email_password(email, password)
        if fb_auth.get("success"):
            clear_failed_logins(email)
            return _start_session_for_email((fb_auth.get("email") or email).strip().lower(), data.get("name"), next_url)

    record_failed_login(email)
    return jsonify({"success": False, "error": "Invalid email or password."}), 401


@app.route("/api/auth/firebase-register", methods=["POST"])
def api_firebase_register():
    """Register a citizen and keep Firebase Auth and the local database in sync."""
    data = request.get_json(silent=True) or request.form.to_dict()
    email = (data.get("email") or "").strip().lower()
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    password = data.get("password") or ""
    id_token = data.get("idToken")

    if not email or not name:
        return jsonify({"success": False, "error": "Name and email are required fields."}), 400
    if not EMAIL_RE.match(email) or len(email) > 150 or len(name) > 100 or len(phone) > 15:
        return jsonify({"success": False, "error": "Please enter a valid name, email and phone number."}), 400

    if id_token:
        # Trust only the e-mail Firebase itself reports for the token
        verify_res = firebase_service.verify_id_token(id_token)
        verified_email = (verify_res.get("email") or "").strip().lower()
        if not verify_res.get("success") or verified_email != email:
            return jsonify({"success": False, "error": "Firebase sign-up could not be verified. Please log in instead."}), 401
    else:
        if len(password) < 6:
            return jsonify({"success": False, "error": "Password must be at least 6 characters long."}), 400
        fb_res = firebase_service.sign_up_with_email_password(email, password, display_name=name)
        if not fb_res.get("success") and not fb_res.get("offline"):
            if fb_res.get("code") == "EMAIL_EXISTS":
                return jsonify({"success": False, "error": "An account with this email already exists."}), 400

    conn = None
    try:
        conn, cur = get_db_cursor()
        cur.execute("SELECT user_id FROM users WHERE email = %s", (email,))
        if cur.fetchone():
            cur.close()
            return jsonify({"success": False, "error": "An account with this email already exists."}), 400

        pwd_hash = generate_password_hash(password if password else uuid.uuid4().hex)
        cur.execute("""
            INSERT INTO users (name, email, phone, password_hash, role)
            VALUES (%s, %s, %s, %s, 'citizen')
        """, (name, email, phone, pwd_hash))
        new_user_id = cur.lastrowid
        cur.execute("""
            INSERT INTO notifications (user_id, title, message)
            VALUES (%s, %s, %s)
        """, (new_user_id, 'Welcome to CleanCity!', 'Your citizen account has been successfully registered.'))
        conn.commit()

        cur.execute("SELECT * FROM users WHERE user_id = %s", (new_user_id,))
        user = cur.fetchone()
        setup_user_session(user, cur)
        cur.close()

        return jsonify({
            "success": True,
            "redirect": url_for('citizen_dashboard'),
            "user": {"name": name, "email": email}
        })
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error("Registration failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": "Registration failed. Please try again."}), 500
    finally:
        if conn and conn.is_connected():
            conn.close()


@app.route("/api/auth/forgot-password", methods=["POST"])
def api_forgot_password():
    """Trigger Firebase password reset email."""
    data = request.get_json(silent=True) or request.form.to_dict()
    email = data.get("email", "").strip().lower()
    if not email:
        return jsonify({"success": False, "error": "Please provide an email address."}), 400

    res = firebase_service.send_password_reset_email(email)
    # Same answer whether or not the address exists (prevents account enumeration)
    if res.get("success") or res.get("code") == "EMAIL_NOT_FOUND":
        return jsonify({"success": True, "message": "If that account exists, password reset instructions have been sent to the email address."})
    return jsonify({"success": False, "error": "Unable to send password reset email right now. Please try again later."}), 400


@app.route("/report", methods=["GET", "POST"])
@login_required
def report_complaint():
    """Submit a new waste complaint with GPS & Photo."""
    conn = None
    if request.method == "POST":
        category_id = request.form.get("category_id", type=int)
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        address = request.form.get("address", "").strip()
        file = request.files.get("before_image")

        if not category_id or not title or not description or not address \
                or not request.form.get("latitude") or not request.form.get("longitude"):
            flash("Please fill in all required fields and pinpoint the location.", "danger")
            return redirect(url_for('report_complaint'))

        try:
            latitude = float(request.form.get("latitude", "").strip())
            longitude = float(request.form.get("longitude", "").strip())
        except ValueError:
            flash("The GPS location is not valid. Please pinpoint it again on the map.", "danger")
            return redirect(url_for('report_complaint'))
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            flash("The GPS location is out of range. Please pinpoint it again on the map.", "danger")
            return redirect(url_for('report_complaint'))

        if len(title) > 200 or len(address) > 255 or len(description) > 2000:
            flash("Title (200), address (255) or description (2000) is too long.", "danger")
            return redirect(url_for('report_complaint'))

        if not file or file.filename == '':
            flash("Please attach a photograph of the waste.", "warning")
            return redirect(url_for('report_complaint'))
        image_kind, image_error = check_image_upload(file)
        if image_error:
            flash(image_error, "danger")
            return redirect(url_for('report_complaint'))

        filename = None
        try:
            conn, cur = get_db_cursor()

            cur.execute("SELECT default_priority FROM categories WHERE category_id = %s", (category_id,))
            cat_row = cur.fetchone()
            if not cat_row:
                cur.close()
                flash("Please choose a valid waste category.", "danger")
                return redirect(url_for('report_complaint'))
            priority = cat_row['default_priority'] or 'Medium'

            filename = save_image(file, "before", image_kind)

            # Insert with a temporary unique code, then derive the final code from the
            # row id. (COUNT(*)+1 produced duplicate codes whenever a row was deleted.)
            cur.execute("""
                INSERT INTO complaints (
                    complaint_code, citizen_id, category_id, title, description,
                    address, latitude, longitude, before_image, status, priority
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'Pending', %s)
            """, (
                f"TMP-{uuid.uuid4().hex[:12]}", session['user_id'], category_id, title,
                description, address, latitude, longitude, filename, priority
            ))
            complaint_id = cur.lastrowid
            complaint_code = f"WMR-{datetime.now().strftime('%Y%m')}-{complaint_id:04d}"
            cur.execute("UPDATE complaints SET complaint_code = %s WHERE complaint_id = %s",
                        (complaint_code, complaint_id))

            cur.execute("""
                INSERT INTO status_history (complaint_id, changed_by_user_id, old_status, new_status, remarks)
                VALUES (%s, %s, NULL, 'Pending', 'Complaint filed by citizen with photographic and GPS evidence.')
            """, (complaint_id, session['user_id']))

            cur.execute("""
                INSERT INTO notifications (user_id, complaint_id, title, message)
                VALUES (%s, %s, %s, %s)
            """, (
                session['user_id'], complaint_id, 'Complaint Registered',
                f'Your waste grievance has been recorded under tracking code {complaint_code}. Sanitation authorities have been notified.'
            ))

            conn.commit()
            cur.close()
            flash(f"Complaint submitted successfully! Your tracking code is {complaint_code}.", "success")
            return redirect(url_for('track_complaint', complaint_code=complaint_code))

        except Exception as e:
            if conn:
                conn.rollback()
            remove_upload(filename)
            flash_error("Failed to record complaint", e)
            return redirect(url_for('report_complaint'))
        finally:
            if conn and conn.is_connected():
                conn.close()

    # GET Request
    try:
        conn, cur = get_db_cursor()
        cur.execute("SELECT * FROM categories ORDER BY name ASC")
        categories = cur.fetchall()
        cur.close()
        return render_template("report_complaint.html", categories=categories)
    except Exception as e:
        flash_error("Error loading categories", e)
        return redirect(url_for('home'))
    finally:
        if conn and conn.is_connected():
            conn.close()


@app.route("/citizen/dashboard")
@role_required('citizen')
def citizen_dashboard():
    """Citizen Dashboard displaying complaints history."""
    conn = None
    try:
        conn, cur = get_db_cursor()
        cur.execute("""
            SELECT c.*, cat.name AS category_name
            FROM complaints c
            JOIN categories cat ON c.category_id = cat.category_id
            WHERE c.citizen_id = %s
            ORDER BY c.created_at DESC
        """, (session['user_id'],))
        complaints = cur.fetchall()
        cur.close()
        return render_template("dashboard.html", complaints=complaints)
    except Exception as e:
        flash_error("Error loading dashboard", e)
        return redirect(url_for('home'))
    finally:
        if conn and conn.is_connected():
            conn.close()


@app.route("/feedback/<int:complaint_id>", methods=["POST"])
@login_required
def submit_feedback(complaint_id):
    """Citizen feedback submission on resolved complaints."""
    rating = request.form.get("rating", type=int)
    comments = request.form.get("comments", "").strip()[:1000]

    if not rating or rating < 1 or rating > 5:
        flash("Please provide a valid rating between 1 and 5.", "warning")
        return redirect(url_for('home'))

    conn = None
    try:
        conn, cur = get_db_cursor()
        cur.execute("SELECT complaint_code, citizen_id, status FROM complaints WHERE complaint_id = %s", (complaint_id,))
        complaint = cur.fetchone()
        if not complaint or complaint['citizen_id'] != session['user_id']:
            flash("You can only submit feedback for complaints you submitted.", "danger")
            cur.close()
            return redirect(url_for('home'))

        if complaint['status'] != 'Resolved':
            flash("Feedback can only be submitted after the complaint has been resolved.", "warning")
            cur.close()
            return redirect(url_for('track_complaint', complaint_code=complaint['complaint_code']))

        cur.execute("""
            INSERT INTO feedback (complaint_id, citizen_id, rating, comments)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE rating = VALUES(rating), comments = VALUES(comments)
        """, (complaint_id, session['user_id'], rating, comments))
        conn.commit()
        cur.close()
        flash("Thank you for your feedback! Your rating helps improve municipal cleanliness.", "success")
        return redirect(url_for('track_complaint', complaint_code=complaint['complaint_code']))
    except Exception as e:
        if conn:
            conn.rollback()
        flash_error("Error saving feedback", e)
        return redirect(url_for('home'))
    finally:
        if conn and conn.is_connected():
            conn.close()


# -----------------------------------------------------------------------------
# Staff Routes: Work Orders & Photographic Resolution
# -----------------------------------------------------------------------------
@app.route("/staff/dashboard")
@role_required('staff')
def staff_dashboard():
    """Sanitation Staff work orders portal."""
    staff_id = session.get('staff_id')
    conn = None
    try:
        conn, cur = get_db_cursor()
        cur.execute("""
            SELECT c.*, cat.name AS category_name, u.name AS citizen_name, u.phone AS citizen_phone
            FROM complaints c
            JOIN categories cat ON c.category_id = cat.category_id
            JOIN users u ON c.citizen_id = u.user_id
            WHERE c.assigned_staff_id = %s
            ORDER BY FIELD(c.status, 'In Progress', 'Assigned', 'Resolved', 'Rejected'), c.created_at DESC
        """, (staff_id,))
        tasks = cur.fetchall()
        cur.close()
        return render_template("staff_dashboard.html", tasks=tasks)
    except Exception as e:
        flash_error("Error loading staff portal", e)
        return redirect(url_for('home'))
    finally:
        if conn and conn.is_connected():
            conn.close()


@app.route("/staff/update-status/<int:complaint_id>", methods=["POST"])
@role_required('staff')
def staff_update_status(complaint_id):
    """Staff marks THEIR OWN assigned work order as 'In Progress'."""
    conn = None
    try:
        conn, cur = get_db_cursor()
        cur.execute("SELECT * FROM complaints WHERE complaint_id = %s", (complaint_id,))
        complaint = cur.fetchone()

        if not complaint:
            flash("Complaint not found.", "danger")
            cur.close()
            return redirect(url_for('staff_dashboard'))

        staff_id = session.get('staff_id')
        if staff_id is None or complaint['assigned_staff_id'] != staff_id:
            flash("This work order is not assigned to you.", "danger")
            cur.close()
            return redirect(url_for('staff_dashboard'))

        if complaint['status'] != 'Assigned':
            flash(f"Only 'Assigned' work orders can be started (current status: {complaint['status']}).", "warning")
            cur.close()
            return redirect(url_for('track_complaint', complaint_code=complaint['complaint_code']))

        new_status = 'In Progress'
        cur.execute("UPDATE complaints SET status = %s, updated_at = NOW() WHERE complaint_id = %s", (new_status, complaint_id))
        cur.execute("""
            INSERT INTO status_history (complaint_id, changed_by_user_id, old_status, new_status, remarks)
            VALUES (%s, %s, %s, %s, %s)
        """, (complaint_id, session['user_id'], complaint['status'], new_status, 'Sanitation staff initiated ground cleaning.'))

        cur.execute("""
            INSERT INTO notifications (user_id, complaint_id, title, message)
            VALUES (%s, %s, %s, %s)
        """, (complaint['citizen_id'], complaint_id, 'Cleaning In Progress', f"Sanitation staff has arrived at the location for ticket {complaint['complaint_code']}."))

        conn.commit()
        cur.close()
        flash("Work order marked as 'In Progress'.", "success")
        return redirect(url_for('track_complaint', complaint_code=complaint['complaint_code']))
    except Exception as e:
        if conn:
            conn.rollback()
        flash_error("Error updating status", e)
        return redirect(url_for('staff_dashboard'))
    finally:
        if conn and conn.is_connected():
            conn.close()


@app.route("/staff/complete/<int:complaint_id>", methods=["POST"])
@role_required('staff')
def staff_complete_work(complaint_id):
    """Staff uploads after-cleaning photo proof for THEIR OWN work order and resolves it."""
    file = request.files.get("after_image")
    remarks = request.form.get("remarks", "").strip()[:500] or "Ground cleaning completed with photo proof."

    if not file or file.filename == '':
        flash("Please upload after-cleaning photographic proof.", "warning")
        return redirect(url_for('staff_dashboard'))
    image_kind, image_error = check_image_upload(file)
    if image_error:
        flash(image_error, "danger")
        return redirect(url_for('staff_dashboard'))

    conn = None
    unique_name = None
    try:
        conn, cur = get_db_cursor()
        cur.execute("SELECT * FROM complaints WHERE complaint_id = %s", (complaint_id,))
        complaint = cur.fetchone()

        if not complaint:
            flash("Complaint not found.", "danger")
            cur.close()
            return redirect(url_for('staff_dashboard'))

        staff_id = session.get('staff_id')
        if staff_id is None or complaint['assigned_staff_id'] != staff_id:
            flash("This work order is not assigned to you.", "danger")
            cur.close()
            return redirect(url_for('staff_dashboard'))

        if complaint['status'] not in ('Assigned', 'In Progress'):
            flash(f"This work order cannot be completed (current status: {complaint['status']}).", "warning")
            cur.close()
            return redirect(url_for('track_complaint', complaint_code=complaint['complaint_code']))

        unique_name = save_image(file, "after", image_kind)

        cur.execute("""
            UPDATE complaints
            SET after_image = %s, status = 'Resolved', resolved_at = NOW(), updated_at = NOW()
            WHERE complaint_id = %s
        """, (unique_name, complaint_id))

        cur.execute("""
            INSERT INTO status_history (complaint_id, changed_by_user_id, old_status, new_status, remarks)
            VALUES (%s, %s, %s, 'Resolved', %s)
        """, (complaint_id, session['user_id'], complaint['status'], remarks))

        cur.execute("""
            INSERT INTO notifications (user_id, complaint_id, title, message)
            VALUES (%s, %s, %s, %s)
        """, (
            complaint['citizen_id'], complaint_id, 'Garbage Issue Cleaned & Resolved',
            f"The reported waste for ticket {complaint['complaint_code']} has been cleaned. View after-photo proof and share your feedback."
        ))

        conn.commit()
        cur.close()
        flash("After-cleaning photo uploaded and complaint marked as Resolved!", "success")
        return redirect(url_for('track_complaint', complaint_code=complaint['complaint_code']))
    except Exception as e:
        if conn:
            conn.rollback()
        remove_upload(unique_name)
        flash_error("Error completing work", e)
        return redirect(url_for('staff_dashboard'))
    finally:
        if conn and conn.is_connected():
            conn.close()


# -----------------------------------------------------------------------------
# Admin Routes: Triage, Geospatial Map, Staff Dispatch & PDF Report
# -----------------------------------------------------------------------------
@app.route("/admin/dashboard")
@role_required('admin')
def admin_dashboard():
    """Admin Command Center Overview."""
    conn = None
    try:
        conn, cur = get_db_cursor()
        cur.execute("SELECT COUNT(*) AS total FROM complaints")
        total_complaints = cur.fetchone()['total']

        cur.execute("SELECT COUNT(*) AS pending FROM complaints WHERE status = 'Pending'")
        pending_complaints = cur.fetchone()['pending']

        cur.execute("SELECT COUNT(*) AS in_progress FROM complaints WHERE status IN ('Assigned', 'In Progress')")
        active_complaints = cur.fetchone()['in_progress']

        cur.execute("SELECT COUNT(*) AS resolved FROM complaints WHERE status = 'Resolved'")
        resolved_complaints = cur.fetchone()['resolved']

        cur.execute("""
            SELECT c.*, cat.name AS category_name, u.name AS citizen_name
            FROM complaints c
            JOIN categories cat ON c.category_id = cat.category_id
            JOIN users u ON c.citizen_id = u.user_id
            ORDER BY c.created_at DESC LIMIT 5
        """)
        recent_complaints = cur.fetchall()

        cur.close()
        metrics = {
            'total': total_complaints,
            'pending': pending_complaints,
            'active': active_complaints,
            'resolved': resolved_complaints
        }
        return render_template("admin_dashboard.html", metrics=metrics, recent_complaints=recent_complaints)
    except Exception as e:
        flash_error("Error loading admin dashboard", e)
        return redirect(url_for('home'))
    finally:
        if conn and conn.is_connected():
            conn.close()


@app.route("/admin/complaints")
@role_required('admin')
def admin_complaints():
    """Full triage queue with filters and search."""
    search_query = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "").strip()
    category_filter = request.args.get("category_id", "").strip()

    conn = None
    try:
        conn, cur = get_db_cursor()

        sql = """
            SELECT c.*, cat.name AS category_name, u.name AS citizen_name, u.phone AS citizen_phone,
                   s.employee_code, su.name AS staff_name
            FROM complaints c
            JOIN categories cat ON c.category_id = cat.category_id
            JOIN users u ON c.citizen_id = u.user_id
            LEFT JOIN staff s ON c.assigned_staff_id = s.staff_id
            LEFT JOIN users su ON s.user_id = su.user_id
            WHERE 1=1
        """
        params = []

        if search_query:
            sql += " AND (c.complaint_code LIKE %s OR c.address LIKE %s OR c.title LIKE %s)"
            wildcard = f"%{search_query}%"
            params.extend([wildcard, wildcard, wildcard])

        if status_filter:
            sql += " AND c.status = %s"
            params.append(status_filter)

        if category_filter:
            sql += " AND c.category_id = %s"
            params.append(category_filter)

        sql += " ORDER BY c.created_at DESC"
        cur.execute(sql, tuple(params))
        complaints = cur.fetchall()

        cur.execute("SELECT * FROM categories ORDER BY name ASC")
        categories = cur.fetchall()

        cur.close()
        return render_template(
            "admin_complaints.html",
            complaints=complaints,
            categories=categories,
            search_query=search_query,
            status_filter=status_filter,
            category_filter=category_filter
        )
    except Exception as e:
        flash_error("Error loading complaints", e)
        return redirect(url_for('admin_dashboard'))
    finally:
        if conn and conn.is_connected():
            conn.close()


@app.route("/admin/update-complaint/<int:complaint_id>", methods=["POST"])
@role_required('admin')
def admin_update_complaint(complaint_id):
    """Admin updates staff assignment, priority, or status."""
    assigned_staff_id = request.form.get("assigned_staff_id", type=int)
    priority = request.form.get("priority", "Medium")
    new_status = request.form.get("status", "Pending")
    remarks = request.form.get("remarks", "").strip()[:500] or "Updated by Administrator."

    if new_status not in STATUSES or priority not in PRIORITIES:
        flash("Invalid status or priority value.", "danger")
        return redirect(url_for('admin_complaints'))

    conn = None
    try:
        conn, cur = get_db_cursor()
        cur.execute("SELECT * FROM complaints WHERE complaint_id = %s", (complaint_id,))
        complaint = cur.fetchone()

        if not complaint:
            flash("Complaint not found.", "danger")
            cur.close()
            return redirect(url_for('admin_complaints'))

        staff_user_id = None
        if assigned_staff_id:
            cur.execute("SELECT user_id FROM staff WHERE staff_id = %s", (assigned_staff_id,))
            staff_row = cur.fetchone()
            if not staff_row:
                flash("The selected staff member does not exist.", "danger")
                cur.close()
                return redirect(url_for('track_complaint', complaint_code=complaint['complaint_code']))
            staff_user_id = staff_row['user_id']

        # Newly assigned while still Pending -> becomes Assigned
        if assigned_staff_id and new_status == 'Pending':
            new_status = 'Assigned'

        if new_status in ('Assigned', 'In Progress') and not assigned_staff_id:
            flash("Please select a staff member before setting the status to Assigned / In Progress.", "warning")
            cur.close()
            return redirect(url_for('track_complaint', complaint_code=complaint['complaint_code']))

        if new_status == 'Resolved':
            time_clause = ", resolved_at = NOW(), updated_at = NOW()"
        else:
            time_clause = ", resolved_at = NULL, updated_at = NOW()"
        rejection_clause = ", rejection_reason = %s" if new_status == 'Rejected' else ""

        params = [assigned_staff_id, priority, new_status]
        if new_status == 'Rejected':
            params.append(remarks)
        params.append(complaint_id)

        cur.execute(f"""
            UPDATE complaints
            SET assigned_staff_id = %s, priority = %s, status = %s {time_clause} {rejection_clause}
            WHERE complaint_id = %s
        """, tuple(params))

        cur.execute("""
            INSERT INTO status_history (complaint_id, changed_by_user_id, old_status, new_status, remarks)
            VALUES (%s, %s, %s, %s, %s)
        """, (complaint_id, session['user_id'], complaint['status'], new_status, remarks))

        cur.execute("""
            INSERT INTO notifications (user_id, complaint_id, title, message)
            VALUES (%s, %s, %s, %s)
        """, (
            complaint['citizen_id'], complaint_id, f'Ticket Status: {new_status}',
            f"Your complaint {complaint['complaint_code']} was updated to '{new_status}'. Remarks: {remarks}"
        ))

        # Notify staff only when the assignment actually changed
        if staff_user_id and assigned_staff_id != complaint['assigned_staff_id']:
            cur.execute("""
                INSERT INTO notifications (user_id, complaint_id, title, message)
                VALUES (%s, %s, %s, %s)
            """, (
                staff_user_id, complaint_id, 'New Task Assigned',
                f"You have been assigned complaint {complaint['complaint_code']} at {complaint['address']}."
            ))

        conn.commit()
        cur.close()
        flash(f"Complaint {complaint['complaint_code']} updated successfully.", "success")
        return redirect(url_for('track_complaint', complaint_code=complaint['complaint_code']))
    except Exception as e:
        if conn:
            conn.rollback()
        flash_error("Failed to update complaint", e)
        return redirect(url_for('admin_complaints'))
    finally:
        if conn and conn.is_connected():
            conn.close()


@app.route("/admin/map")
@role_required('admin')
def admin_map():
    """Geospatial hotspot map page."""
    return render_template("admin_map.html")


@app.route("/api/complaints-map")
def api_complaints_map():
    """API returning all complaint coordinates for the admin Leaflet map (admin only)."""
    if 'user_id' not in session:
        return jsonify({"error": "Authentication required."}), 401
    if session.get('role') != 'admin':
        return jsonify({"error": "Administrator access required."}), 403

    conn = None
    try:
        conn, cur = get_db_cursor()
        cur.execute("""
            SELECT c.complaint_id, c.complaint_code, c.title, c.address,
                   c.latitude, c.longitude, c.before_image, c.status, c.priority,
                   cat.name AS category_name
            FROM complaints c
            JOIN categories cat ON c.category_id = cat.category_id
        """)
        rows = cur.fetchall()
        cur.close()
        result = []
        for r in rows:
            item = dict(r)
            item['latitude'] = float(item['latitude'])
            item['longitude'] = float(item['longitude'])
            result.append(item)
        return jsonify(result)
    except Exception as e:
        logger.error("Map API failed: %s", e, exc_info=True)
        return jsonify({"error": "Could not load map data."}), 500
    finally:
        if conn and conn.is_connected():
            conn.close()


@app.route("/admin/staff", methods=["GET", "POST"])
@role_required('admin')
def admin_staff():
    """Manage and register sanitation staff."""
    conn = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        employee_code = request.form.get("employee_code", "").strip().upper()
        assigned_zone = request.form.get("assigned_zone", "").strip() or "Zone A"
        department_id = request.form.get("department_id", type=int)
        designation = request.form.get("designation", "").strip() or "Field Sanitation Officer"
        password = request.form.get("password", "")

        if not name or not email or not employee_code:
            flash("Name, email and employee code are required.", "danger")
            return redirect(url_for('admin_staff'))
        if not EMAIL_RE.match(email) or len(email) > 150 or len(name) > 100 or len(phone) > 15 \
                or len(employee_code) > 50 or len(assigned_zone) > 100 or len(designation) > 100:
            flash("Please enter a valid name, email, phone and employee details.", "danger")
            return redirect(url_for('admin_staff'))
        if len(password) < 8:
            flash("The temporary password must be at least 8 characters long.", "danger")
            return redirect(url_for('admin_staff'))

        try:
            conn, cur = get_db_cursor()

            if department_id:
                cur.execute("SELECT department_id FROM departments WHERE department_id = %s", (department_id,))
                if not cur.fetchone():
                    flash("The selected department does not exist.", "danger")
                    cur.close()
                    return redirect(url_for('admin_staff'))
            else:
                department_id = None

            cur.execute("SELECT user_id FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                flash("Email already registered for another user.", "warning")
                cur.close()
                return redirect(url_for('admin_staff'))

            cur.execute("SELECT staff_id FROM staff WHERE employee_code = %s", (employee_code,))
            if cur.fetchone():
                flash("Employee code already in use.", "warning")
                cur.close()
                return redirect(url_for('admin_staff'))

            cur.execute("""
                INSERT INTO users (name, email, phone, password_hash, role)
                VALUES (%s, %s, %s, %s, 'staff')
            """, (name, email, phone, generate_password_hash(password)))
            new_user_id = cur.lastrowid

            cur.execute("""
                INSERT INTO staff (user_id, department_id, employee_code, designation, assigned_zone, status)
                VALUES (%s, %s, %s, %s, %s, 'available')
            """, (new_user_id, department_id, employee_code, designation, assigned_zone))

            conn.commit()
            cur.close()
            flash(f"Staff member {name} ({employee_code}) successfully registered!", "success")
            return redirect(url_for('admin_staff'))
        except Exception as e:
            if conn:
                conn.rollback()
            flash_error("Error registering staff", e)
            return redirect(url_for('admin_staff'))
        finally:
            if conn and conn.is_connected():
                conn.close()

    # GET Request
    try:
        conn, cur = get_db_cursor()
        cur.execute("""
            SELECT s.*, u.name, u.email, u.phone, d.name AS department_name,
                   (SELECT COUNT(*) FROM complaints c WHERE c.assigned_staff_id = s.staff_id AND c.status IN ('Assigned', 'In Progress')) AS active_task_count
            FROM staff s
            JOIN users u ON s.user_id = u.user_id
            LEFT JOIN departments d ON s.department_id = d.department_id
            ORDER BY s.created_at DESC
        """)
        staff_members = cur.fetchall()

        cur.execute("SELECT * FROM departments ORDER BY name ASC")
        departments = cur.fetchall()

        cur.close()
        return render_template("admin_staff.html", staff_members=staff_members, departments=departments)
    except Exception as e:
        flash_error("Error loading staff", e)
        return redirect(url_for('admin_dashboard'))
    finally:
        if conn and conn.is_connected():
            conn.close()


@app.route("/admin/export-report-pdf")
@role_required('admin')
def admin_export_pdf():
    """Generates and downloads official monthly PDF audit report."""
    conn = None
    try:
        conn, cur = get_db_cursor()

        # Stats
        cur.execute("SELECT COUNT(*) AS total FROM complaints")
        total = cur.fetchone()['total']
        cur.execute("SELECT COUNT(*) AS resolved FROM complaints WHERE status = 'Resolved'")
        resolved = cur.fetchone()['resolved']
        cur.execute("SELECT COUNT(*) AS active FROM complaints WHERE status IN ('Assigned', 'In Progress')")
        active = cur.fetchone()['active']
        cur.execute("SELECT COUNT(*) AS pending FROM complaints WHERE status = 'Pending'")
        pending = cur.fetchone()['pending']

        stats = {'total': total, 'resolved': resolved, 'active': active, 'pending': pending}

        # Complaints list
        cur.execute("""
            SELECT c.*, cat.name AS category_name
            FROM complaints c
            JOIN categories cat ON c.category_id = cat.category_id
            ORDER BY c.created_at DESC
        """)
        complaints = cur.fetchall()

        # Staff list
        cur.execute("""
            SELECT s.*, u.name, d.name AS department_name
            FROM staff s
            JOIN users u ON s.user_id = u.user_id
            LEFT JOIN departments d ON s.department_id = d.department_id
        """)
        staff_list = cur.fetchall()
        cur.close()

        pdf_buffer = report_generator.generate_pdf_report(stats, complaints, staff_list)

        filename = f"Waste_Management_Report_{datetime.now().strftime('%Y%m%d')}.pdf"
        return send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        flash_error("Error generating PDF report", e)
        return redirect(url_for('admin_dashboard'))
    finally:
        if conn and conn.is_connected():
            conn.close()


# -----------------------------------------------------------------------------
# Public Complaint Tracking by ID
# -----------------------------------------------------------------------------
@app.route("/track")
def track_complaint():
    """Look up a complaint by its unique tracking code."""
    code = request.args.get("complaint_code", "").strip().upper()
    if not code:
        flash("Please enter a valid tracking ID.", "warning")
        return redirect(url_for('home'))

    conn = None
    try:
        conn, cur = get_db_cursor()
        cur.execute("""
            SELECT c.*, cat.name AS category_name, u.name AS citizen_name,
                   s.employee_code, su.name AS staff_name
            FROM complaints c
            JOIN categories cat ON c.category_id = cat.category_id
            JOIN users u ON c.citizen_id = u.user_id
            LEFT JOIN staff s ON c.assigned_staff_id = s.staff_id
            LEFT JOIN users su ON s.user_id = su.user_id
            WHERE c.complaint_code = %s
        """, (code,))
        complaint = cur.fetchone()

        if not complaint:
            cur.close()
            flash(f"No complaint found matching ID '{code}'. Please check and try again.", "danger")
            return redirect(url_for('home'))

        # Fetch status audit history
        cur.execute("""
            SELECT sh.*, u.name AS changed_by_name, u.role AS changed_by_role
            FROM status_history sh
            JOIN users u ON sh.changed_by_user_id = u.user_id
            WHERE sh.complaint_id = %s
            ORDER BY sh.changed_at ASC
        """, (complaint['complaint_id'],))
        history = cur.fetchall()

        # Fetch feedback if exists
        cur.execute("SELECT * FROM feedback WHERE complaint_id = %s", (complaint['complaint_id'],))
        feedback = cur.fetchone()

        # Fetch all available staff for the admin triage panel
        all_staff = []
        if session.get('role') == 'admin':
            cur.execute("""
                SELECT s.staff_id, s.employee_code, s.assigned_zone, u.name
                FROM staff s
                JOIN users u ON s.user_id = u.user_id
                ORDER BY u.name ASC
            """)
            all_staff = cur.fetchall()

        cur.close()
        return render_template(
            "track_detail.html",
            complaint=complaint,
            history=history,
            feedback=feedback,
            all_staff=all_staff
        )

    except Exception as e:
        flash_error("Error retrieving complaint", e)
        return redirect(url_for('home'))
    finally:
        if conn and conn.is_connected():
            conn.close()


# -----------------------------------------------------------------------------
# Notification Center
# -----------------------------------------------------------------------------
@app.route("/notifications")
@login_required
def notifications():
    """User notifications center."""
    conn = None
    try:
        conn, cur = get_db_cursor()
        cur.execute("""
            SELECT n.*, c.complaint_code 
            FROM notifications n
            LEFT JOIN complaints c ON n.complaint_id = c.complaint_id
            WHERE n.user_id = %s
            ORDER BY n.created_at DESC
        """, (session['user_id'],))
        notifs = cur.fetchall()

        # Mark all as read
        cur.execute("UPDATE notifications SET is_read = TRUE WHERE user_id = %s", (session['user_id'],))
        conn.commit()
        cur.close()
        return render_template("notifications.html", notifications=notifs)
    except Exception as e:
        flash_error("Failed to load notifications", e)
        return redirect(url_for('home'))
    finally:
        if conn and conn.is_connected():
            conn.close()


@app.route("/db-test")
@role_required('admin')
def database_test():
    """Database connectivity check (administrators only)."""
    connection = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT DATABASE()")
        result = cursor.fetchone()
        cursor.close()
        return jsonify({
            "status": "success",
            "database": result[0],
            "engine": "sqlite" if connection.__class__.__name__ == "SQLiteConnectionWrapper" else "mysql"
        })
    except Exception as e:
        logger.error("DB test failed: %s", e, exc_info=True)
        return jsonify({"status": "error", "message": "Database connection failed."}), 500
    finally:
        if connection and connection.is_connected():
            connection.close()

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
        use_reloader=True
    )