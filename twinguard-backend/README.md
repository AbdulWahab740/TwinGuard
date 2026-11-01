# TwinGuard backend

Windows (PowerShell), from the repo root:

    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -e .\twinguard-core[test,llm]
    pip install -r .\twinguard-backend\requirements.txt
    cd twinguard-backend
    copy .env.example .env      # fill in SUPABASE_URL / SUPABASE_KEY
    # run sql\schema.sql once in the Supabase SQL editor
    python scripts\seed.py       # needs SEED_PASSWORD in .env; seeds demo data + 3 users
    uvicorn app.asgi:app --reload

Docs: http://localhost:8000/docs  Tests: `pytest`

Evaluation page data: `copy ..	winguard-core\eval_results_*.json results\` (not committed; 2 MB each).
Scenario pack: `python scripts\make_scenarios.py`. Contract: `python scripts\export_openapi.py` rewrites `openapi.json`.
Docker (from this folder, after filling `.env` and applying `sql/schema.sql`): `docker compose up --build`.
Offline demo: `LLM_PROVIDER=fake` (default) uses a scripted agent, no network.
