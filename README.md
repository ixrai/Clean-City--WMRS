# CleanCity – Waste Management Reporting & Resolution System

A Flask web application where citizens report waste problems (photo + GPS), administrators
triage and dispatch sanitation staff, and staff close each ticket with after-cleaning photo proof.

## Features
- **Citizen:** register/login, report waste with photo and map pin, track status by tracking code, notifications, rate resolved work.
- **Staff:** personal work-order list, start work, upload after-photo to resolve (only for tickets assigned to them).
- **Admin:** dashboard, filterable complaint queue, staff dispatch, hotspot map (Leaflet), staff registration, downloadable PDF audit report.
- Full status history (audit trail) and in-app notifications on every change.
- Optional Firebase Authentication for citizens; works fully without it.

## Tech stack
Python 3 · Flask · SQLite (default) or MySQL · Jinja2 + Bootstrap 5 · Leaflet · Chart.js · ReportLab · Firebase Auth (optional)

## Quick start
```bash
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py                       # open http://127.0.0.1:5000
```
The project ships with a ready SQLite database containing demo data. To rebuild it at any time:
```bash
python database/init_db.py --reset --demo
```

## Demo accounts
| Role | Email | Password |
|---|---|---|
| Admin | admin@wastemanage.gov | Admin@123 |
| Staff | staff@wastemanage.gov | Staff@123 |
| Staff | sunita.devi@wastemanage.gov | Staff@123 |
| Citizen | soman@gmail.com | Citizen@123 |

Demo data: 12 complaints across every status (Pending, Assigned, In Progress, Resolved, Rejected), feedback, history and notifications.
Change these passwords and set `DEMO_MODE=false` in `.env` before any real deployment.

## Configuration (`.env`, see `.env.example`)
| Variable | Meaning |
|---|---|
| `DB_TYPE` | `sqlite` (default) · `mysql` · `auto` (try MySQL, else SQLite) |
| `SECRET_KEY` | Flask session secret (auto-generated if empty) |
| `FLASK_DEBUG` | `false` by default – never enable on a public server |
| `DEMO_MODE` | Show one-click demo login buttons |
| `FIREBASE_*` | Firebase web config; leave `FIREBASE_API_KEY` empty to disable Firebase |

**Using MySQL:** `pip install mysql-connector-python`, create the database with `database/schema.sql`
(or run `python database/init_db.py`), then set `DB_TYPE=mysql` and the `DB_*` values.

## Tests
```bash
python -m unittest discover -s tests -v
```
34 tests (isolated temp database, fake Firebase – no internet or real accounts needed) covering
authentication, role permissions, the full complaint lifecycle, input/upload validation, CSRF,
brute-force lock-out, PDF export, and demo-data seeding.

## Project structure
```
app.py                 Flask routes, auth, security helpers
config.py              Settings loaded from .env
db.py                  MySQL/SQLite abstraction layer
firebase_service.py    Firebase REST helpers (token verification, sign-in/up)
report_generator.py    PDF audit report (ReportLab)
database/              schema.sql (MySQL), schema_sqlite.sql, init_db.py, seed_demo.py
templates/  static/    Jinja pages, CSS/JS, uploaded photos
tests/                 Automated test-suite
```

## Security notes
- Passwords are hashed with Werkzeug (salted, scrypt/PBKDF2) – never stored in plain text.
- Role-based access on every protected route; staff can only act on tickets assigned to them.
- CSRF tokens on all forms and API calls; secure session cookie flags; basic security headers.
- Login lock-out after 5 failed attempts (15 min). Open-redirect protection on `?next=`.
- Uploads are checked by real image signature (JPG/PNG/GIF/WEBP), renamed server-side, max 16 MB.
- Firebase sign-in can only open **citizen** accounts; admin/staff always need their own password.
- Known limitation: e-mail ownership is not verified at citizen sign-up (typical for a college project).
