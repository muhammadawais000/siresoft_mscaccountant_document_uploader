"""Create a demo user and three sample documents (one with deliberate inconsistencies).

Usage: ``python manage.py seed_demo [--username demo] [--password ...]``. Idempotent:
documents that already exist for the demo user (same content) are not created again.
Runs without Ollama: extraction uses canned output.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandParser

from document_intake import demo
from document_intake.models import Document
from document_intake.services.extraction import run_extraction
from document_intake.services.upload import create_document


class Command(BaseCommand):
    help = "Create a demo user and 3 sample documents (works without Ollama)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--username", default="demo")
        parser.add_argument(
            "--password", default="", help="Defaults to a random password that is printed."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        user_model = get_user_model()
        username = options["username"]
        user, created = user_model.objects.get_or_create(
            **{user_model.USERNAME_FIELD: username}, defaults={"is_staff": True}
        )
        password = options["password"]
        if created or password:
            password = password or secrets.token_urlsafe(12)
            user.set_password(password)
            user.save()
            self.stdout.write(f"User '{username}' password: {password}")
        else:
            self.stdout.write(f"User '{username}' exists; password unchanged.")

        for spec in demo.DOCUMENTS:
            content = demo.pdf_for(spec)
            digest = hashlib.sha256(content).hexdigest()
            if Document.objects.filter(uploaded_by=user, sha256=digest).exists():
                self.stdout.write(f"  exists: {spec['filename']}")
                continue
            document = create_document(
                upload=SimpleUploadedFile(spec["filename"], content, "application/pdf"),
                uploaded_by=user,
                doc_type=spec["doc_type"],
                enqueue=False,  # extraction runs inline below with canned output
            )
            result = run_extraction(
                document.id, demo.CannedProvider(spec["data"], spec["doc_type"])
            )
            issues = result.issues.count() if result else 0
            self.stdout.write(f"  created: {spec['filename']} ({issues} validation issues)")
        self.stdout.write(self.style.SUCCESS("Demo data ready. Open /intake/ and sign in."))
