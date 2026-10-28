# HomeSwap India: Architecture

```
 Web / mobile client
        |  HTTPS
   FastAPI service  (backend/app/main.py: routes only)
        |
   services.py      (all business rules; pure functions over a DB session; unit-tested)
        |
   SQLAlchemy models --> PostgreSQL (SQLite for local dev and tests); schema via Alembic
```

## Principles
- Business rules live in `services.py`, not in route handlers, so they are testable without HTTP.
- Time comes from `app/timeutil.py` (timezone-aware UTC), which tests patch to stay deterministic.
- Route handlers translate domain errors to HTTP status codes and commit the transaction.
- Money is stored in integer rupees or paise; never floats.

## Production hardening checklist
Done in code (no external credentials needed):
- [x] **Authentication**: phone/email OTP login, JWT bearer tokens (`app/auth.py`). OTP delivery sits behind the `OtpSender` interface (`app/otp.py`); only a console sender (dev) exists. Codes are stored as keyed hashes, expire after 10 minutes, allow 5 attempts, are single use, and are throttled to 3 requests per 10 minutes per contact.
- [x] **Authorisation**: every route except `/health` and `/auth/*` needs a token. Roles are `member` and `admin`; the role is read from the database on each request, so demoting an admin is immediate. Admins are bootstrapped through `ADMIN_CONTACTS`. Swap actions are limited to participants (approve: host only), and strangers get 404 rather than 403.
- [x] **KYC and verification are not client-callable**: user verification is `POST /admin/users/{id}/verify` (admin) or `POST /kyc/webhook` (HMAC-signed, disabled until `KYC_WEBHOOK_SECRET` is set); home verification is `POST /admin/homes/{id}/verify` (admin).
- [x] **Migrations**: Alembic (`backend/alembic/`), initial migration `0001`. The app never calls `create_all`; the Docker image runs `alembic upgrade head` on start. CI applies migrations on Postgres 16, runs `alembic check` (models and migrations must not drift) and a downgrade/upgrade round trip.
- [x] **Timezone-aware time**: all timestamps are UTC `timestamptz` via `app/timeutil.py`.
- [x] **Rate limiting**: in-process sliding window per client IP (120/min general, 10/min for `/auth/*`, `/health` exempt). Per instance only; use Redis or the host's edge limiter before running several instances.
- [x] **Structured logging**: one JSON log line per request with a request id (`X-Request-ID` is accepted and echoed), unhandled errors logged with a traceback and returned as a plain 500.
- [x] **Reviews** and **damage claims** (phase 2 items), see [02-product-requirements.md](02-product-requirements.md).

Still to do (needs owner decisions or credentials):
- [ ] A real OTP provider (SMS/email) implementing `OtpSender`; `ENV=production` refuses to start with the console sender.
- [ ] A KYC provider and mapping of its webhook payload onto `POST /kyc/webhook`.
- [ ] Payments via a licensed gateway; webhooks verified by signature. Membership is currently activated together with KYC.
- [ ] Settling approved damage claims (deposit, insurance or points) once the legal model is decided; today a claim is a record resolved by an admin and no money or points move.
- [ ] Error tracking (e.g. Sentry), database backups, shared rate-limit store.
- [ ] Secrets in the host's secret manager, never in the repo (`JWT_SECRET`, `KYC_WEBHOOK_SECRET`, `ADMIN_CONTACTS`, provider keys).

## Environments
`local` (SQLite or docker compose Postgres), `staging` and `production` on a cloud host. See [08-cloud-deploy.md](08-cloud-deploy.md).
