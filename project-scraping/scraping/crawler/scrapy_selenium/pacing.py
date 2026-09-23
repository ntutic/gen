"""Pace browser HTTP requests before dispatch, including action-triggered fetches.

Fetch interception sees requests inside HTTPS connections; pacing proxy CONNECT
alone cannot limit HTTP/2 streams or requests reusing an HTTPS tunnel.
"""
from __future__ import annotations

import threading
from urllib.parse import urlsplit

from scraping.crawler.rate_limit import DomainGate


class BrowserPacing:
    def __init__(self, driver, *, gate=None, logger=None):
        self.gate = gate or DomainGate()
        self.logger = logger
        self.error = None
        self.closed = False
        self.lock = threading.Lock()
        self.devtools, self.connection = driver.start_devtools()
        self.connection.add_callback(self.devtools.fetch.RequestPaused, self.request_paused)
        self.connection.add_callback(self.devtools.target.AttachedToTarget, self.extra_target)
        self.execute(self.devtools.network.set_cache_disabled(True))
        self.execute(self.devtools.network.set_bypass_service_worker(True))
        self.execute(self.devtools.fetch.enable())
        # Current helpers operate on one tab. Keep child worker/iframe targets
        # paused rather than let uninstrumented contexts issue unpaced traffic.
        self.execute(self.devtools.target.set_auto_attach(True, True, flatten=True))

    def execute(self, command):
        # Selenium's websocket command counter is shared by callback threads.
        with self.lock:
            return self.connection.execute(command)

    def failed(self, message):
        self.error = RuntimeError(message)
        if self.logger:
            self.logger.error(message)

    def extra_target(self, event):
        if not self.closed:
            self.failed("Production browser opened an unsupported child target; kept paused to preserve domain pacing")

    def request_paused(self, event):
        if self.closed:
            return
        try:
            scheme = urlsplit(event.request.url).scheme
            if scheme in ("http", "https"):
                self.gate.run(event.request.url, lambda: self.continue_request(event.request_id))
            else:
                self.continue_request(event.request_id)
        except Exception:
            self.failed("Browser domain pacing failed; request was not released")

    def continue_request(self, request_id):
        if not self.closed and self.error is None:
            self.execute(self.devtools.fetch.continue_request(request_id))

    def check(self):
        if self.error:
            raise self.error

    def close(self):
        # Do not disable Fetch: that would release queued traffic unpaced.
        self.closed = True
