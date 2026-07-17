#!/usr/bin/env python3
"""Run the driver_message_card migration against Supabase.

Usage:
    source .venv/bin/activate
    python scripts/run_migration.py

Requires DATABASE_URL in .env or exports it.
The DATABASE_URL should be the direct connection string from:
    Supabase Dashboard → Settings → Database → Connection string → URI (Session mode)
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

try:
    import psycopg
except ImportError:
    sys.exit("psycopg not installed. Run: pip install 'psycopg[binary]'")

MIGRATION_SQL = (Path("supabase/migrations/202607140001_driver_message_card.sql")).read_text()


def main():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        sys.exit(
            "DATABASE_URL not set. Add it to .env or export it.\n"
            "Get it from: Supabase Dashboard → Settings → Database → Connection string → URI"
        )

    print(f"Connecting to database...")
    conn = psycopg.connect(db_url, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(MIGRATION_SQL)
            conn.commit()
        print("Migration applied successfully!")
    except Exception as e:
        conn.rollback()
        sys.exit(f"Migration failed: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
