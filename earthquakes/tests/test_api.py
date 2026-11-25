from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from earthquakes.clients.usgs import REGION
from earthquakes.models import Earthquake, ImportWindow
from earthquakes.services.trends import count_change


class TrendArithmeticTests(SimpleTestCase):
    def test_counts_change_relative_to_the_older_week(self):
        for previous, current, difference, percent in (
            (2, 3, 1, 50.0), (3, 2, -1, -33.33), (5, 0, -5, -100.0),
            (0, 3, 3, None), (0, 0, 0, None),
        ):
            with self.subTest(previous=previous, current=current):
                change = count_change(previous, current)
                self.assertEqual(change["difference"], difference)
                self.assertEqual(change["percentage_change"], percent)
                self.assertEqual(bool(change["percentage_change_reason"]), previous == 0)


class EarthquakeAPITests(TestCase):
    end = datetime(2026, 11, 23, tzinfo=UTC)

    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=self.end + timedelta(hours=12))
        clock.start()
        self.addCleanup(clock.stop)

    def quake(self, external_id, occurred_at, magnitude=1.0, latitude=36):
        return Earthquake.objects.create(
            external_id=external_id, occurred_at=occurred_at, source_updated_at=self.end,
            magnitude=magnitude, latitude=latitude, longitude=-120,
        )

    def coverage(self):
        days = []
        for offset in range(1, 15):
            day = self.end.date() - timedelta(days=offset)
            days.append(ImportWindow(
                **REGION, day=day, last_success_at=self.end - timedelta(hours=offset),
                last_attempt_started_at=self.end, last_attempt_status=ImportWindow.AttemptStatus.SUCCEEDED,
            ))
        ImportWindow.objects.bulk_create(days)

    def test_filtering_and_bounded_pagination_use_one_query_per_page(self):
        day = self.end - timedelta(days=1)
        self.quake("outside-start", day - timedelta(microseconds=1))
        first = self.quake("at-start", day)
        second = self.quake("later", day + timedelta(hours=3), magnitude=2.0)
        self.quake("unknown-magnitude", day, magnitude=None)
        self.quake("below-threshold", day, magnitude=-1.0)
        self.quake("outside-region", day, latitude=43)
        self.quake("at-end", self.end)

        params = {"start": "2026-11-22", "end": "2026-11-23", "min_magnitude": 0, "page_size": 1}
        with self.assertNumQueries(1):
            page = self.client.get(reverse("earthquake-list"), params).json()
        self.assertEqual([row["external_id"] for row in page["results"]], [second.external_id])
        self.assertIsNone(page["previous"])
        self.quake("new-arrival", day)
        with self.assertNumQueries(1):
            next_page = self.client.get(page["next"]).json()
        self.assertEqual([row["external_id"] for row in next_page["results"]], [first.external_id])
        self.assertIsNone(next_page["next"])
        self.assertEqual(self.client.get(next_page["previous"]).json()["results"], page["results"])

    def test_invalid_queries_return_a_readable_error(self):
        url = reverse("earthquake-list")
        for query in (
            "min_magnitude=nan", "min_magnitude=inf", "min_magnitude=", "start=2026-13-01",
            "start=2026-11-01T12:30:00", "start=2026-11-23&end=2026-11-22",
            "start=2026-01-01&end=2026-11-23", "page_size=0", "page_size=101",
            "cursor=garbage", "cursor=cD1hYmM%3D", "other=5", "page_size=1&page_size=2",
        ):
            with self.subTest(query=query):
                response = self.client.get(url + "?" + query)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(response.json()["code"], "invalid_parameters")
                self.assertTrue(response.json()["details"])

    def test_invalid_page_size_retry_opens_first_page_with_same_filters(self):
        day = self.end - timedelta(days=1)
        self.quake("older", day, magnitude=2)
        self.quake("newest", day, magnitude=3)
        self.quake("below-magnitude", day, magnitude=1)
        self.quake("before-window", day - timedelta(days=1), magnitude=3)

        url = reverse("earthquake-list")
        filters = {"start": "2026-11-22", "end": "2026-11-23", "min_magnitude": 2}
        first = self.client.get(url, {**filters, "page_size": 1}).json()
        cursor = parse_qs(urlsplit(first["next"]).query)["cursor"][0]

        invalid = self.client.get(url, {**filters, "page_size": 0, "cursor": cursor})
        self.assertEqual(invalid.status_code, 400)
        error = invalid.json()
        self.assertIn("page_size", error["details"])
        self.assertEqual(urlsplit(error["retry_url"]).netloc, "testserver")
        retry = self.client.get(error["retry_url"])
        self.assertEqual(retry.status_code, 200)
        page = retry.json()
        self.assertIsNone(page["previous"])
        self.assertEqual([row["external_id"] for row in page["results"]], ["newest", "older"])

        invalid_filter = self.client.get(url, {"page_size": 0, "min_magnitude": "nan"})
        self.assertEqual(invalid_filter.status_code, 400)
        self.assertNotIn("retry_url", invalid_filter.json())

    def test_default_cursor_window_stays_fixed_across_midnight(self):
        self.quake("earliest", self.end - timedelta(days=30))
        self.quake("later", self.end - timedelta(days=1))
        page = self.client.get(reverse("earthquake-list"), {"page_size": 1}).json()
        self.assertEqual(page["results"][0]["external_id"], "later")
        with patch("django.utils.timezone.now", return_value=self.end + timedelta(days=1)):
            following = self.client.get(page["next"]).json()
        self.assertEqual(following["results"][0]["external_id"], "earliest")

    def test_public_detail_and_mutation_policy(self):
        quake = self.quake("us-test", self.end - timedelta(hours=1))
        detail = reverse("earthquake-detail", args=[quake.external_id])
        self.assertEqual(self.client.get(detail).json()["external_id"], quake.external_id)
        invalid = self.client.get(detail, {"min_magnitude": 1})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["code"], "invalid_parameters")
        missing = self.client.get(reverse("earthquake-detail", args=["missing"]))
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["code"], "not_found")
        for method, url in (("post", reverse("earthquake-list")), ("put", detail),
                            ("patch", detail), ("delete", detail), ("post", reverse("earthquake-trend"))):
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response.json()["code"], "method_not_allowed")
        self.assertTrue(Earthquake.objects.filter(pk=quake.pk).exists())

    def test_exact_week_boundaries_and_oldest_verified_freshness(self):
        start = self.end - timedelta(days=14)
        midpoint = self.end - timedelta(days=7)
        self.coverage()
        ImportWindow.objects.filter(day=start.date()).update(last_attempt_status=ImportWindow.AttemptStatus.FAILED)
        self.quake("before-window", start - timedelta(microseconds=1))
        self.quake("at-start", start)
        self.quake("before-midnight", midpoint - timedelta(microseconds=1), magnitude=2)
        self.quake("at-midpoint", midpoint, magnitude=2)
        self.quake("before-end", self.end - timedelta(microseconds=1), magnitude=None)
        self.quake("after-window", self.end)
        self.quake("outside-region", midpoint, latitude=45)
        with self.assertNumQueries(2):
            response = self.client.get(reverse("earthquake-trend"))
        self.assertEqual(response.status_code, 200)
        trend = response.json()
        self.assertEqual((trend["previous_count"], trend["current_count"]), (2, 2))
        self.assertEqual(trend["previous_period"]["end"], trend["current_period"]["start"])
        self.assertEqual(trend["current_period"]["end"], "2026-11-23T00:00:00Z")
        self.assertEqual(trend["freshness"]["oldest_success_at"], "2026-11-22T10:00:00Z")
        self.assertEqual(trend["freshness"]["failed_refresh_days"], [start.date().isoformat()])
        filtered = self.client.get(reverse("earthquake-trend"), {"min_magnitude": 2}).json()
        self.assertEqual((filtered["previous_count"], filtered["current_count"]), (1, 1))

    def test_missing_or_first_failed_day_is_unknown_even_if_another_area_succeeded(self):
        self.coverage()
        missing_day = (self.end - timedelta(days=1)).date()
        other_area_day = missing_day - timedelta(days=1)
        ImportWindow.objects.filter(day=missing_day).update(last_success_at=None, last_attempt_status="failed")
        ImportWindow.objects.filter(day=other_area_day).update(min_latitude=0)
        with self.assertNumQueries(1):
            response = self.client.get(reverse("earthquake-trend"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "coverage_incomplete")
        self.assertEqual(response.json()["details"]["missing_days"],
                         [other_area_day.isoformat(), missing_day.isoformat()])
        self.assertNotIn("current_count", response.json())

    def test_verified_empty_days_have_zero_counts(self):
        self.coverage()
        trend = self.client.get(reverse("earthquake-trend")).json()
        self.assertEqual((trend["previous_count"], trend["current_count"]), (0, 0))
        self.assertIsNone(trend["percentage_change"])
        self.assertTrue(trend["percentage_change_reason"])
