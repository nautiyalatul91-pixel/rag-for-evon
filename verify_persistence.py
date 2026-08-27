import sqlite3
import os
from pathlib import Path

# Resolve DB path
ROOT_DIR = Path(__file__).resolve().parent
DB_PATH = os.getenv("SQLITE_DB_PATH", "data/metadata.db")
ABS_DB_PATH = ROOT_DIR / DB_PATH

print(f"Target Database Path: {ABS_DB_PATH.resolve()}")

# Ensure data directory exists
ABS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Connect to database
conn = sqlite3.connect(str(ABS_DB_PATH))
cursor = conn.cursor()

# Ensure users table exists (mirroring db_service schema)
cursor.execute(
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        hashed_password TEXT NOT NULL,
        role TEXT NOT NULL,
        created_at TEXT
    )
    """
)
conn.commit()

# Check if test user exists
cursor.execute("SELECT id, username, role FROM users WHERE username = 'persistence_test_user'")
user = cursor.fetchone()

if user:
    print("\n[VERIFICATION RESULT] SUCCESS!")
    print(f"Test user already exists: ID={user[0]}, Username='{user[1]}', Role='{user[2]}'")
    print("This confirms that the SQLite database has successfully PERSISTED across redeployments/restarts!")
else:
    # Insert test user
    cursor.execute(
        """
        INSERT INTO users (username, hashed_password, role, created_at)
        VALUES ('persistence_test_user', 'dummy_hash_not_for_login', 'admin', '2026-08-28T03:00:00Z')
        """
    )
    conn.commit()
    print("\n[VERIFICATION RESULT] SEEDED!")
    print("Successfully inserted new user 'persistence_test_user' into the SQLite database.")
    print("ACTION REQUIRED:")
    print("  1. Trigger a redeployment or restart of this service on Railway.")
    print("  2. Run this script again: 'python verify_persistence.py'")
    print("  3. If it outputs 'SUCCESS', your persistent volume is correctly configured and working!")

# Print all users in DB for inspection
cursor.execute("SELECT id, username, role FROM users")
all_users = cursor.fetchall()
print("\n--- Current Users in Database ---")
for u in all_users:
    print(f"  - ID: {u[0]} | Username: {u[1]} | Role: {u[2]}")
print("-" * 33)

conn.close()
