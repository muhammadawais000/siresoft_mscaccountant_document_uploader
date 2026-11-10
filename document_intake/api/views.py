"""API views. Views stay thin; business logic lives in ``document_intake.services``."""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import BaseThrottle
from rest_framework.views import APIView

from document_intake.models import Document
from document_intake.services import access, query, upload

from .permissions import configured_permission_classes
from .serializers import DocumentFilterSerializer, DocumentSerializer, DocumentUploadSerializer
from .throttles import UploadRateThrottle


class HealthView(APIView):
    """Liveness probe for load balancers. Reveals nothing about documents."""

    permission_classes = [AllowAny]
    authentication_classes: list[type] = []

    @extend_schema(
        responses=inline_serializer("Health", {"status": serializers.CharField()}),
    )
    def get(self, request: Request) -> Response:
        return Response({"status": "ok"})


class IntakeAPIView:
    """Mixin: permission classes come from ``DOCUMENT_INTAKE["PERMISSION_CLASSES"]``."""

    def get_permissions(self) -> list[BasePermission]:
        return [cls() for cls in configured_permission_classes()]


@extend_schema(tags=["documents"])
class DocumentViewSet(
    IntakeAPIView,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet[Document],
):
    """Upload, list and inspect documents. Poll ``retrieve`` for status changes."""

    serializer_class = DocumentSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self) -> QuerySet[Document]:
        documents = access.visible_documents(self.request.user)
        if self.action != "list":
            return documents
        filters = DocumentFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        return query.filter_documents(documents, filters.validated_data)

    def get_throttles(self) -> list[BaseThrottle]:
        if self.action == "create":
            return [UploadRateThrottle()]
        return super().get_throttles()

    @extend_schema(parameters=[DocumentFilterSerializer])
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().list(request, *args, **kwargs)

    @extend_schema(
        request={"multipart/form-data": DocumentUploadSerializer},
        responses={
            201: DocumentSerializer,
            400: OpenApiResponse(description="Validation failed (type, size or content)."),
        },
    )
    def create(self, request: Request) -> Response:
        payload = DocumentUploadSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        document = upload.create_document(
            upload=data["file"],
            uploaded_by=request.user,
            doc_type=data["doc_type"],
            owner_reference=data["owner_reference"],
            request_reference=data["request_reference"],
        )
        return Response(DocumentSerializer(document).data, status=status.HTTP_201_CREATED)
