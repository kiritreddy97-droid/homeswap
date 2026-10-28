# HomeSwap India: Product Requirements (MVP)

## MVP scope
- Identity (KYC) + property verification
- Home listings with calendar and house rules
- Points ledger (append-only) with listing bonus
- Swap request, host approval, cancellation with refund
- Reviews after a completed stay (implemented): guest and host each review once; ratings 1-5
- Damage claims workflow (implemented as a record): host files within 14 days of checkout, an admin approves or rejects; no automatic settlement
- Login by phone/email OTP; roles member and admin

## Personas
- Leisure travellers (25-45) with a home in a metro or tourist city
- Remote workers who do month-long stays
- Families doing school-holiday trips
- Property owners with a second home that sits idle

## Non-goals for MVP
- Native mobile apps (web first), advanced analytics, internationalisation.

## Acceptance
- Every rule in `backend/app/services.py` has tests in `backend/tests/`.
- `/health` responds and the API deploys from the Dockerfile.
- Pricing and legal pages are reviewed against `05-legal-compliance.md` before real users are onboarded.

## API
Run the backend and open `/docs` for the generated OpenAPI documentation.
