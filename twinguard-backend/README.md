# TwinGuard backend

Windows (PowerShell), from the repo root:

    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -e .\twinguard-core[test,llm]
    pip install -r .\twinguard-backend\requirements.txt
    cd twinguard-backend
    copy ..\.env.example .env   # fill in SUPABASE_URL / SUPABASE_KEY / JWT_SECRET / SEED_PASSWORD
    # run sql\schema.sql once in the Supabase SQL editor
    python scripts\seed.py       # needs SEED_PASSWORD in .env; seeds demo data + 3 users
    uvicorn app.asgi:app --reload

Docs: http://localhost:8000/docs  Tests: `pytest`

Evaluation page data: `copy ..\twinguard-core\eval_results_*.json results\` (not committed; 2 MB each).
Contracts (OpenAPI, schemas, examples, scenarios) live in `..\contracts`; see the root README.
Docker (from the repo root, after filling `.env` and applying `sql/schema.sql`): `docker compose up --build`.
Offline demo: `LLM_PROVIDER=fake` (default) uses a scripted agent, no network.
