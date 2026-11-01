# TwinGuard backend

Windows (PowerShell), from the repo root:

    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -e .\twinguard-core[test,llm]
    pip install -r .\twinguard-backend\requirements.txt
    cd twinguard-backend
    copy .env.example .env      # fill in SUPABASE_URL / SUPABASE_KEY
    # run sql\schema.sql once in the Supabase SQL editor
    uvicorn app.main:app --reload

Docs: http://localhost:8000/docs  Tests: `pytest`
