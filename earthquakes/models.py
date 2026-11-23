from django.db import models


class Earthquake(models.Model):
    external_id = models.CharField(max_length=128, unique=True)
    occurred_at = models.DateTimeField(db_index=True)
    source_updated_at = models.DateTimeField()
    magnitude = models.FloatField(null=True, blank=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    place = models.TextField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(latitude__gte=-90, latitude__lte=90),
                name="earthquake_latitude_in_range",
            ),
            models.CheckConstraint(
                condition=models.Q(longitude__gte=-180, longitude__lte=180),
                name="earthquake_longitude_in_range",
            ),
        ]


class ImportWindow(models.Model):
    """Import state for one complete UTC day and geographic rectangle."""

    class AttemptStatus(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    day = models.DateField()
    min_latitude = models.FloatField()
    max_latitude = models.FloatField()
    min_longitude = models.FloatField()
    max_longitude = models.FloatField()

    last_attempt_started_at = models.DateTimeField()
    last_attempt_status = models.CharField(max_length=9, choices=AttemptStatus)
    last_success_at = models.DateTimeField(null=True, blank=True)
    accepted_count = models.PositiveIntegerField(default=0)
    rejected_count = models.PositiveIntegerField(default=0)
    failure_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "day",
                    "min_latitude",
                    "max_latitude",
                    "min_longitude",
                    "max_longitude",
                ],
                name="unique_import_day_and_bounds",
            ),
        ]
