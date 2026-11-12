# Final review checklist

Status at the end of delivery step 9. ✅ done and tested · ⚠️ done with a caveat ·
⏳ open (needs a decision or later work).

## Security

| Item | Status | Where / how verified |
|---|---|---|
| Upload checks: extension, real MIME (magic bytes), size, empty file | ✅ | `services/upload.py`; spoofed `.exe`→`.pdf`, PNG named `.pdf`, HTML disguised as PDF tests |
| Image sanity: corrupt files, decompression bombs | ✅ | `MAX_IMAGE_PIXELS`; truncated and oversized image tests |
| Filenames sanitised; storage path never uses the client name | ✅ | `../../etc/passwd.pdf` test |
| Private storage with no public URL; downloads only through a permission check | ✅ | `PrivateFileSystemStorage.url()` raises; file endpoint tests |
| User A cannot see user B's document (API, file, pages, activity, UI) | ✅ | 404 tests on every endpoint |
| Clients can be blocked from approving (`REVIEW_PERMISSION_CLASSES`) | ✅ | hook test; portal must configure it |
| CSRF on session API; secure cookies, HSTS, SSL redirect in prod | ✅ | `check --deploy` clean (HSTS preload left off on purpose) |
| CORS locked down | ✅ | no CORS headers are sent; cross-origin calls are blocked by browsers |
| Content-Security-Policy on intake pages (no inline scripts) | ✅ | CSP test; the login page (host project) has no CSP yet ⚠️ |
| No HTML built in JavaScript | ✅ | pages are Django templates (auto-escaped); htmx config disables `eval` and script tags; test scans `upload.js` |
| CSV formula injection | ✅ | `=HYPERLINK(...)` test |
| Upload rate limit | ✅ | 60/hour per user, 429 test |
| Logs carry IDs and statuses only | ✅ | end-to-end test scans every log record; this test found that pdfminer logs document text at DEBUG, now silenced in `apps.py` |
| Audit log append-only | ✅ | ORM guard + PostgreSQL trigger test |
| No external API calls for extraction | ✅ | only `OLLAMA_BASE_URL`; frontend loads nothing external (test) |
| Secrets only from the environment; `.env` git-ignored | ✅ | `.env.example` |
| Malware scanning of uploads | ⏳ | proposed in the spec (Q17); not implemented. ClamAV can be added as a step in `validate_upload` |
| Page previews are not audited (originals are) | ⚠️ | deliberate, to avoid one log entry per zoom; change if every view must be logged |

## Data integrity

| Item | Status | Notes |
|---|---|---|
| Money as `Decimal` on the server, BigInt cents in the browser | ✅ | float input from the model is rejected |
| Model output never auto-approved | ✅ | only `needs_review → approved` via `approve()` |
| Nulls never silently filled | ✅ | missing fields stay `missing`; currency default is flagged as a warning |
| Unreadable values kept verbatim and flagged | ✅ | e.g. `>$12.40` from a live run |
| Full account numbers never stored | ✅ | masked to last 4, flagged |
| History kept: result versions, soft-deleted lines, audit trail | ✅ | |
| Oversized documents fail loudly, never truncated | ✅ | `MAX_PAGES`, `MAX_VISION_PAGES`, `MAX_TEXT_CHARS` |
| Retention period and purge job | ⏳ | `retain_until` exists; period is a business decision (spec Q19) |

## Reliability

| Item | Status | Notes |
|---|---|---|
| Extraction off the request cycle (Celery), eager mode for dev/tests | ✅ | |
| Idempotent task, retries with backoff, `failed` dead-letter state | ✅ | verified with the Docker worker against an unreachable Ollama |
| Unreachable Ollama fails fast | ✅ | 10 s connect timeout (was the full 180 s read timeout; found in Docker testing) |
| Explicit Ollama context window | ✅ | `OLLAMA_NUM_CTX`; Ollama's default would cut long statements |
| `check_ollama` command | ✅ | `--probe` runs a real request |

## Performance

| Item | Status | Notes |
|---|---|---|
| Document list: constant query count (issue counts via subqueries) | ✅ | ≤ 4 queries test |
| Result endpoint: constant query count with 59 lines | ✅ | ≤ 10 queries test |
| Large uploads streamed to disk, hashed in 1 MB chunks | ✅ | |
| Nginx `client_max_body_size` | ⏳ | set on the server (README) |

## Accessibility

| Item | Status | Notes |
|---|---|---|
| Colour contrast WCAG AA | ✅ | every token pair measured; nav indicator raised to 3:1 |
| Status never by colour alone | ✅ | text + shape marker on badges; text reasons on fields |
| Keyboard: focus rings, tab order, Enter-to-next-row, PageUp/Down in viewer, arrow keys on tabs | ✅ | |
| Screen readers: labels on every input, `aria-live` for status and save state, `aria-invalid`, dialogs via `<dialog>` | ✅ | |
| Reduced motion respected | ✅ | |
| Responsive to tablet | ✅ | screenshots at 820–900 px |
| Automated audit (axe) | ⏳ | not run; recommended before launch |

## Test summary

- Python: pytest, 430+ tests (unit, API, permissions, end-to-end with the provider faked).
  Coverage 99% on `services/` and `validators/` (target was 85%).
- JavaScript: only `upload.js` (syntax-checked); totals are computed in Python and unit-tested.
- Manual: headless Chrome walkthrough (upload, review, edit, approve, export, activity),
  live run against local Ollama, full Docker stack (web, worker, seed).

## Open decisions for MSC

1. Retention period and what happens when a client leaves (spec Q19).
2. Malware scanning on upload (spec Q17).
3. QuickBooks Desktop import format and date format for CSV (spec Q25); dates are
   ISO `YYYY-MM-DD` today.
4. Bulk export across documents filtered by client and date (spec FP-32).
5. Which vision model to run on the server, and its context size and RAM budget.
