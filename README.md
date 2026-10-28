# HomeSwap India

> Swap homes across Indian cities. Stop paying for hotels.

Idea from a creator video: someone in Mumbai wants Goa, someone in Goa wants Mumbai; both pay for hotels. A points system makes swaps flexible; membership funds the platform.

## What is in this repo
| Path | Purpose |
|---|---|
| `docs/` | Business plan, product requirements, architecture, roadmap, legal checklist, go-to-market, financial model, cloud setup |
| `backend/app/` | FastAPI service: `models.py`, `services.py` (business rules), `auth.py` + `otp.py` (OTP login, JWT, roles), `middleware.py` (rate limit, JSON logs), `main.py` (routes) |
| `backend/alembic/` | Database migrations |
| `backend/tests/` | Unit tests for the business rules, end-to-end API tests (incl. auth failures) and a migration drift test |
| `web/index.html` | Draft landing page |
| `web/app/` | Web client (plain HTML/JS/CSS, no build step), served by the API at `/app/` |
| `finance/model.py` | Editable unit-economics model |
| `Dockerfile`, `docker-compose.yml`, `render.yaml` | Container and cloud deployment |
| `.github/workflows/ci.yml` | Lint, test, build on every push |
| `.devcontainer/` | Cloud dev environment (GitHub Codespaces) |

## Quick start
```bash
cd backend
pip install -r requirements-dev.txt
pytest
alembic upgrade head              # creates app.db (SQLite) from the migrations
uvicorn app.main:app --reload     # http://localhost:8000/docs
```
With Postgres: `docker compose up --build` (migrations run on start).

Log in: `POST /auth/otp/request {"identifier": "you@example.com"}`, read the 6-digit code from the server log (console OTP sender, dev only), then `POST /auth/otp/verify` to get a bearer token. Set `ADMIN_CONTACTS=you@example.com` before logging in to become an admin. See `.env.example` for all settings.

Web client: open `http://localhost:8000/app/` (same origin as the API, so no CORS setup). It covers login, browsing homes, requesting stays, listing homes, host approval, completing stays, reviews, damage claims, and an admin screen for verification and claim resolution.

## Status
Backend MVP: OTP login with JWT and roles, admin/webhook-only verification, swaps with a points ledger, reviews, damage-claim records, Alembic migrations, rate limiting and structured logs; CI runs lint, tests and a Postgres migration check.

Not launched yet: needs a real OTP provider, a KYC provider and payments. The checklist with what is done and what the owner must configure is in `docs/03-architecture.md`.

Start reading at `docs/01-business-plan.md`.
