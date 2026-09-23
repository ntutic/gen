from __future__ import annotations

import base64
import select
import socket
import socketserver
import threading
from dataclasses import dataclass, field
from types import TracebackType
from typing import cast
from urllib.parse import urlsplit

MAX_HEADER_BYTES = 64 * 1024
SOCKET_TIMEOUT_SECONDS = 30
RELAY_IDLE_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class UpstreamProxy:
    host: str
    port: int
    username: str = field(repr=False)
    password: str = field(repr=False)

    @classmethod
    def from_url(cls, url: str, username: str, password: str) -> "UpstreamProxy":
        try:
            parsed = urlsplit(url if "://" in url else f"http://{url}")
            valid = (
                parsed.scheme == "http"
                and parsed.hostname
                and parsed.port
                and parsed.username is None
                and parsed.password is None
                and parsed.path in ("", "/")
                and not parsed.query
                and not parsed.fragment
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(
                "PROXY_SERVER must be an HTTP proxy host with an explicit port; supply credentials separately"
            ) from None
        return cls(
            host=parsed.hostname,
            port=parsed.port,
            username=username,
            password=password,
        )

    @classmethod
    def from_settings(cls, settings) -> "UpstreamProxy":
        import os

        values = {
            name: settings.get(name) or os.environ.get(name)
            for name in ("PROXY_SERVER", "PROXY_USERNAME", "PROXY_PASSWORD")
        }
        if not all(values.values()):
            raise ValueError("Scraping requires PROXY_SERVER, PROXY_USERNAME and PROXY_PASSWORD")
        return cls.from_url(values["PROXY_SERVER"], values["PROXY_USERNAME"], values["PROXY_PASSWORD"])

    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"

    def authorization_header(self) -> bytes:
        token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        return f"Proxy-Authorization: Basic {token}\r\n".encode()


def _read_headers(sock: socket.socket) -> tuple[bytes, bytes]:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(16 * 1024)
        if not chunk:
            raise ConnectionError("connection closed before proxy headers completed")
        data.extend(chunk)
        if len(data) > MAX_HEADER_BYTES:
            raise ValueError("proxy headers exceed the configured limit")
    boundary = data.index(b"\r\n\r\n") + 4
    return bytes(data[:boundary]), bytes(data[boundary:])


def _with_proxy_authorization(headers: bytes, authorization_header: bytes) -> bytes:
    lines = headers.split(b"\r\n")
    request_line = lines[0]
    retained = [
        line
        for line in lines[1:]
        if line and not line.lower().startswith((b"proxy-authorization:", b"connection:", b"proxy-connection:"))
    ]
    return b"\r\n".join([request_line, *retained, authorization_header.rstrip(b"\r\n"), b"Connection: close", b"", b""])


def _relay(left: socket.socket, right: socket.socket) -> None:
    sockets = (left, right)
    while True:
        readable, _, _ = select.select(sockets, (), (), RELAY_IDLE_TIMEOUT_SECONDS)
        if not readable:
            return
        for source in readable:
            data = source.recv(64 * 1024)
            if not data:
                return
            destination = right if source is left else left
            destination.sendall(data)


class _AuthenticatedProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, upstream: UpstreamProxy) -> None:
        self.upstream = upstream
        super().__init__(("127.0.0.1", 0), _AuthenticatedProxyHandler)


class _AuthenticatedProxyHandler(socketserver.BaseRequestHandler):
    server: _AuthenticatedProxyServer
    request: socket.socket

    def handle(self) -> None:
        self.request.settimeout(SOCKET_TIMEOUT_SECONDS)
        response_started = False
        try:
            client_headers, client_remainder = _read_headers(self.request)
            request_line = client_headers.split(b"\r\n", 1)[0]
            method = request_line.split(b" ", 1)[0].upper()
            upstream_config = self.server.upstream
            with socket.create_connection(
                (upstream_config.host, upstream_config.port),
                timeout=SOCKET_TIMEOUT_SECONDS,
            ) as upstream:
                upstream.settimeout(SOCKET_TIMEOUT_SECONDS)
                upstream.sendall(
                    _with_proxy_authorization(
                        client_headers,
                        upstream_config.authorization_header(),
                    )
                )
                if method == b"CONNECT":
                    response_headers, response_remainder = _read_headers(upstream)
                    self.request.sendall(response_headers + response_remainder)
                    response_started = True
                    status_parts = response_headers.split(b"\r\n", 1)[0].split(b" ")
                    if len(status_parts) < 2 or not status_parts[1].startswith(b"2"):
                        return
                    if client_remainder:
                        upstream.sendall(client_remainder)
                else:
                    if client_remainder:
                        upstream.sendall(client_remainder)
                upstream.settimeout(None)
                self.request.settimeout(None)
                _relay(self.request, upstream)
        except (ConnectionError, OSError, ValueError):
            if response_started:
                return
            try:
                self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                pass


class AuthenticatedProxyBridge:
    """Expose an authenticated upstream HTTP proxy on an unauthenticated loopback port."""

    def __init__(self, upstream: UpstreamProxy) -> None:
        self._server = _AuthenticatedProxyServer(upstream)
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.05),
            name="scraping-proxy",
            daemon=True,
        )

    def proxy_string(self) -> str:
        host, port = cast(tuple[str, int], self._server.server_address)
        return f"{host}:{port}"

    def start(self) -> "AuthenticatedProxyBridge":
        self._thread.start()
        return self

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()

    def __enter__(self) -> "AuthenticatedProxyBridge":
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class ProxyMiddleware:
    """Force every Scrapy request through the configured proxy, including redirects.

    Replaces Scrapy's environment-aware proxy middleware: NO_PROXY and per-request
    proxy overrides cannot opt scraping requests out of the project proxy.
    """

    def __init__(self, upstream: UpstreamProxy | None, settings=None):
        self.upstream = upstream
        self.settings = settings

    @classmethod
    def from_crawler(cls, crawler):
        return cls(None, crawler.settings)

    def process_request(self, request, spider=None):
        if request.url.split(":", 1)[0] not in ("http", "https"):
            raise ValueError("Scraping requests must use HTTP or HTTPS through the project proxy")
        if self.upstream is None:
            self.upstream = UpstreamProxy.from_settings(self.settings)
        request.meta["proxy"] = self.upstream.url()
        request.headers["Proxy-Authorization"] = self.upstream.authorization_header().split(b": ", 1)[1].strip()
