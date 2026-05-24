-- =============================================================================
-- SaaS Video Affiliate AI Generator — Database Schema DDL
-- Target: 10,000 Users (Mobile-First) on Low-RAM VPS (aaPanel)
-- Optimized for high performance, small index size, and stateless force logout.
-- =============================================================================

-- =============================================================================
-- SECTION 1: POSTGRESQL DDL
-- =============================================================================

/*
-- Jalankan skrip ini jika menggunakan database PostgreSQL:

-- 1. Buat fungsi trigger untuk memperbarui otomatis kolom `updated_at`
CREATE OR REPLACE FUNCTION update_timestamp_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- 2. Buat tabel `users`
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    hashed_pw VARCHAR(255) NOT NULL,
    full_name VARCHAR(100),
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    is_verified BOOLEAN DEFAULT FALSE NOT NULL,
    token_version INTEGER DEFAULT 0 NOT NULL,
    daily_quota INTEGER DEFAULT 5 NOT NULL,
    quota_used INTEGER DEFAULT 0 NOT NULL,
    quota_reset TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
);

-- 3. Optimasi Index B-Tree untuk pencarian cepat via Email (Registrasi & Login)
-- Ukuran VARCHAR(255) membatasi ukuran index di RAM agar tidak membebani VPS.
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- 4. Pasang trigger untuk otomatisasi update `updated_at`
DROP TRIGGER IF EXISTS trigger_update_users_timestamp ON users;
CREATE TRIGGER trigger_update_users_timestamp
BEFORE UPDATE ON users
FOR EACH ROW
EXECUTE PROCEDURE update_timestamp_column();

*/


-- =============================================================================
-- SECTION 2: MYSQL DDL
-- =============================================================================

-- Jalankan skrip ini jika menggunakan database MySQL/MariaDB (Sangat disarankan di aaPanel):

CREATE TABLE IF NOT EXISTS `users` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `email` VARCHAR(255) NOT NULL,
    `hashed_pw` VARCHAR(255) NOT NULL,
    `full_name` VARCHAR(100) NULL,
    `is_active` TINYINT(1) DEFAULT 1 NOT NULL,
    `is_verified` TINYINT(1) DEFAULT 0 NOT NULL,
    `token_version` INT DEFAULT 0 NOT NULL,
    `daily_quota` INT DEFAULT 5 NOT NULL,
    `quota_used` INT DEFAULT 0 NOT NULL,
    `quota_reset` DATETIME NULL,
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
    `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP NOT NULL,
    
    -- Optimasi Index untuk pencarian supercepat dan hemat memori RAM VPS
    UNIQUE KEY `uq_users_email` (`email`),
    INDEX `idx_users_email` (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
