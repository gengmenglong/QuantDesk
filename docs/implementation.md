# QuantDesk implementation record

Accepted scope: local-only React/TypeScript + FastAPI/worker + PostgreSQL; real read-only data for US equities, mainland daily equities, crypto spot and binary prediction markets; internal paper execution only. Original prototype remains a visual reference.

## Tasks and acceptance
1. Foundation and contracts: migration, decimal storage, configuration encryption, localhost sessions, OpenAPI.
2. Transactional core: account initialization, reservations, risk, fill deduplication, market rules, ledger, global pause and recovery.
3. Data and AI: four read-only adapters, normalized quality/time, durable jobs, validated decisions and budgets.
4. Strategy lifecycle: constrained specs, historical/holdout evaluation, shadow observations, promotion and rollback.
5. UI: seven real API views, setup, event reconnection, trace, export and errors.
6. Delivery: Docker, CI, tests, documentation, independent review.

## Release gates
Real credentials and network permissions must be verified on the user's machine. Five stock trading days of observed running are required before declaring v1 released. Missing data never falls back to demo data. Automated fixture tests are not live-provider certification.

## Decisions
- Work in a dedicated branch in the supplied clean checkout; retain the user's primary project location.
- One globally locked runtime row serializes monetary mutations, settings and pause. Appropriate for the small personal workload; can be replaced by ordered account locks later.
- No paid provider purchase or credentials inferred. Setup remains paused until the user supplies capital, risk, FX and model limits.
