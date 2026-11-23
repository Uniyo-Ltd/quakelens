from contextlib import nullcontext
from datetime import date
from unittest.mock import patch

import httpx
from django.test import SimpleTestCase

from earthquakes.clients import usgs


class DayQueryTests(SimpleTestCase):
    def test_no_content_is_a_successful_empty_area_query(self):
        request = httpx.Request("GET", usgs.USGS_QUERY_URL)
        with patch.object(httpx, "stream", return_value=nullcontext(httpx.Response(204, request=request))) as get:
            self.assertEqual(usgs.fetch_day(date(2026, 11, 20)), [])
        params = get.call_args.kwargs["params"]
        self.assertEqual(params["starttime"], "2026-11-20T00:00:00.000Z")
        self.assertEqual(params["endtime"], "2026-11-20T23:59:59.999Z")
        self.assertEqual(params["eventtype"], "earthquake")

    def test_oversized_reply_is_rejected_before_json_decoding(self):
        response = httpx.Response(200, content=b"x" * 128, request=httpx.Request("GET", usgs.USGS_QUERY_URL))
        with patch.object(httpx, "stream", return_value=nullcontext(response)):
            with patch.object(usgs, "MAX_RESPONSE_BYTES", 64):
                with self.assertRaisesMessage(ValueError, "exceeds the supported size"):
                    usgs.fetch_day(date(2026, 11, 20))

    def test_invalid_and_potentially_incomplete_collections_are_rejected(self):
        for payload in (
            [],
            {"type": "FeatureCollection"},
            {"type": "FeatureCollection", "features": [], "metadata": {"count": 1}},
            {"type": "FeatureCollection", "features": [{}] * usgs.MAX_EVENTS},
        ):
            with self.subTest(keys=list(payload)[:2]):
                with patch.object(usgs, "fetch_catalogue", return_value=payload):
                    with self.assertRaises(ValueError):
                        usgs.fetch_day(date(2026, 11, 20))
