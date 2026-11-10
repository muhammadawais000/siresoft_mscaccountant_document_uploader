"""factory_boy factories for tests and the seed command."""

import hashlib
import uuid
from decimal import Decimal

import factory
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile

from document_intake.models import (
    AuditAction,
    AuditLog,
    DocType,
    Document,
    ExtractedField,
    ExtractedLine,
    ExtractionMethod,
    ExtractionResult,
    Severity,
    ValidationIssue,
)


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = get_user_model()
        django_get_or_create = ("username",)

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.test")
    password = factory.django.Password("pass-for-tests")


class DocumentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Document

    class Params:
        content = b"%PDF-1.4 test document"

    uploaded_by = factory.SubFactory(UserFactory)
    original_filename = "statement.pdf"
    mime_type = "application/pdf"
    doc_type = DocType.BANK_STATEMENT
    size_bytes = factory.LazyAttribute(lambda o: len(o.content))
    sha256 = factory.LazyAttribute(lambda o: hashlib.sha256(o.content).hexdigest())
    file = factory.LazyAttribute(lambda o: ContentFile(o.content, name=o.original_filename))


class ExtractionResultFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ExtractionResult

    document = factory.SubFactory(DocumentFactory)
    version = factory.Sequence(lambda n: n + 1)
    extraction_method = ExtractionMethod.PDF_TEXT
    provider = "ollama"
    model_name = "test-model"
    prompt_version = "v1"


class ExtractedFieldFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ExtractedField

    result = factory.SubFactory(ExtractionResultFactory)
    key = factory.Sequence(lambda n: f"field_{n}")
    label = factory.LazyAttribute(lambda o: o.key.replace("_", " ").title())
    value = "value"


class ExtractedLineFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ExtractedLine

    result = factory.SubFactory(ExtractionResultFactory)
    line_no = factory.Sequence(lambda n: n + 1)
    description = "Line item"
    amount = Decimal("10.00")


class ValidationIssueFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ValidationIssue

    result = factory.SubFactory(ExtractionResultFactory)
    rule_code = "test_rule"
    severity = Severity.WARNING
    message = "Test issue"


class AuditLogFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = AuditLog

    document = factory.SubFactory(DocumentFactory)
    action = AuditAction.UPLOAD
    actor = factory.LazyAttribute(lambda o: o.document.uploaded_by)
    metadata = factory.LazyFunction(lambda: {"trace": uuid.uuid4().hex})
