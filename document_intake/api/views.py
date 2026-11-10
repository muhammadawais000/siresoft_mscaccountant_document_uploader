"""API views. Views stay thin; business logic lives in ``document_intake.services``."""

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    """Liveness probe for load balancers. Reveals nothing about documents."""

    permission_classes = [AllowAny]
    authentication_classes: list[type] = []

    @extend_schema(
        responses=inline_serializer("Health", {"status": serializers.CharField()}),
    )
    def get(self, request: Request) -> Response:
        return Response({"status": "ok"})
