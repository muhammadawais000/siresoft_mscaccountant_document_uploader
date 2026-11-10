"""Versioned REST API.

Mount with ``path("api/v1/intake/", include("document_intake.api.urls"))``.
"""

from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from . import views

app_name = "document_intake_api"

urlpatterns = [
    path("health/", views.HealthView.as_view(), name="health"),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "docs/",
        SpectacularSwaggerView.as_view(url_name="document_intake_api:schema"),
        name="docs",
    ),
]
