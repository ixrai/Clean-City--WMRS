-- ============================================================
-- Waste Management Reporting System
-- Database Schema for MySQL
-- Database: waste_management
-- ============================================================

CREATE DATABASE IF NOT EXISTS waste_management;
USE waste_management;

-- 1. Departments Table
CREATE TABLE IF NOT EXISTS departments (
    department_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- 2. Users Table
CREATE TABLE IF NOT EXISTS users (
    user_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(150) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    phone VARCHAR(15),
    role ENUM('citizen', 'admin', 'staff') DEFAULT 'citizen',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- If users table already existed with ENUM('citizen', 'admin'), update enum to include 'staff'
ALTER TABLE users MODIFY COLUMN role ENUM('citizen', 'admin', 'staff') DEFAULT 'citizen';

-- 3. Categories Table
CREATE TABLE IF NOT EXISTS categories (
    category_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    description TEXT,
    default_priority ENUM('Low', 'Medium', 'High') DEFAULT 'Medium',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- 4. Cleaning Staff Table
CREATE TABLE IF NOT EXISTS staff (
    staff_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL UNIQUE,
    department_id INT NULL,
    employee_code VARCHAR(50) UNIQUE NOT NULL,
    designation VARCHAR(100) DEFAULT 'Sanitation Officer',
    assigned_zone VARCHAR(100) DEFAULT 'Zone A',
    status ENUM('available', 'busy', 'on_leave') DEFAULT 'available',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_staff_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    CONSTRAINT fk_staff_dept FOREIGN KEY (department_id) REFERENCES departments(department_id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- 5. Complaints Table
CREATE TABLE IF NOT EXISTS complaints (
    complaint_id INT AUTO_INCREMENT PRIMARY KEY,
    complaint_code VARCHAR(30) UNIQUE NOT NULL,
    citizen_id INT NOT NULL,
    category_id INT NOT NULL,
    assigned_staff_id INT NULL,
    title VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    address VARCHAR(255) NOT NULL,
    latitude DECIMAL(10, 8) NOT NULL,
    longitude DECIMAL(11, 8) NOT NULL,
    before_image VARCHAR(255) NULL,
    after_image VARCHAR(255) NULL,
    status ENUM('Pending', 'Assigned', 'In Progress', 'Resolved', 'Rejected') DEFAULT 'Pending',
    priority ENUM('Low', 'Medium', 'High') DEFAULT 'Medium',
    rejection_reason TEXT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP NULL,
    CONSTRAINT fk_complaint_citizen FOREIGN KEY (citizen_id) REFERENCES users(user_id) ON DELETE CASCADE,
    CONSTRAINT fk_complaint_category FOREIGN KEY (category_id) REFERENCES categories(category_id) ON DELETE RESTRICT,
    CONSTRAINT fk_complaint_staff FOREIGN KEY (assigned_staff_id) REFERENCES staff(staff_id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- 6. Status History Table (Audit Trail)
CREATE TABLE IF NOT EXISTS status_history (
    history_id INT AUTO_INCREMENT PRIMARY KEY,
    complaint_id INT NOT NULL,
    changed_by_user_id INT NOT NULL,
    old_status VARCHAR(50),
    new_status VARCHAR(50) NOT NULL,
    remarks TEXT,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_history_complaint FOREIGN KEY (complaint_id) REFERENCES complaints(complaint_id) ON DELETE CASCADE,
    CONSTRAINT fk_history_user FOREIGN KEY (changed_by_user_id) REFERENCES users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 7. Notifications Table
CREATE TABLE IF NOT EXISTS notifications (
    notification_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    complaint_id INT NULL,
    title VARCHAR(150) NOT NULL,
    message TEXT NOT NULL,
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_notif_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    CONSTRAINT fk_notif_complaint FOREIGN KEY (complaint_id) REFERENCES complaints(complaint_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 8. Feedback Table
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id INT AUTO_INCREMENT PRIMARY KEY,
    complaint_id INT NOT NULL UNIQUE,
    citizen_id INT NOT NULL,
    rating INT NOT NULL CHECK (rating >= 1 AND rating <= 5),
    comments TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_feedback_complaint FOREIGN KEY (complaint_id) REFERENCES complaints(complaint_id) ON DELETE CASCADE,
    CONSTRAINT fk_feedback_citizen FOREIGN KEY (citizen_id) REFERENCES users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ============================================================
-- Seed Initial Data (Departments, Categories, Admin & Staff)
-- ============================================================

INSERT IGNORE INTO departments (department_id, name, description) VALUES
(1, 'Solid Waste Management', 'Responsible for residential and commercial solid waste collection and disposal.'),
(2, 'Sanitation & Public Health', 'Oversees general cleanliness, dustbin clearing, and health hazard mitigation.'),
(3, 'Hazardous & Special Waste', 'Manages medical, industrial, and toxic waste safely.'),
(4, 'Drainage & Waterway Cleaning', 'Handles open drains, clogged culverts, and stagnant water bodies.');

INSERT IGNORE INTO categories (category_id, name, description, default_priority) VALUES
(1, 'Overflowing Public Dustbin', 'Dustbins spilling over into public streets or parks.', 'Medium'),
(2, 'Illegal Roadside Waste Dump', 'Unauthorized open dumping of mixed waste on roads or vacant plots.', 'High'),
(3, 'Dead Animal / Biohazard', 'Animal carcasses or organic decomposing matter requiring urgent removal.', 'High'),
(4, 'Hazardous & E-Waste', 'Discarded batteries, electronics, chemicals, or toxic materials.', 'High'),
(5, 'Construction & Demolition Debris', 'Bricks, concrete rubble, and debris obstructing public pathways.', 'Low'),
(6, 'Clogged Drain with Garbage', 'Plastic and garbage causing stormwater drains to block and overflow.', 'Medium');
