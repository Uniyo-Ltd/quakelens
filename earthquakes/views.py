from django.db import InterfaceError, OperationalError
from rest_framework import generics
from rest_framework.decorators import api_view
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response
from rest_framework.utils.urls import replace_query_param
from rest_framework.views import exception_handler

from earthquakes.serializers import EarthquakeSerializer, ListFilters, TrendFilters, read_filters
from earthquakes.services.trends import IncompleteCoverage, regional_earthquakes, weekly_trend


def api_exception_handler(exc, context):
    if isinstance(exc, (InterfaceError, OperationalError)):
        return Response({"code": "database_unavailable", "message": "The database is unavailable."}, status=503)
    response = exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, ValidationError):
        response.data = {
            "code": "invalid_parameters", "message": "Check the query parameters.", "details": response.data,
        }
    else:
        detail = response.data["detail"]
        response.data = {"code": detail.code, "message": str(detail)}
    return response


class EarthquakePagination(CursorPagination):
    ordering = "-id"
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100

    def paginate_queryset(self, queryset, request, view=None):
        page = super().paginate_queryset(queryset, request, view)
        # Implicit UTC dates must stay fixed if the next page is opened after midnight.
        for key in ("start", "end"):
            if key not in request.query_params:
                self.base_url = replace_query_param(self.base_url, key, view.filters[key].isoformat())
        return page

    def decode_cursor(self, request):
        # DRF decodes the token; our unique integer ordering needs only a row ID.
        try:
            cursor = super().decode_cursor(request)
        except NotFound:
            raise ValidationError({"cursor": "Invalid cursor."}) from None
        if cursor is not None:
            position = cursor.position or ""
            if cursor.offset or not position.isascii() or not position.isdecimal():
                raise ValidationError({"cursor": "Invalid cursor."})
            if not 1 <= int(position) <= 2**63 - 1:
                raise ValidationError({"cursor": "Invalid cursor."})
        return cursor


class EarthquakeList(generics.ListAPIView):
    serializer_class = EarthquakeSerializer
    pagination_class = EarthquakePagination

    def get_queryset(self):
        self.filters = read_filters(ListFilters, self.request.query_params)
        return regional_earthquakes(self.filters.get("min_magnitude")).filter(
            occurred_at__gte=self.filters["start"], occurred_at__lt=self.filters["end"],
        )


class EarthquakeDetail(generics.RetrieveAPIView):
    serializer_class = EarthquakeSerializer
    lookup_field = "external_id"

    def get_queryset(self):
        if self.request.query_params:
            raise ValidationError({key: "Unknown parameter." for key in self.request.query_params})
        external_id = self.kwargs[self.lookup_field]
        if len(external_id) > 128 or "\x00" in external_id:
            raise NotFound()
        return regional_earthquakes()


@api_view(["GET", "HEAD"])
def trend_detail(request):
    filters = read_filters(TrendFilters, request.query_params)
    try:
        return Response(weekly_trend(filters.get("min_magnitude")))
    except IncompleteCoverage as exc:
        return Response({
            "code": "coverage_incomplete",
            "message": "Some days have no successful import.",
            "details": {"missing_days": exc.missing_days},
        }, status=503)
