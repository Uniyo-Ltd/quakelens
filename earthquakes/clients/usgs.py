import json
import os
from datetime import date

import httpx


USGS_QUERY_URL = os.environ.get("USGS_QUERY_URL", "https://earthquake.usgs.gov/fdsnws/event/1/query")
MAX_EVENTS = 20_000
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
REGION = {
    "min_latitude": 32,
    "max_latitude": 42,
    "min_longitude": -125,
    "max_longitude": -114,
}


def fetch_catalogue(params: dict[str, str]):
    with httpx.stream("GET", USGS_QUERY_URL, params=params, timeout=10.0) as response:
        response.raise_for_status()
        if response.status_code == 204 and "eventid" not in params:
            return {"type": "FeatureCollection", "features": []}

        body = bytearray()
        for chunk in response.iter_bytes(chunk_size=64 * 1024):
            if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                raise ValueError("USGS response exceeds the supported size")
            body.extend(chunk)
    return json.loads(body)


def fetch_day(day: date) -> list:
    payload = fetch_catalogue({
        "format": "geojson",
        "eventtype": "earthquake",
        "starttime": f"{day.isoformat()}T00:00:00.000Z",
        "endtime": f"{day.isoformat()}T23:59:59.999Z",
        "minlatitude": str(REGION["min_latitude"]),
        "maxlatitude": str(REGION["max_latitude"]),
        "minlongitude": str(REGION["min_longitude"]),
        "maxlongitude": str(REGION["max_longitude"]),
    })

    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise ValueError("Expected a USGS earthquake collection")

    events = payload.get("features")
    if not isinstance(events, list):
        raise ValueError("USGS response is missing its earthquake list")
    if len(events) >= MAX_EVENTS:
        raise ValueError("USGS result reached its limit; the day may be incomplete")
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict) or metadata.get("count", len(events)) != len(events):
        raise ValueError("USGS event count does not match its collection")

    return events
