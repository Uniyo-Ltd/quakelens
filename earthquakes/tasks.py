import logging
import math
import random
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from functools import lru_cache

import httpx
from celery import shared_task
from django.conf import settings
from django.db import OperationalError
from django.utils import timezone
from redis import Redis

from earthquakes.clients.usgs import REGION
from earthquakes.models import ImportWindow
from earthquakes.services.ingestion import import_day

logger = logging.getLogger(__name__)
HISTORY_DAYS = 30
REFRESH_DAYS = 14
MAX_COUNTDOWN = 600  # Long pauses are handled by Beat; keep distant ETA jobs out of workers.
SOURCE_PAUSE_KEY = "quakelens:usgs:retry-after"


@lru_cache(maxsize=1)
def broker_cache():
    return Redis.from_url(settings.CELERY_BROKER_URL, socket_timeout=3, socket_connect_timeout=3)


def retry_after_seconds(header):
    if not header:
        return None
    header = header.strip()
    if header.isascii() and header.isdecimal():
        digits = header.lstrip("0")
        # A delay beyond representable calendar dates must still suppress early requests.
        if len(digits) > 15:
            return math.inf
        return int(digits or "0")
    try:
        deadline = parsedate_to_datetime(header)
        if deadline.tzinfo is None:
            return None
        return max(0, math.ceil((deadline - timezone.now()).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


def pause_source(seconds):
    # Redis ZADD GT atomically keeps the latest deadline if workers are rate limited together.
    deadline = timezone.now().timestamp() + seconds
    broker_cache().zadd(SOURCE_PAUSE_KEY, {"usgs": deadline}, gt=True)


def source_wait():
    deadline = broker_cache().zscore(SOURCE_PAUSE_KEY, "usgs")
    if deadline is None:
        return 0
    if math.isinf(deadline):
        return math.inf
    return max(0, math.ceil(deadline - timezone.now().timestamp()))


def backoff(retries):
    base = min(120, 5 * 2**retries)
    return base + random.randint(0, base)


@shared_task(bind=True, acks_late=True, max_retries=3, soft_time_limit=150, time_limit=180)
def import_earthquake_day(self, iso_day):
    day = date.fromisoformat(iso_day)
    remaining = source_wait()
    if remaining > MAX_COUNTDOWN:
        logger.warning("USGS cooldown active; skipping %s until a later scheduled refresh", day)
        return None
    if remaining:
        raise self.retry(countdown=remaining)

    try:
        return import_day(day)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status not in {429, 500, 502, 503, 504}:
            raise
        delay = None
        if status == 429:
            delay = retry_after_seconds(exc.response.headers.get("Retry-After"))
        if delay is None:
            delay = backoff(self.request.retries)
        if status == 429 and delay:
            pause_source(delay)
        if delay > MAX_COUNTDOWN:
            logger.warning("USGS requested a long cooldown for %s; scheduling will resume after it", day)
            raise
        raise self.retry(exc=exc, countdown=delay)
    except (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError, OperationalError) as exc:
        raise self.retry(exc=exc, countdown=backoff(self.request.retries))


@shared_task(acks_late=True, soft_time_limit=45, time_limit=60)
def refresh_catalogue():
    if source_wait():
        logger.info("USGS cooldown active; deferring catalogue refresh")
        return 0
    today = timezone.now().date()
    verified = set(ImportWindow.objects.filter(
        **REGION, day__gte=today - timedelta(days=HISTORY_DAYS), day__lt=today,
        last_success_at__isnull=False,
    ).values_list("day", flat=True))
    queued = 0
    for age in range(1, HISTORY_DAYS + 1):
        day = today - timedelta(days=age)
        if age <= REFRESH_DAYS or day not in verified:
            import_earthquake_day.delay(day.isoformat())
            queued += 1
    logger.info("Queued %s daily USGS imports through %s", queued, today - timedelta(days=1))
    return queued
