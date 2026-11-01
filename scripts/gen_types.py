"""Regenerate frontend TypeScript types from contracts/openapi.yaml (needs Node/npx)."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
out = ROOT / "frontend" / "src" / "types" / "api.d.ts"
out.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(
    ["npx", "--yes", "openapi-typescript@7", "contracts/openapi.yaml", "-o", str(out)],
    cwd=ROOT, check=True, shell=(os.name == "nt"),
)
