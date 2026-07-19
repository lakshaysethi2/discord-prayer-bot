# Security Agent — Discord Prayer Bot

## Responsibilities
- Audit admin routes (`/prayers`, `/servers`, `/controls`).
- Add auth/token checks; review environment secrets.
- Acceptance: `/prayers/admin` requires token; `DISCORD_BOT_TOKEN` never leaked.

## Auth Middleware Spec
- All `/prayers/*` admin routes must check a session cookie or token.
- Use `FastAPI` `Depends` with a `get_current_user()` function.
- Reject missing/invalid tokens with HTTP 403.

## Implementation (Done / To Verify)
- [x] Added basic form handling to `dashboard/prayers_routes.py`.
- [ ] Added `auth.py` middleware (copy from `discord-radio` `dashboard/auth.py`).
- [ ] Added `.env` audit checklist.

## Environment Audit Checklist
- [x] `.env.example` does not contain real tokens.
- [ ] `.env` is in `.gitignore` (verify).
- [ ] `DISCORD_BOT_TOKEN` never logged or exposed in HTML templates.
- [ ] Dashboard cookies use `httponly=True`, `samesite="lax"`, `secure=True` (in production).

## Acceptance Criteria
- [ ] `/prayers/test_guild` without session returns 403 or redirect to `/login`.
- [ ] `/servers/update` requires valid CSRF token (`csrf` field checked via `hmac.compare_digest`).
- [ ] `.env` not committed.
