from datetime import UTC, datetime, time, timedelta
from math import isfinite

from django.utils import timezone
from rest_framework import serializers

from earthquakes.models import Earthquake


class EarthquakeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Earthquake
        fields = (
            "external_id", "occurred_at", "source_updated_at", "magnitude",
            "latitude", "longitude", "place",
        )


class TimeFilter(serializers.DateTimeField):
    def to_internal_value(self, data):
        if isinstance(data, str) and len(data) == 10:
            data += "T00:00:00Z"
        return super().to_internal_value(data)

    def enforce_timezone(self, value):
        if timezone.is_naive(value):
            raise serializers.ValidationError("Include a UTC offset or use a date (YYYY-MM-DD).")
        return super().enforce_timezone(value)


class TrendFilters(serializers.Serializer):
    min_magnitude = serializers.FloatField(required=False)

    def validate_min_magnitude(self, value):
        if not isfinite(value):
            raise serializers.ValidationError("Enter a finite magnitude.")
        return value


class ListFilters(TrendFilters):
    start = TimeFilter(required=False)
    end = TimeFilter(required=False)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=100)
    cursor = serializers.CharField(required=False, max_length=256)

    def validate(self, values):
        max_span = timedelta(days=30)
        start, end = values.get("start"), values.get("end")
        try:
            if end is None:
                if start is None:
                    end = datetime.combine(timezone.now().date(), time.min, tzinfo=UTC)
                else:
                    end = start + max_span
            if start is None:
                start = end - max_span
        except OverflowError:
            raise serializers.ValidationError({"end": "The date range is outside supported bounds."}) from None
        if not timedelta(0) < end - start <= max_span:
            raise serializers.ValidationError({"end": "The end must follow the start by at most 30 days."})
        return {**values, "start": start, "end": end}


def read_filters(filter_class, params):
    serializer = filter_class(data=params.dict())
    errors = {}
    for key, values in params.lists():
        if key not in serializer.fields:
            errors[key] = "Unknown parameter."
        elif len(values) != 1:
            errors[key] = "Supply this parameter once."
    if errors:
        raise serializers.ValidationError(errors)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data
