"""UI views: render the page shell; data loads through the REST API."""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView


class DocumentListView(LoginRequiredMixin, TemplateView):
    template_name = "document_intake/document_list.html"
