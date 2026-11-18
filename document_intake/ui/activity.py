"""One readable sentence per audit log entry, for the Activity section."""

from __future__ import annotations

from typing import Any

from document_intake.models import AuditLog, DocumentStatus, ExtractionMethod

STATUS_LABELS = dict(DocumentStatus.choices)
METHOD_LABELS = dict(ExtractionMethod.choices)


def actor_name(entry: AuditLog) -> str:
    if entry.actor is None:
        return "System"
    full_name = str(getattr(entry.actor, "get_full_name", lambda: "")())
    return full_name or str(entry.actor.get_username())


def _status(value: Any) -> str:
    return STATUS_LABELS.get(value, str(value))


def _edit(m: dict[str, Any]) -> str:
    if m.get("target") == "field":
        return f"{m.get('key')}: “{m.get('before')}” → “{m.get('after')}”"
    if m.get("target") == "issue":
        return f"{m.get('rule_code')} {'acknowledged' if m.get('resolved') else 'reopened'}"
    if m.get("operation") == "add":
        return f"Added line {m.get('line_no')}"
    if m.get("operation") == "delete":
        return f"Removed line {m.get('line_no')}"
    before = m.get("before") or {}
    changes = ", ".join(
        f"{key} “{before.get(key) or ''}” → “{value or ''}”"
        for key, value in (m.get("after") or {}).items()
    )
    return f"Line {m.get('line_no')}: {changes}"


def describe(entry: AuditLog) -> str:
    m: dict[str, Any] = entry.metadata or {}
    action = entry.action
    if action == "upload":
        duplicate = ", duplicate of an earlier upload" if m.get("duplicate_of") else ""
        return f"Uploaded ({m.get('mime_type', 'file')}){duplicate}"
    if action == "extract":
        if m.get("outcome") != "success":
            return "Extraction failed"
        method = METHOD_LABELS.get(str(m.get("method")), m.get("method"))
        return (
            f"Version {m.get('version')} · {method} · {m.get('model')} · "
            f"{m.get('lines')} lines · {m.get('issues')} issues"
        )
    if action == "status_change":
        text = f"{_status(m.get('from'))} → {_status(m.get('to'))}"
        if m.get("reason"):
            text += f" · {m['reason']}"
        if m.get("question"):
            text += f" · “{m['question']}”"
        return text
    if action == "edit":
        return _edit(m)
    if action == "approve":
        text = "Approved"
        if m.get("acknowledged"):
            text += f" · acknowledged {', '.join(m['acknowledged'])}"
        if m.get("note"):
            text += f" · {m['note']}"
        return text
    if action == "reject":
        return f"Rejected · {m.get('reason', '')}"
    if action == "download":
        return (
            "Downloaded the original"
            if m.get("disposition") == "attachment"
            else "Viewed the original"
        )
    if action == "export":
        return f"Exported CSV ({m.get('rows')} rows, version {m.get('version')})"
    if action == "reprocess":
        return "Requested a new extraction"
    if action == "delete":
        return "Deleted the document"
    return str(action)
