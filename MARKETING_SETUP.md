# A2Z Marketing Center — deployment and roadmap

This feature is in a draft PR. **No live posting has been implemented.**
The creative engine now renders branded images from uploaded store photographs and 9-second vertical reels from approved MP4 store footage (or 6-second photo-based reels). Image and clip generation is code-complete but requires staging validation before launch.

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

## Stage 3 — media production (implemented, pending validation)

- `/marketing` includes a media library for uploaded JPEG, PNG, WebP (up to 8MB), and MP4 (up to 25MB).
- Store original normalized JPEG and source MP4 bytes in the existing PostgreSQL database.
- Approved photos are automatically matched to draft subjects using supplied tags. Drafts without relevant photos remain unrendered rather than inventing an animal photo.
- Render 1080x1080 square, 1080x1350 portrait, or 1080x1920 story JPEGs using the real A2Z repository logo.
- Render 720x1280 photo-based six-second MP4s or nine-second branded videos from actual store MP4 footage.
- Generated media is stored for preview and owner approval. An approved post must reference a creative matching the exact current text; editing text invalidates old creative approvals.
- There is no background storage cleanup or video thumbnail generation yet; monitor database growth.
- Videos have no licensed music, and footage is cropped/normalized. Additional video editing styles remain future work.
- Real media copyright and permission remain the operator's responsibility.

## Stage 4 — publishing (not yet implemented)

1. Add editable media crop/focal point, creative revisions, per-platform previews and scheduling.
2. Connect a provider such as Buffer through supported authorized APIs.
3. Only queue an exact approved text+media snapshot for external publication.
4. Re-check price, availability, promotions and event dates at publish time.
5. Add publishing idempotency, failure reporting and analytics.
6. Validate with staging social accounts before enabling production posting.

## Verification before merging

- `pytest tests/test_marketing.py tests/test_marketing_creative.py`
- Generate drafts on staging at `/marketing`, upload a store photo, render square/portrait/story/reel, preview, approve, reject, edit and revise. Verify the old creative is rejected after text changes. Restart and check persistence.
- Upload a real short MP4 clip and render a vertical reel. Validate video codec, dimensions, crop, logo placement and output with multiple devices.
- Test weekly endpoint without secret (401), valid secret (creates), duplicate call (no-op).
- Confirm invoice automation and Lightspeed APIs still work.
- Restrict the app to HTTPS, use a strong APP_PASSWORD and never publish secrets in GitHub.
