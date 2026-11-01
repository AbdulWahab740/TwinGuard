import logging
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRET = "change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "prod"
    supabase_url: str = ""
    supabase_key: str = ""
    jwt_secret: str = DEFAULT_SECRET
    seed_password: str = ""  # only used by scripts/seed.py
    llm_provider: Literal["fake", "openai_compatible"] = "fake"
    allow_auto_apply: bool = False
    agent_timeout_s: float = 60
    cors_origins: list[str] = ["http://localhost:5173"]
    eval_results_dir: str = "results"
    scenarios_dir: str = str(Path(__file__).resolve().parents[2] / "contracts" / "scenarios")
    demo_dataset: Literal["demo_baseline", "demo_stale"] = "demo_stale"


def check_secret(s: Settings) -> None:
    if s.env != "dev" and (s.jwt_secret == DEFAULT_SECRET or len(s.jwt_secret) < 32):
        raise RuntimeError("set JWT_SECRET to 32+ chars (weak secrets only allowed with ENV=dev)")
