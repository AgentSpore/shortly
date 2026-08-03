"""Shortly - URL shortener with SQLite storage (stdlib only)."""
import json, os, secrets, sqlite3, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.request import urlopen

DB = "links.db"

def _db():
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS links (short TEXT PRIMARY KEY, long TEXT, hits INTEGER DEFAULT 0)")
    return c

class H(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/health":
            return self._json(200, {"status": "ok"})
        if u.path == "/":
            return self._json(200, {"service": "shortly", "example": "POST /shorten {\"url\": \"https://example.com\"}"})
        if u.path.startswith("/r/"):
            short = u.path[3:]
            c = _db()
            row = c.execute("SELECT long FROM links WHERE short=?", (short,)).fetchone()
            if not row:
                return self._json(404, {"error": "not found"})
            c.execute("UPDATE links SET hits=hits+1 WHERE short=?", (short,))
            c.commit()
            self.send_response(302)
            self.send_header("Location", row[0])
            self.end_headers()
            return
        return self._json(404, {"error": "not found"})
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/shorten":
            return self._json(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length).decode() or "{}")
        except Exception:
            return self._json(400, {"error": "bad json"})
        long_url = body.get("url", "")
        if not long_url.startswith(("http://", "https://")):
            return self._json(400, {"error": "url must be http(s)"})
        short = secrets.token_urlsafe(5)
        c = _db()
        c.execute("INSERT OR IGNORE INTO links (short, long) VALUES (?,?)", (short, long_url))
        c.commit()
        return self._json(201, {"short": short, "url": long_url})
    def log_message(self, *a):
        pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    HTTPServer(("0.0.0.0", port), H).serve_forever()
