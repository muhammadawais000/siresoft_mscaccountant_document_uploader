# Document Intake & Extraction

A reusable Django app (`document_intake`) for accounting documents. Users upload bank
statements, credit-card statements, receipts and invoices (PDFs or phone photos). The app
extracts the data with a **locally hosted model through Ollama**, checks it with accounting
rules, and shows it in an editable review screen. A person approves the result before it
can be exported as CSV.

> **Data stays on our infrastructure.** Extraction runs against an Ollama server that we
> operate. No document, image or extracted text is sent to any external API, and the
> frontend loads no CDN, font or script from outside this application.

- Python 3.12, Django 5, DRF, PostgreSQL 16, Celery + Redis, Ollama
- Server-rendered pages with [HTMX](https://htmx.org) 2.0.4 (served from our own static
  files, no CDN, no build step). Views return small HTML pieces; there is almost no
  custom JavaScript (only `js/upload.js` for drag-and-drop and the progress bar)
- Standalone today; designed to be mounted in the MSC accounting portal
  ([docs/INTEGRATION.md](docs/INTEGRATION.md))

## Quick start (about 10 minutes)

Requirements: Docker, [uv](https://docs.astral.sh/uv/), and (optionally) Ollama.

```bash
cp .env.example .env              # set DJANGO_SECRET_KEY to a long random string
make install                      # Python 3.12 venv, dependencies, git hooks
make services                     # postgres on 127.0.0.1:55433, redis on 127.0.0.1:56380
make migrate
make seed                         # demo user + 3 sample documents; prints the password
make run                          # http://localhost:8000  (sign in as "demo")
make worker                       # in a second terminal: runs extractions
```

`make seed` works **without Ollama**: the sample documents use canned extraction output.
One sample (the card statement) is deliberately inconsistent so validation errors show.

To run everything in containers instead: `make up` (postgres, redis, web, worker), then
`docker compose exec web python manage.py seed_demo`.

### Connecting Ollama

1. Install Ollama on the host or a separate server and pull a **vision** model, for
   example `ollama pull qwen2.5vl:7b`. The model is not hardcoded; choose and benchmark
   your own.
2. Set `OLLAMA_VISION_MODEL` (and optionally a text-only `OLLAMA_TEXT_MODEL` for PDFs with
   a text layer) in `.env`.
3. Check: `make check-ollama` (or `uv run python manage.py check_ollama --probe` to also run
   a tiny real request).

From inside Docker, the web and worker containers reach the host's Ollama at
`http://host.docker.internal:11434` (override with `OLLAMA_BASE_URL_DOCKER`). Ollama only
listens on `127.0.0.1` by default; on a server set `OLLAMA_HOST` so the containers can
reach it, and keep port 11434 closed to the outside with a firewall.

Size `OLLAMA_NUM_CTX` (default 16384 tokens) to your model and RAM: long statements need
a large context window, and Ollama's own default is much smaller.

## How it works

```
upload ─> checks (type, real content, size, SHA-256, duplicate) ─> private storage
       ─> Celery task ─> PDF with text? pdfplumber text ─┐
                         scan or photo? page images ─────┴─> Ollama (JSON schema, temp 0)
       ─> Decimal parsing ─> validation rules ─> needs_review ─> human review ─> approved
       ─> CSV export (QuickBooks-friendly columns) ─> exported
```

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

- **Never auto-approved.** Every extraction stops at *Needs review*. Approval is blocked
  while validation errors are open unless the reviewer explicitly acknowledges them.
- **Never guessed.** The model is told to return null for anything not clearly legible.
  Values that do not parse are kept verbatim and flagged, never "repaired". Money is
  `Decimal` on the server and integer cents in the browser, never float.
- **Exact duplicates** (same file, same client) are linked and not sent to the model.

## Day-to-day commands

| Command | What it does |
|---|---|
| `make check` | Everything CI runs: ruff, mypy, pytest, JS syntax check |
| `make test` / `make cov` | Python tests / with coverage |
| `make jstest` | Syntax check of `js/upload.js` |
| `make format` | Auto-format and fix lint |
| `make seed` | Demo user and documents |
| `make check-ollama` | Verify Ollama and the configured models |

API docs (signed in): `/api/v1/intake/docs/`. Schema: `/api/v1/intake/schema/`.

## Configuration

Environment variables are listed in [.env.example](.env.example). Module behaviour is set
in one place, the `DOCUMENT_INTAKE` dict (defaults in
[document_intake/conf.py](document_intake/conf.py)): upload limits, model settings,
page/size guards, tax rate, permission hooks and feature flags.

## Production notes

- **Server with Docker + GPU Ollama on the host:** see
  [docs/DEPLOY_DOCKER_SERVER.md](docs/DEPLOY_DOCKER_SERVER.md) (`docker-compose.server.yml`).
- **CentOS / RHEL server with systemd:** see [docs/DEPLOY_CENTOS.md](docs/DEPLOY_CENTOS.md)
  (`deploy/centos/install.sh` installs web + worker as background services).
- Use `DJANGO_SETTINGS_MODULE=config.settings.prod` (secure cookies, HSTS, SSL redirect).
  The Docker image runs gunicorn; `docker-compose.yml` is for local development.
- Put Nginx in front: terminate TLS, set `X-Forwarded-Proto`, and set
  `client_max_body_size 26m;` so the 25 MB upload limit is enforced before Django.
- Serve `/static/` (after `collectstatic`). **Never** serve `INTAKE_PRIVATE_STORAGE_ROOT`:
  documents are only available through the permission-checked API.
- Back up PostgreSQL and the private storage volume together.
- Logs contain IDs and statuses only, never document contents. Use `LOG_FORMAT=json`.

## Retention

Documents are never hard-deleted: they are soft-deleted (`deleted_at`) and each record
has a `retain_until` field. **The retention period is a business decision still to be
confirmed** (the requirements spec, question Q19, notes CRA generally expects 6 years).
No purge job exists yet.

## Project layout

```
config/                 thin host project (settings, urls, celery)
document_intake/        the reusable app
  api/  ui/             REST API (/api/v1/intake/) and pages (/intake/)
  services/             business logic: upload, extraction, validation, lifecycle, review, export
  validators/           pure-Python accounting rules
  extraction/           routing, PDF/image readers, schemas, prompts, parsing
  providers/            ExtractionProvider interface + OllamaProvider
  models/               Document, ExtractionResult, fields, lines, issues, audit log
  static/ templates/    CSS, vendor/htmx.min.js, upload.js; pages + partials/ (HTMX pieces)
  tests/                pytest suite and node tests
docs/                   ARCHITECTURE, INTEGRATION, REVIEW_CHECKLIST
```
