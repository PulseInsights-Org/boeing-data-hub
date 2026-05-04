# Vendor Alert Audit — boeing-data-hub

> Read-only audit. No code modified. Secrets referenced by env-var name and `file:line` only.

## Integration Inventory

| # | Integration | Credential Env Vars | Code Entry Points |
|---|-------------|--------------------|--------------------|
| 1 | Boeing PNA API | `BOEING_CLIENT_ID`, `BOEING_CLIENT_SECRET`, `BOEING_USERNAME`, `BOEING_PASSWORD`, `BOEING_SCOPE` | `backend/app/clients/boeing_client.py` |
| 2 | Shopify Admin API | `SHOPIFY_ADMIN_API_TOKEN`, `SHOPIFY_STORE_DOMAIN` | `backend/app/clients/shopify_client.py`, `backend/app/services/search_service.py` |
| 3 | Supabase | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_STORAGE_BUCKET` | `backend/app/clients/supabase_client.py` |
| 4 | AWS Cognito | `COGNITO_REGION`, `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID` | `backend/app/core/cognito.py`, `backend/app/services/auth_service.py` |
| 5 | AWS EC2 (deploy) | `EC2_SSH_KEY` (GitHub secret) | `.github/workflows/deploy-backend.yml` |
| 6 | Redis / Celery | `REDIS_URL` | `backend/app/celery_app/celery_config.py`, `backend/app/utils/rate_limiter.py` |
| 7 | Google Gemini | `GEMINI_API_KEY`, `GEMINI_MODEL` | `backend/app/clients/gemini_client.py` |
| 8 | Resend | `RESEND_API_KEY`, `RESEND_FROM_ADDRESS` | `backend/app/clients/resend_client.py` |

## Findings

### CRITICAL

**F-01 — Production secrets committed to public GitHub repository**
- Service: All (Supabase, Shopify, Boeing, Gemini, Resend)
- File: `backend/.env` (working tree); commit `e6b485167d` ("Fix : removed .env") on `feature/sso-integration` proves prior tracking
- Description: The file exists on disk with live production values for `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SHOPIFY_ADMIN_API_TOKEN`, `BOEING_CLIENT_ID`, `BOEING_CLIENT_SECRET`, `BOEING_USERNAME`, `BOEING_PASSWORD`, `RESEND_API_KEY`, `GEMINI_API_KEY`. Although the file was later removed, the secrets are permanently readable from history at that SHA on `github.com/CHIRANTHB2002/boeing-data-hub`. Recovering them is `git show e6b485167d:backend/.env`.
- Likely vendor alert: Supabase exposed-key alert; GitHub secret-scanning email about the `shpat_` Shopify token; Boeing credential-misuse alert; Resend abuse suspension; Google Cloud Gemini key suspension.

**F-02 — Boeing OAuth tokens not cached — re-auth on every batch call**
- Service: Boeing PNA API
- File: `backend/app/clients/boeing_client.py:24-91, 117-152`
- Description: Both `fetch_price_availability()` and `fetch_price_availability_batch()` call `_get_oauth_access_token()` and `_get_part_access_token()` on every invocation. No expiry check, no in-memory cache. Combined with `SYNC_MODE=testing` and `SYNC_DISPATCH_MINUTE=*/10`, the scheduler cycles 6 buckets per hour, each running multiple batches → hundreds of OAuth logins per day on `albert.restrepo@skynet-intl.com`. Boeing's anomaly detection flags this and emails the registered owner.
- Likely vendor alert: Boeing security-alert / account-lockout email — the most likely current driver of the user's vendor-email noise.

**F-03 — CORS `allow_origins=["*"]` with `allow_credentials=True`**
- Service: FastAPI / all consumers
- File: `backend/app/core/middleware.py:15-21`
- Description: Spec-violating combination — browsers normally reject preflight, but the configuration creates an attack surface for non-browser CSRF and any browser implementation that doesn't strictly enforce. Cognito JWTs travel in `Authorization` headers, so XSS on a consumer app could steal and replay them.

**F-04 — Redis rate-limiter fails open on Redis errors**
- Service: Boeing / Redis
- File: `backend/app/utils/rate_limiter.py:156-163`
- Description: `acquire_token()` returns `True, 0, 0` on `redis.RedisError`. If Redis is unavailable (crash / network partition / OOM eviction), every worker bypasses rate limiting at once and pummels Boeing and Shopify with unrestricted concurrency.
- Likely vendor alert: Boeing API-quota exhaustion + account suspension; Shopify 429 + temporary key suspension.

**F-05 — Production EC2 IP hardcoded in workflow file**
- Service: AWS EC2
- File: `.github/workflows/deploy-backend.yml:12` — `EC2_HOST: 98.84.57.169`
- Description: Public IP committed to the repo. Combined with F-01, an attacker has both the address and the credentials.

### HIGH

**F-06 — Multi-part search endpoints require no authentication**
- Service: Shopify Admin API
- Files: `backend/app/routes/search.py:21-28`, `backend/app/routes/legacy.py:62`
- Description: `POST /api/v1/search/multi-part` and the legacy `/api/shopify/multi-part-search` have no `Depends(get_current_user)`. Each call triggers Shopify GraphQL queries that consume the store's API budget; an unauthenticated attacker can burn quota and exfiltrate inventory data.

**F-07 — `SYNC_MODE=testing` and `SYNC_DISPATCH_MINUTE=*/10` active in production**
- Service: Boeing / Resend
- Files: `backend/.env:70-73`, `backend/app/core/config.py:79-88`, `backend/app/celery_app/celery_config.py:75-76`
- Description: Test mode = 6 dispatch cycles per hour × 24 hours ≈ 144 cycles/day vs ~24 in production. Each cycle fires Boeing logins (amplifying F-02) and emits start + completion emails to `STAKEHOLDER_RECIPIENTS` (5 addresses) via Resend.
- Likely vendor alert: Resend complaint-rate / spam alert; Boeing login-volume amplification.

**F-08 — Cognito `client_id` validation is optional**
- Service: AWS Cognito
- File: `backend/app/core/cognito.py:122-147`
- Description: `verify_aud=False` and `if settings.cognito_app_client_id:` guard means a missing env var allows any app client in the same user pool to authenticate. Cross-application token reuse possible.

**F-09 — Deploy workflow disables SSH host-key checking**
- Service: AWS EC2
- File: `.github/workflows/deploy-backend.yml:48, 54`
- Description: Both `rsync` and `ssh` use `-o StrictHostKeyChecking=no`. DNS poisoning or IP hijack would silently deploy to a hostile server and leak the SSH private key.

**F-10 — Recipient list and full Resend response logged at INFO**
- Service: Resend
- File: `backend/app/clients/resend_client.py:45`
- Description: `logger.info(f"Email sent to {to}, response={response}")` emits the full `STAKEHOLDER_RECIPIENTS` list (personal contentease.ai and skynetparts.com addresses) on every send. Pipes PII into any log aggregator.

**F-11 — Boeing error responses forwarded raw to API callers**
- Service: Boeing PNA API
- File: `backend/app/clients/boeing_client.py:46-49, 73-78, 112-113, 149-150`
- Description: Boeing's error body is interpolated into `HTTPException(detail=...)` and propagated to end users. Boeing error payloads can include internal codes and partial credential context (e.g., echoed `client_id`).

**F-12 — Service-role key used for all DB ops; RLS effectively bypassed**
- Service: Supabase
- Files: `backend/app/clients/supabase_client.py:18-36`, `backend/app/core/config.py:31`
- Description: All operations use `SUPABASE_SERVICE_ROLE_KEY`. Application-layer `user_id` filters are the only isolation; any missing-filter bug becomes IDOR. Combined with F-17 (`get_published_product` lacks `user_id`), cross-tenant access is already possible.

**F-13 — `boto3.client('cognito-idp')` instantiated without explicit credentials**
- Service: AWS Cognito
- File: `backend/app/services/auth_service.py:31`
- Description: Falls back to the boto3 chain → EC2 instance role. If the instance profile is overly permissive, the app inherits all of those permissions, far beyond `cognito-idp:GlobalSignOut`.

### MEDIUM

**F-14 — Celery result backend stores sensitive task outputs in Redis**
- Service: Redis / Celery
- Files: `backend/app/celery_app/celery_config.py:188-189`; `backend/.env:52` (`REDIS_URL=redis://localhost:6379/0`)
- Description: Task return values (SKU lists, update counts, embedded report content) are persisted in unauthenticated local Redis. Any process on the EC2 host can read business-sensitive inventory and pricing.

**F-15 — Redis has no password**
- Service: Redis
- Files: `backend/.env:52`, `backend/app/core/config.py:62`
- Description: `redis://localhost:6379/0` with no auth. Any misconfiguration of `bind` or Docker bridge networking exposes Celery state, rate-limiter keys, and dispatch locks to the network. Modifying rate-limiter keys would bypass Boeing/Shopify limits; modifying dispatch locks would silently suppress sync cycles.

**F-16 — Empty-string fallbacks on missing API keys**
- Service: Gemini / Resend
- File: `backend/app/container.py:127, 135`
- Description: `api_key=settings.gemini_api_key or ""` (and same for Resend) silently constructs a misconfigured client. Failure surfaces only at request time as an opaque auth error and accumulates failed-auth requests against the vendor.

**F-17 — `GET /products/published/{product_id}` lacks `user_id` filter (IDOR)**
- Service: Supabase
- File: `backend/app/routes/products.py:148-170` plus service-role-key context (F-12)
- Description: Authenticated, but the Supabase query fetches by `product_id` alone. Any logged-in user who guesses or learns another user's `product_id` retrieves their data.

**F-18 — Production deploy uses `git reset --hard origin/main` on the EC2 host**
- Service: AWS EC2 / supply chain
- File: `.github/workflows/deploy-backend.yml:58-59`
- Description: EC2 must hold GitHub credentials. A compromised push to `main` auto-deploys to prod. `rsync --delete` then `git reset --hard` also leaves a brief inconsistent window.

**F-19 — `STAKEHOLDER_RECIPIENTS` (5 personal addresses) committed via F-01**
- Service: Resend
- File: `backend/.env:90`
- Description: PII exposure (and phishing target list) baked into git history.

### LOW

**F-20 — Global `logging.basicConfig(level=INFO)` at module scope**
- Service: Logging infrastructure
- File: `backend/app/main.py:199`
- Description: `basicConfig` is no-op if any handler already attached by an imported lib; effective log config becomes unpredictable. CI verbosity flags can also raise level globally and accidentally surface debug-only token-acquisition messages.

**F-21 — Celery sync banner uses `print()` instead of `logging`**
- Service: Celery
- File: `backend/app/celery_app/celery_config.py:142-181`
- Description: Bypasses log aggregation, level filters, structured formatters; configuration details land in raw stdout.

**F-22 — Synchronous Gemini SDK call would block asyncio if used from FastAPI**
- Service: Gemini
- File: `backend/app/clients/gemini_client.py:21-31`
- Description: `generate_content()` is sync. Currently only called from Celery (correct), but the API design invites a future async-route call that would block the event loop for 5–30 s.

## Remediation Plan

1. **Step 1 — Rotate every credential in `backend/.env` immediately** (resolves F-01). Vendor dashboards: Supabase (regen `service_role`), Shopify (delete + recreate `shpat_…`), Boeing developer portal (rotate `BOEING_CLIENT_ID`/`SECRET`/`USERNAME`/`PASSWORD` — likely a support ticket), Resend (revoke + recreate), Google Cloud (revoke Gemini key + recreate). Replace values in `.env` (do NOT commit). *Verify*: vendor dashboards show old keys at zero usage. *Risk*: high — coordinated short outage. *Vendor-dashboard required: yes (5 vendors).*

2. **Step 2 — Remove `.env` from git history** (resolves F-01 history exposure). Run `git filter-repo --path backend/.env --invert-paths` (or BFG); force-push all branches; require team to re-clone. Add a `pre-commit` hook with `detect-secrets`. *Verify*: `git log --all --full-history -- backend/.env` returns nothing. *Risk*: medium — destroys SHAs / invalidates open PRs. *Vendor-dashboard required: no.*

3. **Step 3 — Cache Boeing OAuth tokens** (resolves F-02; mitigates F-07). In `boeing_client.py`, add `_oauth_token`, `_oauth_token_expiry`, and same for `_part_token`. Reuse token while `time.time() < expiry - 60`. The `BoeingClient` singleton (via `lru_cache` in `container.py`) preserves cache across calls. *Verify*: unit test confirms one OAuth call across two batch invocations; Boeing OAuth endpoint hits drop from O(batches/min) to O(1/hour). *Risk*: low. *Vendor-dashboard required: no, but notify Boeing contact to clear existing flags.*

4. **Step 4 — Fix CORS** (resolves F-03). Replace `allow_origins=["*"]` in `middleware.py` with explicit `ALLOWED_ORIGINS` from env. If only header-token auth is used, also set `allow_credentials=False`. *Verify*: `curl -H "Origin: https://evil.example.com" -I /health` → no permissive ACAO header. *Risk*: medium — must enumerate legitimate origins (frontend, Aviation Gateway). *Vendor-dashboard required: no.*

5. **Step 5 — Make rate-limiter fail-closed** (resolves F-04). In `rate_limiter.py:156-163`, change the `RedisError` handler to return `False, 60.0, 0` and log at ERROR. *Verify*: stop Redis; trigger `process_boeing_batch`; confirm it waits/retries instead of firing the Boeing API. *Risk*: low. *Vendor-dashboard required: no.*

6. **Step 6 — Move `EC2_HOST` from workflow env to GitHub secret** (resolves F-05). Reference `${{ secrets.EC2_HOST }}` in `deploy-backend.yml`. *Verify*: `git log -p .github/workflows/deploy-backend.yml` contains no IP literal. *Risk*: low. *Vendor-dashboard required: yes (GitHub Actions secret).*

7. **Step 7 — Authenticate multi-part search endpoints** (resolves F-06). Add `current_user: dict = Depends(get_current_user)` to `multi_part_search` and the legacy route. *Verify*: anonymous POST → 401/403. *Risk*: low — confirm no anonymous client is in use. *Vendor-dashboard required: no.*

8. **Step 8 — Set `SYNC_MODE=production` and `SYNC_DISPATCH_MINUTE=45`** (resolves F-07). Update production `.env` after Step 1 rotation. *Verify*: Celery Beat logs show `dispatch-hourly-sync` at `:45` only. *Risk*: low. *Vendor-dashboard required: no.*

9. **Step 9 — Make Cognito `client_id` validation mandatory** (resolves F-08). Remove the `if settings.cognito_app_client_id:` guard in `cognito.py:122-147`. Add startup assertion in `Settings.model_post_init()` that the value is set. *Verify*: missing env-var → app refuses to start; mismatched `client_id` → 401. *Risk*: low. *Vendor-dashboard required: no.*

10. **Step 10 — Re-enable strict SSH host-key checking** (resolves F-09). Drop `-o StrictHostKeyChecking=no` from `rsync` and `ssh` lines in `deploy-backend.yml`; rely on the existing `ssh-keyscan` step (line 33). *Verify*: deploy still works against current host key; deploy fails loudly if host key changes. *Risk*: low. *Vendor-dashboard required: no.*

11. **Step 11 — Sanitize Resend send log** (resolves F-10). Replace `logger.info(f"Email sent to {to}, response={response}")` in `resend_client.py:45` with `logger.info(f"Email sent to {len(to)} recipient(s), message_id={response.get('id', 'unknown')}")`. *Verify*: no `@` symbols in Resend log lines. *Risk*: low. *Vendor-dashboard required: no.*

12. **Step 12 — Mask Boeing error bodies** (resolves F-11). In `boeing_client.py`, log the upstream body internally but raise `HTTPException(status_code=502, detail="Upstream Boeing error")` to the caller. *Verify*: client receives generic 502; logs retain detail. *Risk*: low. *Vendor-dashboard required: no.*

13. **Step 13 — Lock down Supabase access** (resolves F-12, F-17). Create a scoped Supabase API key, enable RLS on `product`, `product_sync_schedule`, `batches`. Add `.eq("user_id", user_id)` filter to `get_published_product`. Audit all stores for missing `user_id` scoping. *Verify*: User A cannot fetch User B's product via known ID. *Risk*: medium — RLS policy authoring; test in staging. *Vendor-dashboard required: yes (Supabase dashboard).*

14. **Step 14 — Add Redis password authentication** (resolves F-15). Set `requirepass` on the EC2 Redis. Update `REDIS_URL` to `redis://:password@localhost:6379/0`; deliver via GitHub secret + deploy. *Verify*: `redis-cli ping` → `NOAUTH`; with `-a <password>` → `PONG`. *Risk*: medium — coordinated restart of all workers. *Vendor-dashboard required: no.*

15. **Step 15 — Fail hard on missing API keys at startup** (resolves F-16). Drop `or ""` fallbacks in `container.py:127, 135`; raise from `Settings.model_post_init()` if Gemini/Resend keys are empty. *Verify*: missing key → app refuses to start. *Risk*: low. *Vendor-dashboard required: no.*

## Notes

- Severity counts match the prior pass (Critical 5, High 8, Medium 6, Low 3 = 22). IDs were renumbered F-01..F-22 for clarity. The prior MEDIUM "SYNC_MODE=testing" finding is reclassified HIGH (F-07) here because it directly amplifies the CRITICAL Boeing login-volume issue and drives Resend complaint rates.
- `backend/.env` is present in the working tree of this clone; do not commit again. Steps 1–2 are mandatory before anything else.
- Step 2 (history rewrite) invalidates all commit SHAs and open PRs — coordinate with the team.
- AWS instance-role permissions (F-13) cannot be audited from source alone; check via `aws iam get-role-policy` against the EC2 role out-of-band.
- `STAKEHOLDER_RECIPIENTS` in `.env:90` does not appear to be wired into `Settings`; the report service uses `report_recipients` (`config.py:104` ← `REPORT_RECIPIENTS`). The broader stakeholder list may be vestigial in code but is still exposed via F-01/F-19.
- No frontend secrets observed; `frontend/` contains no `.env`.
- Files not fully read: `backend/app/services/shopify_update_service.py`, parts of `backend/app/db/product_store.py`. IDOR patterns similar to F-17 may exist there — recommend a follow-on store-by-store query audit.
