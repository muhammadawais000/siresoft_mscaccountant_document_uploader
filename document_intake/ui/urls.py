"""HTMX pages. Mount with ``path("intake/", include("document_intake.ui.urls"))``."""

from django.urls import path

from . import views

app_name = "document_intake"

doc = "documents/<uuid:pk>/"

urlpatterns = [
    # Documents list
    path("", views.document_list, name="document-list"),
    path("upload/", views.upload_documents, name="upload"),
    path("documents/delete/", views.delete_documents, name="delete-documents"),
    # Review page and its pieces
    path(doc, views.document_review, name="document-review"),
    path(doc + "status/", views.review_status, name="review-status"),
    path(doc + "viewer/", views.viewer, name="viewer"),
    path(doc + "pages/<int:number>.png", views.page_image, name="page-image"),
    path(doc + "file/", views.original_file, name="file"),
    path(doc + "export.csv", views.export_csv, name="export-csv"),
    path(doc + "activity/", views.activity_list, name="activity"),
    # Edits: answer with the re-rendered review panels
    path(doc + "fields/<uuid:field_id>/", views.edit_field, name="edit-field"),
    path(doc + "lines/add/", views.add_line, name="add-line"),
    path(doc + "lines/<uuid:line_id>/", views.edit_line, name="edit-line"),
    path(doc + "lines/<uuid:line_id>/delete/", views.delete_line, name="delete-line"),
    path(doc + "issues/<uuid:issue_id>/", views.resolve_issue, name="resolve-issue"),
    # Status changes: answer by reloading the page
    path(doc + "approve/", views.approve, name="approve"),
    path(doc + "reject/", views.reject, name="reject"),
    path(doc + "clarify/", views.clarify, name="clarify"),
    path(doc + "resume-review/", views.resume_review, name="resume-review"),
    path(doc + "mark-duplicate/", views.mark_duplicate, name="mark-duplicate"),
    path(doc + "reprocess/", views.reprocess, name="reprocess"),
    path(doc + "delete/", views.delete_document, name="delete-document"),
]
