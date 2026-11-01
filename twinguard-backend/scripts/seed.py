"""Seed the Supabase project: demo dataset + users. Run from twinguard-backend/.

Needs SUPABASE_URL, SUPABASE_KEY and SEED_PASSWORD in the environment/.env, and
sql/schema.sql already applied.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import Settings  # noqa: E402
from app.db import SupabaseDb  # noqa: E402
from app.seed import seed_dataset_into, seed_users  # noqa: E402

settings = Settings()
password = os.environ.get("SEED_PASSWORD")
if not password:
    sys.exit("set SEED_PASSWORD")
db = SupabaseDb.from_settings(settings)
seed_dataset_into(db, settings.demo_dataset)
seed_users(db, password)
print("seeded", settings.demo_dataset)
