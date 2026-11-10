"""Abstract bases: UUID keys, timestamps and soft delete."""

from __future__ import annotations

import uuid
from typing import Any, Self

from django.conf import settings
from django.db import models
from django.utils import timezone

from document_intake.exceptions import IntakeError


class HardDeleteForbidden(IntakeError):
    """Financial records are never hard-deleted; use ``soft_delete()``."""

    code = "hard_delete_forbidden"


class TimeStampedModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteQuerySet(models.QuerySet[Any]):
    def alive(self) -> Self:
        return self.filter(deleted_at__isnull=True)

    def deleted(self) -> Self:
        return self.filter(deleted_at__isnull=False)

    def delete(self) -> tuple[int, dict[str, int]]:
        raise HardDeleteForbidden("Bulk hard delete is not allowed on financial records.")


class AliveManager(models.Manager[Any]):
    """Default manager: hides soft-deleted rows."""

    def get_queryset(self) -> SoftDeleteQuerySet:
        return SoftDeleteQuerySet(self.model, using=self._db).alive()


class SoftDeleteModel(TimeStampedModel):
    """Rows are hidden, never removed. ``retain_until`` is set by a future retention policy."""

    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    retain_until = models.DateField(
        null=True,
        blank=True,
        help_text="Earliest date the record may be purged. Policy still to be confirmed.",
    )

    objects = AliveManager()
    all_objects = SoftDeleteQuerySet.as_manager()

    class Meta:
        abstract = True
        base_manager_name = "all_objects"

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self, actor: Any | None = None) -> None:
        self.deleted_at = timezone.now()
        self.deleted_by = actor
        self.save(update_fields=["deleted_at", "deleted_by", "updated_at"])

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise HardDeleteForbidden("Hard delete is not allowed; use soft_delete().")
