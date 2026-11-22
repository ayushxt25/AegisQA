"""Fixed development fixture; not a production target server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


_BROWSER_PAGE = b"""<!doctype html><title>Controlled Fixture</title><p id='result'>Clicks: 0</p><button id='increment'>Increment</button><script>document.getElementById('increment').addEventListener('click',function(){document.getElementById('result').textContent='Clicks: 1';});</script>"""


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/allowed":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"fixture-ok")
        elif self.path == "/browser":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(_BROWSER_PAGE)))
            self.end_headers()
            self.wfile.write(_BROWSER_PAGE)
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://example.invalid/escaped")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return


ThreadingHTTPServer(("0.0.0.0", 8080), FixtureHandler).serve_forever()
