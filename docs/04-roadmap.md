# HomeSwap India: Roadmap

## Phase 0 - Validate (weeks 1-4)
- 50 interviews; landing page + waitlist; pick 3 cities
- Legal opinion on housing-society and short-stay rules

## Phase 1 - Closed beta (weeks 5-12)
- KYC + property verification
- Listings, points ledger, swap flow (this repo's backend)
- Done: OTP login + JWT, roles, admin/webhook-only verification, Alembic migrations, rate limiting, structured logs
- Done: simple web client at `/app/` (login, browse, stays, reviews, claims, admin)
- Remaining before beta: real OTP and KYC providers, Render deploy (see `03-architecture.md`)
- Manual concierge for the first 100 swaps

## Phase 2 - Launch (months 4-6)
- Mobile app, payments
- Done early in the backend: reviews and damage-claim records; claim settlement still needs a legal/insurance decision
- Open to 10 cities
- Paid membership

## Phase 3 - Scale (months 7-12)
- Group/long-stay swaps, partner perks
- Fraud scoring, dispute tooling

Timings are planning estimates for a small team; re-plan at the end of each phase.
