#!/usr/bin/env python3
"""Local MITM proxy: serve files.pythonhosted.org paths from aliyun pypi mirror.

uv sync --frozen fetches the absolute URLs pinned in uv.lock
(https://files.pythonhosted.org/packages/...) and ignores index mirrors, so on
a slow international link we point that hostname at 127.0.0.1 via /etc/hosts
and translate the same content-addressed paths from aliyun, which keeps the
identical /packages/<hash>/ layout.
"""
import http.client
import http.server
import ssl
import sys

UPSTREAM_HOST = "mirrors.aliyun.com"
UPSTREAM_PREFIX = "/pypi"
LISTEN_PORT = 443

HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate",
               "proxy-authorization", "te", "trailers", "transfer-encoding",
               "upgrade"}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _proxy(self):
        upstream_path = UPSTREAM_PREFIX + self.path
        body = None
        if "Content-Length" in self.headers:
            body = self.rfile.read(int(self.headers["Content-Length"]))

        conn = http.client.HTTPSConnection(UPSTREAM_HOST, timeout=120)
        fwd_headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in HOP_HEADERS}
        fwd_headers["Host"] = UPSTREAM_HOST
        conn.request(self.command, upstream_path, body=body, headers=fwd_headers)
        resp = conn.getresponse()

        self.send_response(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() in HOP_HEADERS:
                continue
            self.send_header(k, v)
        self.end_headers()

        if self.command != "HEAD":
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
        conn.close()

    def do_GET(self):
        self._proxy()

    def do_HEAD(self):
        self._proxy()

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain("/etc/pypi-proxy/cert.pem", "/etc/pypi-proxy/key.pem")
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    print(f"listening on 127.0.0.1:{LISTEN_PORT} -> https://{UPSTREAM_HOST}{UPSTREAM_PREFIX}", flush=True)
    server.serve_forever()
