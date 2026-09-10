#!/usr/bin/env python3
"""Detect and optionally remove users with malformed bcrypt hashes from local SQLite DB.

Usage:
  python scripts/clean_bad_users.py        # list suspects (no change)
  python scripts/clean_bad_users.py --delete   # backup DB and delete suspects

This script only operates on a local SQLite DB (`sqlite:///./storefinder.db`).
It will refuse to run against a non-sqlite `DATABASE_URL` to avoid accidental
deletion in production Postgres.
"""
import os
import shutil
import sqlite3
import sys


def get_sqlite_path():
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        if not db_url.startswith("sqlite:"):
            print("DATABASE_URL is set and not sqlite. Aborting to avoid touching remote DB.")
            sys.exit(2)
        # sqlite:///./storefinder.db or sqlite:///C:/path
        path = db_url.replace("sqlite:///", "")
        return path
    return "storefinder.db"


def list_bad_users(conn):
    cur = conn.cursor()
    cur.execute("SELECT id, email, password_hash FROM users WHERE password_hash NOT LIKE '$2%'")
    return cur.fetchall()


def main():
    delete = "--delete" in sys.argv
    sqlite_path = get_sqlite_path()
    if not os.path.exists(sqlite_path):
        print(f"SQLite DB not found at {sqlite_path}")
        sys.exit(1)

    print(f"Using SQLite DB: {sqlite_path}")
    conn = sqlite3.connect(sqlite_path)
    try:
        bad = list_bad_users(conn)
        if not bad:
            print("No malformed password hashes found.")
            return
        print("Found the following suspect users (will be deleted if --delete provided):")
        for row in bad:
            print(f" - id={row[0]} email={row[1]} hash={row[2]!r}")

        if delete:
            backup = f"{sqlite_path}.bak"
            print(f"Backing up DB to {backup}...")
            shutil.copy2(sqlite_path, backup)
            cur = conn.cursor()
            cur.execute("DELETE FROM users WHERE password_hash NOT LIKE '$2%'")
            deleted = cur.rowcount
            conn.commit()
            print(f"Deleted {deleted} user(s). Backup at {backup}")
        else:
            print("Run with --delete to remove these rows (a backup will be created).")
    finally:
        conn.close()


if __name__ == '__main__':
    main()
