# Shortly

A URL shortener with SQLite storage, zero external dependencies. Health at
`/health`. Shorten: `POST /shorten {"url": "https://..."}` -> `{"short": "abc12"}`.
Redirect: `GET /r/abc12`. Run: `python main.py`.
