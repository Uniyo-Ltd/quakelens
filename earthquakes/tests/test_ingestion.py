from datetime import date
from unittest.mock import patch

import httpx
from django.test import TestCase

from earthquakes.models import Earthquake, ImportWindow
from earthquakes.services import ingestion
from earthquakes.services.ingestion import save_batch, save_earthquake


def sample_quake(updated, magnitude):
    return {
        "type": "Feature",
        "id": "test-quake",
        "properties": {
            "type": "earthquake",
            "time": 1000,
            "updated": updated,
            "mag": magnitude,
        },
        "geometry": {"type": "Point", "coordinates": [-118.0, 36.0, 2.0]},
    }


class IngestionTests(TestCase):
    def test_repeat_and_late_messages_keep_one_corrected_quake(self):
        original = sample_quake(updated=2000, magnitude=1.4)
        correction = sample_quake(updated=3000, magnitude=1.6)

        save_earthquake(original)
        save_earthquake(correction)
        save_earthquake(correction)
        save_earthquake(original)

        saved = Earthquake.objects.filter(external_id="test-quake")
        self.assertEqual(saved.count(), 1)
        self.assertEqual(saved.get().magnitude, 1.6)

    def test_failed_batch_rolls_back(self):
        first = sample_quake(updated=2000, magnitude=1.4)
        second = sample_quake(updated=3000, magnitude=1.6)
        del second["id"]

        with self.assertRaises(KeyError):
            save_batch([first, second])

        self.assertFalse(Earthquake.objects.exists())

    def test_successful_empty_day_is_recorded(self):
        day = date(2026, 11, 20)
        with patch("earthquakes.clients.usgs.fetch_day", return_value=[]) as fetch:
            ingestion.import_day(day)

        fetch.assert_called_once_with(day)
        window = ImportWindow.objects.get(day=day)
        self.assertEqual(window.last_attempt_status, ImportWindow.AttemptStatus.SUCCEEDED)
        self.assertEqual(window.accepted_count, 0)
        self.assertIsNotNone(window.last_success_at)

    def test_failure_then_replay_then_bad_refresh_preserves_verified_data(self):
        day = date(1970, 1, 1)
        good = sample_quake(updated=2000, magnitude=-0.3)
        with patch("earthquakes.clients.usgs.fetch_day", side_effect=httpx.ReadTimeout("timeout")):
            with self.assertRaises(httpx.ReadTimeout):
                ingestion.import_day(day)
        window = ImportWindow.objects.get(day=day)
        self.assertEqual(window.last_attempt_status, ImportWindow.AttemptStatus.FAILED)
        self.assertIsNone(window.last_success_at)

        with patch("earthquakes.clients.usgs.fetch_day", return_value=[good]):
            self.assertEqual(ingestion.import_day(day), 1)
            self.assertEqual(ingestion.import_day(day), 1)
        window.refresh_from_db()
        last_success = window.last_success_at
        self.assertIsNotNone(last_success)
        self.assertEqual(window.accepted_count, 1)
        self.assertIsNone(Earthquake.objects.get().place)

        correction = sample_quake(updated=3000, magnitude=2.0)
        bad = sample_quake(updated=4000, magnitude=float("nan"))
        with patch("earthquakes.clients.usgs.fetch_day", return_value=[correction, bad]):
            with self.assertRaises(ingestion.InvalidEarthquake):
                ingestion.import_day(day)
        window.refresh_from_db()
        self.assertEqual(window.last_attempt_status, ImportWindow.AttemptStatus.FAILED)
        self.assertEqual(window.last_success_at, last_success)
        self.assertEqual(window.accepted_count, 0)
        self.assertEqual(window.rejected_count, 1)
        self.assertEqual(Earthquake.objects.get().magnitude, -0.3)

    def test_day_and_area_boundaries(self):
        day = date(1970, 1, 1)
        quake = sample_quake(updated=2000, magnitude=None)
        quake["geometry"]["coordinates"] = [-125, 42, 2]
        with patch("earthquakes.clients.usgs.fetch_day", return_value=[quake]):
            for time in (0, 86_399_999):
                quake["properties"]["time"] = time
                self.assertEqual(ingestion.import_day(day), 1)
            self.assertIsNone(Earthquake.objects.get().magnitude)
            for time, longitude in [(86_400_000, -125), (-1, -125), (0, -125.01)]:
                with self.subTest(time=time, longitude=longitude):
                    quake["properties"]["time"] = time
                    quake["geometry"]["coordinates"][0] = longitude
                    with self.assertRaises(ingestion.InvalidEarthquake):
                        ingestion.import_day(day)

    def test_superseded_attempt_cannot_overwrite_later_result(self):
        day = date(1970, 1, 1)

        def finish_newer_attempt_before_returning(day):
            with patch("earthquakes.clients.usgs.fetch_day", return_value=[]):
                ingestion.import_day(day)
            return [sample_quake(updated=2000, magnitude=1.0)]

        with patch("earthquakes.clients.usgs.fetch_day", side_effect=finish_newer_attempt_before_returning):
            self.assertIsNone(ingestion.import_day(day))
        self.assertFalse(Earthquake.objects.exists())
        window = ImportWindow.objects.get(day=day)
        self.assertEqual(window.last_attempt_status, ImportWindow.AttemptStatus.SUCCEEDED)
        self.assertEqual(window.accepted_count, 0)
