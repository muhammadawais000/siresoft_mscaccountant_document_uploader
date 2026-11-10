"""Host project URLs. A portal would add the same two ``include()`` lines to its own urls."""

from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/intake/", include("document_intake.api.urls")),
    path("intake/", include("document_intake.ui.urls")),
    path("", RedirectView.as_view(pattern_name="document_intake:document-list")),
]
