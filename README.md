# Document Intake & Extraction

A reusable Django app (`document_intake`) for uploading accounting documents, extracting
their data with a locally hosted model (Ollama), and reviewing the result before approval.

**Data stays on our infrastructure.** Extraction runs against an Ollama server that we
operate. No document, image or extracted text is sent to an external API.

> Status: delivery step 1 of 9 (scaffold). Full setup and usage docs arrive in step 9.

## Quick start (local)

Requires Docker and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env        # then set DJANGO_SECRET_KEY
make install                # Python 3.12 venv + deps + git hooks
make services               # postgres (127.0.0.1:55433) + redis (127.0.0.1:56380)
make migrate
make run                    # http://localhost:8000
```

Or run everything in containers: `make up`.

## Checks

```bash
make check                  # ruff + mypy + pytest
```
