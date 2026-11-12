# Mounting `document_intake` in another Django project

This app was built to drop into the MSC accounting portal (Django + React + Nginx +
PostgreSQL) without the portal changing this app's code, and without this app's
migrations depending on portal models.

## 1. Install

Add the app and its requirements (see `pyproject.toml`: DRF, drf-spectacular, Celery,
httpx, pdfplumber, pypdfium2, Pillow, python-magic, jsonschema). The host needs
`libmagic` installed (`apt-get install libmagic1`).

```python
INSTALLED_APPS += ["rest_framework", "drf_spectacular", "document_intake"]
```

The app uses `settings.AUTH_USER_MODEL` and never imports a concrete user model.

## 2. URLs

```python
urlpatterns += [
    path("api/v1/intake/", include("document_intake.api.urls")),  # namespace document_intake_api
    path("intake/", include("document_intake.ui.urls")),  # namespace document_intake
]
```

The prefixes can change; the UI reads its API base and page URLs from `reverse()`.
Two namespaces are used because the API and the UI are separate URL modules:
`document_intake_api:*` for the API and `document_intake:*` for pages.

If the portal has its own React frontend, mount only the API and ignore the UI. The UI
does not depend on the API: it is server-rendered with HTMX and calls the same services.

## 3. Storage

Documents use the Django storage alias in `DOCUMENT_INTAKE["STORAGE_ALIAS"]`
(default `intake_private`). It must point to storage that is **not publicly served**:

```python
STORAGES["intake_private"] = {
    "BACKEND": "document_intake.storage.PrivateFileSystemStorage",
    "OPTIONS": {"location": "/srv/intake-private"},
}
```

`PrivateFileSystemStorage` refuses to build URLs, so a mistake fails loudly. For
S3-compatible storage use a private bucket backend; files are still only served through
`GET documents/{id}/file/`.

## 4. Settings

Everything lives in one dict; only override what you need (defaults:
`document_intake/conf.py`).

```python
DOCUMENT_INTAKE = {
    "OLLAMA_BASE_URL": env("OLLAMA_BASE_URL"),
    "OLLAMA_VISION_MODEL": env("OLLAMA_VISION_MODEL"),
    "MAX_UPLOAD_BYTES": 25 * 1024 * 1024,
    "PERMISSION_CLASSES": ("portal.permissions.CanUseIntake",),
    "REVIEW_PERMISSION_CLASSES": ("portal.permissions.IsFirmStaff",),
    "DOCUMENT_SCOPE": "portal.intake.scope_documents",
}
```

Celery: the app registers one task, `document_intake.extract_document`, through
`autodiscover_tasks()`. Run at least one worker with access to the private storage.
Use `acks_late` (already set on the task) and a broker visibility timeout longer than
`OLLAMA_TIMEOUT_SECONDS`.

## 5. Linking documents to portal records

`Document` has two plain fields, not foreign keys:

| Field | Use |
|---|---|
| `owner_reference` (UUID) | The portal's client / company ID. Also scopes duplicate detection. |
| `request_reference` (text) | The portal's DocumentRequest (or period) ID. |

Send them with the upload (`owner_reference`, `request_reference` form fields) or set
them in a signal receiver. Filter the list with `?owner_reference=<uuid>`.

## 6. Permissions

Three hooks, all in `DOCUMENT_INTAKE`:

| Setting | Applies to | Default |
|---|---|---|
| `PERMISSION_CLASSES` | every API request and object | `IsOwnerOrStaff`: users see their own uploads, staff see all |
| `REVIEW_PERMISSION_CLASSES` | edit, approve, reject, clarify, duplicate, reprocess, export | `IsOwnerOrStaff` |
| `DOCUMENT_SCOPE` | which documents a user can list or open (`(user, queryset) -> queryset`) | own uploads; staff all |

For the portal, the spec says clients upload but never approve accounting entries, so
set `REVIEW_PERMISSION_CLASSES` to a staff-only class, and make `DOCUMENT_SCOPE` follow
client assignments (for example: client users see their company's documents; staff see
their assigned clients). Documents outside the scope return 404, not 403.

## 7. Signals

Subscribe without modifying this app. Both fire after the transaction commits.

```python
from document_intake.signals import document_approved, document_extracted


@receiver(document_extracted)
def notify_accountant(sender, document, result, **kwargs): ...


@receiver(document_approved)
def mark_request_received(sender, document, result, actor, **kwargs): ...
```

## 8. Swapping the extraction engine

Implement `document_intake.providers.base.ExtractionProvider` (`complete_json`,
`model_for`) and set `DOCUMENT_INTAKE["PROVIDER_CLASS"]`. Business logic, validation and
the UI do not change.

## 9. Templates

UI templates extend `document_intake/base.html`. To use the portal's shell, override that
template in the portal's template directory and keep the `head`, `page_title`, `actions`,
`content` and `scripts` blocks, the `htmx-config` meta tag, the `hx-headers` CSRF attribute
on `<body>`, the `#messages` element and the `vendor/htmx.min.js` script.
