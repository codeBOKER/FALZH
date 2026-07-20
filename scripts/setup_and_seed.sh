#!/usr/bin/env bash
set -euo pipefail

# Usage: ./scripts/setup_and_seed.sh
# Seeds FALZH info and syncs active trips into Supabase vector tables.
# Ensure you have activated your virtualenv and filled .env before running.

python scripts/seed_info.py
python scripts/sync_trips.py

echo "Done: Supabase vector info seeded and trips synced." 
