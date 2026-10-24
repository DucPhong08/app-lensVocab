# Correctness and optimization rollout — 2026-10-11

This document supersedes older descriptions of parent/alias cache hits and Redis counters for authenticated users.


## Auth and quota refactor — 2026-10-24

- Existing auth routes, token response fields and error codes remain compatible. No refresh/logout, OTP or social-login endpoints are added in this refactor.
- Registration still checks email before hashing, and also translates a duplicate-key error during insert to `EMAIL_ALREADY_REGISTERED` (HTTP 400), covering simultaneous registrations of the same normalized email. Other Mongo failures are not classified as duplicate emails.
- Email continues to be stripped/lowercased. Display names are now stripped, with an empty name stored as null. Password content is never stripped, normalized or truncated.
- Registration rejects passwords longer than **72 UTF-8 bytes** with HTTP 422 before bcrypt runs. The existing minimum of six characters remains. This is a byte limit, so Vietnamese characters can occupy more than one byte. Login behavior is unchanged for oversized passwords: invalid bcrypt input is rejected as invalid credentials.
- Validation errors retain the existing envelope and error locations/messages, but omit the raw `input` value from `data` and `detail` to avoid echoing passwords or private request data.
- An explicit `timedelta(0)` token lifetime is now honored. The configured default still applies only when no lifetime is supplied. Invalid password hashes return false; unexpected bcrypt failures are allowed to surface as infrastructure failures rather than wrong credentials.
- The auth dependency centralizes HTTP 401 responses and their Bearer challenge while retaining per-request active-user lookup and admin checks. `/auth/me` validates only the declared response fields from the User model; stored password hashes/admin internals are not exposed.
- The quota refactor names the Mongo filter and update pipeline explicitly and shares one BSON encoder. Reset/decrement remains one atomic update with Mongo as the source of truth, Redis checked first, and **UTC day boundaries unchanged**. This change adds no quota reset, tier change, schema migration, dependency, or database operation during rollout.
- Offline regression coverage includes zero token lifetime, UTF-8 password boundaries, validation redaction, concurrent duplicate registration, authentication failures and active/admin authorization. Real Mongo concurrency tests remain opt-in and require a disposable local replica set.

## API and behavior changes

- `/admin/settings` GET/PATCH requires an active authenticated user with `is_admin=true`. All existing accounts default to false, including PREMIUM. No public registration/preferences endpoint can grant this field. Provision the intended administrator through a controlled database operation after checking that account's UUID; never promote all premium accounts.
- Settings writes return 503 `SETTINGS_SAVE_FAILED` if MongoDB cannot persist them. A failed write does not change the process fallback snapshot. Explicit null is rejected for editable settings/preferences.
- `/flashcards/confirm` needs only `{ "keyword": "chair" }`; trims/lowercases keyword (1–100 characters). Legacy extra content fields are accepted and ignored. Content comes from exact global Mongo card or server-created Redis draft; missing draft returns 409 `DRAFT_EXPIRED`. Repeated confirm returns the same user card. No AI call is made by confirm.
- Scan now uses exact keyword Redis → exact keyword Mongo → Bedrock/Polly. Parent/alias labels remain metadata, never replace the recognized keyword. Redis responses report `source=redis`.
- Generated text must pass Pydantic validation before caching. Malformed JSON/schema is retried within the existing bounded retry policy; exhausted generation returns 502 `AI_CONTENT_INVALID` or an SSE error event.
- All three upload routes read at most 5 MB + 1 and reject bad MIME/empty/oversized files before consuming quota. Request-body limits at the ingress should still be configured independently.
- Authenticated quota is decremented/reset atomically in the existing Mongo User document. Redis cache availability is checked first; guest budgets still use Redis Lua. No tier limits or refund policy changed. Old `quota:{id}` keys are no longer read and can expire normally.
- Preferences update only the preferences field, so a stale snapshot cannot overwrite quota/admin fields.
- Review accepts optional `review_id` UUID. The same user/ID/card/quality returns the original response; reuse with changed card/quality returns 409 `REVIEW_ID_CONFLICT`. UI keeps the answer ID and quality on retry. Old clients without an ID retain legacy repeat-submission semantics.
- Review card update and ReviewLog insert run inside a Mongo transaction. Replica-set/Atlas support is required; a standalone Mongo server is not supported for grading.
- ReviewLog adds nullable `review_id` and nullable `result` (serialized response). Beanie creates a partial unique `(user_id, review_id)` index only for UUID binary IDs, so legacy logs are unaffected.
- Due queue applies its cap in Mongo before materializing results. The cap remains per queue request, not a daily total limit.
- Confirm no longer generates Titan embeddings because scan does not use vector search. Existing embeddings/index/model are retained, and new cards have `embedding=null`. Any future vector feature needs an explicit backfill plan.
- AWS clients are created under a lock and reused within a process; Nova and Titan share `AWS_EMBEDDING_REGION` (falling back to `AWS_REGION`), preserving the existing deployment configuration. Rekognition/Polly use `AWS_REGION`. Live testing confirmed Nova Lite works with the existing credentials in `ap-southeast-2`; invoking it in `ap-southeast-1` was denied. Clients close at shutdown. Bcrypt runs in a worker thread.

## Deploy order

1. Verify the target Mongo deployment supports transactions and that startup creates the new partial unique index.
2. Deploy BE first. Replace all old API instances together when changing quota authority; old code can still overwrite counters from stale user snapshots.
3. Provision one explicit administrator through the database if admin access is needed; this code change does not grant anyone privileges automatically.
4. Deploy UI after BE supports keyword-only confirm and review_id. UI continues to use the existing full-list endpoints.
5. Smoke test guest scan → login → confirm → review → retry/reopen on a real device.

Rolling back UI alone is compatible with the BE (old confirm fields are ignored and review_id is optional). Rolling BE back removes the security/correctness fixes; do not roll back to old admin/confirm code as a routine mitigation. No data deletion or embedding migration is included.

Existing shared vocabulary created before this fix may contain client-supplied content. Audit it separately; this change neither proves its correctness nor rewrites existing records.

## Verification

Offline suite:

```bash
PYTHONDONTWRITEBYTECODE=1 SECRET_KEY=offline-review-test-key .venv/bin/python -m unittest discover tests
```

Optional real Mongo tests (disposable local replica set only, no AWS/production credentials):

```bash
PYTHONDONTWRITEBYTECODE=1 SECRET_KEY=offline-review-test-key LENSVOCAB_TEST_MONGO_URL='mongodb://127.0.0.1:27028/?directConnection=true' .venv/bin/python -m unittest tests.test_mongo_integration -v
```

The integration suite creates a random test database per case and drops only that namespace. It checks transaction rollback, concurrent review replays, concurrent first-day quota, quota reset, authoritative confirm, and due ordering/cap. Without the explicit test URL those tests are skipped.

Not included: pagination/audio endpoints, native FlatList profiling, SSE mobile adoption, personalized voices, model changes, or AWS latency/cost claims. These require the measurements/product decisions described in the UI optimization plan.
