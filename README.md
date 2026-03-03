# Fitted

An AI-powered outfit suggestion app. Given a user's location, it fetches real-time weather data and generates personalized outfit recommendations based on stored style preferences.

## Stack

| Layer | Technology |
|-------|-----------|
| Backend API | FastAPI (Python), deployed on EC2 + AWS Lambda |
| Frontend | FastHTML + HTMX, deployed on EC2 |
| Database | PostgreSQL (AWS RDS) with pgvector extension |
| Embeddings | CLIP ViT-B/32 (512-dim, L2-normalized) via `embedding_service.py` |
| Vector search | pgvector HNSW index on `catalog_items` and `query_cache` |
| LLM | OpenRouter → `google/gemini-3-flash-preview` |
| Weather | WeatherAPI.com, cached in S3 |
| Storage | AWS S3 |
| Infrastructure | AWS SAM (Lambda), EC2 + Caddy reverse proxy, AWS SSM |
| CI/CD | GitHub Actions — runs tests on EC2 before deploying |

## Architecture

Two independently deployed Python services, both on EC2 (and optionally Lambda):

- **Backend** (`app/`) — FastAPI REST API on port 8000
- **Frontend** (`frontend/`) — FastHTML app on port 5001; calls the backend at `API_BASE_URL`

### Recommendation Pipeline

```
generate_search_query (LLM)
    → encode_text (CLIP)
    → vector_cache.lookup
        HIT  → return cached Item list
        MISS → dev_catalog_service.search (pgvector ANN)
             → vector_cache.store
    → RecommendationService.rank (UserTower + ItemTower cosine similarity)
    → generate_explanation (LLM, optional)
```

## Setup

```bash
uv venv && source .venv/bin/activate
uv sync
```

## Running Tests

```bash
./run_tests.sh
# or
PYTHONPATH=. pytest tests/ -v
```

## Deployment

CI/CD runs automatically on push to `dev` (staging) or `main` (production):
1. Tests run on the EC2 instance via AWS SSM
2. If tests pass, `scripts/deploy.sh` is invoked on EC2 to pull the branch, update dependencies, and restart services

To deploy manually from EC2:

```bash
./scripts/deploy.sh
```

## Database Migrations

```bash
# Run manually — never automated
python scripts/db_migrate.py
```

## Configuration

- **Production**: secrets fetched from AWS SSM Parameter Store (`/fitted/<key>`)
- **Local**: falls back to environment variables
- `DEV_MODE=true` bypasses JWT authentication for local testing

Key env vars: `DATABASE_URL`, `JWT_SECRET_KEY`, `WEATHER_API_KEY`, `OPENROUTER_API_KEY`, `DEV_MODE`
