from datetime import UTC, datetime, timedelta
from unittest.mock import call, patch

import httpx
from celery.exceptions import Retry
from django.test import SimpleTestCase, TestCase

from earthquakes import tasks
from earthquakes.clients.usgs import REGION
from earthquakes.models import ImportWindow


class RefreshTests(TestCase):
    @patch("earthquakes.tasks.timezone.now")
    @patch("earthquakes.tasks.source_wait", return_value=0)
    @patch("earthquakes.tasks.import_earthquake_day.delay")
    def test_startup_and_refresh_queue_completed_days(self, dispatch, wait, now):
        now.return_value = datetime(2024, 5, 1, 12, tzinfo=UTC)
        today = now.return_value.date()
        days = [today - timedelta(days=age) for age in range(1, 31)]

        self.assertEqual(tasks.refresh_catalogue(), 30)
        self.assertEqual(dispatch.call_args_list, [call(day.isoformat()) for day in days])

        for day in days[:-1]:
            ImportWindow.objects.create(
                day=day, **REGION, last_attempt_status=ImportWindow.AttemptStatus.SUCCEEDED,
                last_attempt_started_at=now.return_value, last_success_at=now.return_value,
            )
        dispatch.reset_mock()
        self.assertEqual(tasks.refresh_catalogue(), 15)
        self.assertEqual(dispatch.call_args_list, [call(day.isoformat()) for day in days[:14] + days[-1:]])

    @patch("earthquakes.tasks.source_wait", return_value=3600)
    @patch("earthquakes.tasks.import_earthquake_day.delay")
    def test_source_cooldown_applies_to_independent_scheduled_refreshes(self, dispatch, wait):
        self.assertEqual(tasks.refresh_catalogue(), 0)
        dispatch.assert_not_called()


def status_error(status, headers=None):
    response = httpx.Response(status, headers=headers, request=httpx.Request("GET", "https://example.org/"))
    return httpx.HTTPStatusError("USGS error", request=response.request, response=response)


class ImportTaskTests(SimpleTestCase):
    def test_retry_after_parses_delay_or_date(self):
        now = datetime(2024, 5, 1, 12, tzinfo=UTC)
        with patch("earthquakes.tasks.timezone.now", return_value=now):
            self.assertEqual(tasks.retry_after_seconds("120"), 120)
            self.assertEqual(tasks.retry_after_seconds("Wed, 01 May 2024 12:02:00 GMT"), 120)
            self.assertIsNone(tasks.retry_after_seconds("soon"))
            self.assertIsNone(tasks.retry_after_seconds("-1"))

    @patch("earthquakes.tasks.source_wait", return_value=0)
    @patch("earthquakes.tasks.import_day")
    @patch("earthquakes.tasks.pause_source")
    def test_rate_limits_honour_wait_and_do_not_hold_a_worker(self, pause, import_day, wait):
        task = tasks.import_earthquake_day
        for seconds in (125, 3600):
            with self.subTest(seconds=seconds), patch.object(task, "retry", side_effect=Retry()) as retry:
                exc = status_error(429, {"Retry-After": str(seconds)})
                import_day.side_effect = exc
                expected_error = Retry if seconds <= tasks.MAX_COUNTDOWN else httpx.HTTPStatusError
                with self.assertRaises(expected_error):
                    task.run("2024-04-30")
                pause.assert_called_with(seconds)
                if seconds <= tasks.MAX_COUNTDOWN:
                    retry.assert_called_once_with(exc=exc, countdown=seconds)
                else:
                    retry.assert_not_called()

    @patch("earthquakes.tasks.source_wait", return_value=0)
    @patch("earthquakes.tasks.import_day")
    def test_only_transient_source_failures_retry(self, import_day, wait):
        task = tasks.import_earthquake_day
        transient = [httpx.ReadTimeout("slow"), status_error(503)]
        permanent = [ValueError("malformed data"), status_error(400)]
        for exc in transient + permanent:
            with self.subTest(error=exc), patch.object(task, "retry", side_effect=Retry()) as retry:
                import_day.side_effect = exc
                with self.assertRaises(Retry if exc in transient else type(exc)):
                    task.run("2024-04-30")
                if exc in transient:
                    self.assertGreaterEqual(retry.call_args.kwargs["countdown"], 5)
                else:
                    retry.assert_not_called()
