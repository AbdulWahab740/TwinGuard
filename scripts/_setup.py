"""Import first: puts the backend on sys.path (twinguard_core comes from the venv install)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "contracts"
sys.path.insert(0, str(ROOT / "twinguard-backend"))
