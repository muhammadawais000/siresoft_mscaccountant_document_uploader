"""Signals the host portal can subscribe to without modifying this app.

Both send ``sender=Document`` and keyword arguments:

* ``document_extracted``: ``document``, ``result`` (the new ExtractionResult).
* ``document_approved``: ``document``, ``result``, ``actor``.

Receivers run after the database transaction commits.
"""

from django.dispatch import Signal

document_extracted = Signal()
document_approved = Signal()
