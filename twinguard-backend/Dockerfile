# Build from the repo root: docker compose -f twinguard-backend/docker-compose.yml up --build
FROM python:3.12-slim
WORKDIR /srv
COPY twinguard-core ./twinguard-core
COPY twinguard-backend ./twinguard-backend
RUN pip install --no-cache-dir "./twinguard-core[llm]" -r twinguard-backend/requirements.txt
WORKDIR /srv/twinguard-backend
EXPOSE 8000
CMD ["uvicorn", "app.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
