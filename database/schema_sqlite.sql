-- ============================================================
-- CleanCity Waste Management System
-- SQLite Schema (Resilient local database fallback)
-- ============================================================

CREATE TABLE IF NOT EXISTS departments (
    department_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    phone TEXT,
    role TEXT NOT NULL DEFAULT 'citizen' CHECK (role IN ('citizen','admin','staff')),
    firebase_uid TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS categories (
    category_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    default_priority TEXT NOT NULL DEFAULT 'Medium' CHECK (default_priority IN ('Low','Medium','High')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS staff (
    staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE,
    department_id INTEGER NULL,
    employee_code TEXT UNIQUE NOT NULL,
    designation TEXT DEFAULT 'Sanitation Officer',
    assigned_zone TEXT DEFAULT 'Zone A',
    status TEXT NOT NULL DEFAULT 'available' CHECK (status IN ('available','busy','on_leave')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (department_id) REFERENCES departments(department_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS complaints (
    complaint_id INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_code TEXT UNIQUE NOT NULL,
    citizen_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    assigned_staff_id INTEGER NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    address TEXT NOT NULL,
    latitude REAL NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude REAL NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    before_image TEXT NULL,
    after_image TEXT NULL,
    status TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending','Assigned','In Progress','Resolved','Rejected')),
    priority TEXT NOT NULL DEFAULT 'Medium' CHECK (priority IN ('Low','Medium','High')),
    rejection_reason TEXT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP NULL,
    FOREIGN KEY (citizen_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(category_id) ON DELETE RESTRICT,
    FOREIGN KEY (assigned_staff_id) REFERENCES staff(staff_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS status_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id INTEGER NOT NULL,
    changed_by_user_id INTEGER NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    remarks TEXT,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (complaint_id) REFERENCES complaints(complaint_id) ON DELETE CASCADE,
    FOREIGN KEY (changed_by_user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    complaint_id INTEGER NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    is_read INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (complaint_id) REFERENCES complaints(complaint_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id INTEGER NOT NULL UNIQUE,
    citizen_id INTEGER NOT NULL,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comments TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (complaint_id) REFERENCES complaints(complaint_id) ON DELETE CASCADE,
    FOREIGN KEY (citizen_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- Indexes for the most common lookups
CREATE INDEX IF NOT EXISTS idx_complaints_status   ON complaints(status);
CREATE INDEX IF NOT EXISTS idx_complaints_citizen  ON complaints(citizen_id);
CREATE INDEX IF NOT EXISTS idx_complaints_staff    ON complaints(assigned_staff_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user  ON notifications(user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_history_complaint   ON status_history(complaint_id);

-- Seed departments
INSERT OR IGNORE INTO departments (department_id, name, description) VALUES
(1, 'Solid Waste Management', 'Responsible for residential and commercial solid waste collection and disposal.'),
(2, 'Sanitation & Public Health', 'Oversees general cleanliness, dustbin clearing, and health hazard mitigation.'),
(3, 'Hazardous & Special Waste', 'Manages medical, industrial, and toxic waste safely.'),
(4, 'Drainage & Waterway Cleaning', 'Handles open drains, clogged culverts, and stagnant water bodies.');

-- Seed categories
INSERT OR IGNORE INTO categories (category_id, name, description, default_priority) VALUES
(1, 'Overflowing Public Dustbin', 'Dustbins spilling over into public streets or parks.', 'Medium'),
(2, 'Illegal Roadside Waste Dump', 'Unauthorized open dumping of mixed waste on roads or vacant plots.', 'High'),
(3, 'Dead Animal / Biohazard', 'Animal carcasses or organic decomposing matter requiring urgent removal.', 'High'),
(4, 'Hazardous & E-Waste', 'Discarded batteries, electronics, chemicals, or toxic materials.', 'High'),
(5, 'Construction & Demolition Debris', 'Bricks, concrete rubble, and debris obstructing public pathways.', 'Low'),
(6, 'Clogged Drain with Garbage', 'Plastic and garbage causing stormwater drains to block and overflow.', 'Medium');

