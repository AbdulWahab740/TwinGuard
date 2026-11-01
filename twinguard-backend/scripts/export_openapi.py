"""Regenerate openapi.json (the contract with the frontend). Run from twinguard-backend/."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import Settings  # noqa: E402
from app.db import MemoryDb  # noqa: E402
from app.main import create_app  # noqa: E402

spec = create_app(Settings(env="dev"), MemoryDb()).openapi()
with open(os.path.join(os.path.dirname(__file__), "..", "openapi.json"), "w") as f:
    json.dump(spec, f, indent=2)
