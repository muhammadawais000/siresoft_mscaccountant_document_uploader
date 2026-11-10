"""Server-rendered UI. Mount with ``path("intake/", include("document_intake.ui.urls"))``."""

from django.urls import path

from . import views

app_name = "document_intake"

urlpatterns = [
    path("", views.DocumentListView.as_view(), name="document-list"),
]
