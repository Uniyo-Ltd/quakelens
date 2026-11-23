"""Local catalogue for the isolated smoke test. One known earthquake per UTC day."""

import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class Catalogue(BaseHTTPRequestHandler):
    def reply(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/offline":
            return self.reply(404, {})
        self.server.offline = True
        self.reply(200, {})

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/health":
            return self.reply(200, {})
        if url.path != "/query":
            return self.reply(404, {})
        if self.server.offline:
            return self.reply(503, {"error": "simulated source outage"})
        start = datetime.fromisoformat(parse_qs(url.query)["starttime"][0])
        timestamp = int(start.timestamp() * 1000)
        event = {
            "type": "Feature",
            "id": f"smoke-{start.date()}",
            "properties": {"type": "earthquake", "time": timestamp, "updated": timestamp,
                           "mag": 2.5, "place": "Smoke fixture"},
            "geometry": {"type": "Point", "coordinates": [-118, 36, 2]},
        }
        self.reply(200, {"type": "FeatureCollection", "features": [event]})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Catalogue)
    server.offline = False
    server.serve_forever()
