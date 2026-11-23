from unittest.mock import patch

from django.db import OperationalError
from django.test import TestCase
from django.urls import reverse


class HealthTests(TestCase):
    def test_reports_ready_when_postgresql_responds(self):
        response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_reports_unavailable_when_postgresql_cannot_be_reached(self):
        with patch("config.views.connection.cursor", side_effect=OperationalError):
            response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
