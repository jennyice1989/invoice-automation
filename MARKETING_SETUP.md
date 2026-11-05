# A2Z Marketing Center — deployment and roadmap

This feature is in a draft PR. **No live posting or automatic image rendering has been implemented.**
All generated content is text plus a visual brief, **not a completed graphic**.

## Stage 1 — draft approval queue

- Existing FastAPI login is required for `/marketing`, `/marketing/api/drafts`, `/marketing/api/generate`, edit, revise, approve, and reject endpoints.
- Draft records are persisted in the existing Render PostgreSQL database.
- The current app's `OPENAI_API_KEY` is reused for content-generation requests.
- Set `MARKETING_OPENAI_MODEL` to a model supporting Chat Completions JSON object mode (default `gpt-4.1-mini`).
- Existing catalog is sampled to inspire creative ideas. Catalog cache cannot confirm stock.
- Approval means *approved in the draft queue* only. It does not publish.

## Stage 2 — automated weekly generation

Add `MARKETING_CRON_SECRET` in the Render environment to a securely generated secret.
Schedule a weekly HTTPS `POST https://YOUR-APP-HOST/marketing/api/weekly-run` request,
including `Authorization: Bearer <MARKETING_CRON_SECRET>`.
Use a secure scheduler such as a Render Cron Job and keep credentials in its secret environment,
not in public source code. The endpoint creates up to five pending ideas for each ISO week
and makes repeated calls for that week no-ops. A failed attempt is marked failed,
requiring operator recovery; it does not silently repeat and risk duplicates.
As of this PR, the weekly scheduler itself is **not provisioned**.

## Stage 3 — media + publishing (not yet implemented)

1. Make a private approved media library (store photos, licensed supplier media, real footage).
2. Render actual branded images and edited videos and persist immutable, reviewable previews.
3. Add per-platform previews, publication times, and editable creative assets.
4. Connect a provider (e.g. Buffer) via authorized API credentials and supported channel IDs.
5. Only queue a post **after the owner has approved the precise final text AND media**.
6. Re-check price, availability, offers and expiration at publish time; block uncertain claims.
7. Use publish attempt IDs for idempotency, logging, retries, and failure notifications.
8. Track published URLs, results and engagement metrics where the platform API supports them.
9. Test with staging accounts before enabling any production posting.

## Verification before merging

- `pytest tests/test_marketing.py`
- Generate drafts on staging at `/marketing`, reject/edit/revise/approve, restart and check persistence.
- Test weekly endpoint without secret (401), valid secret (creates), duplicate call (no-op).
- Confirm invoice automation and Lightspeed APIs still work.
- Restrict the app to HTTPS, use a strong APP_PASSWORD and never publish secrets in GitHub.
