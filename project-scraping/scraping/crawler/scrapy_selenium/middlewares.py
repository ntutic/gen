"""Browser requests share a lazy driver and the mandatory project proxy."""

import asyncio
import threading

from scrapy import signals
from scrapy.http.response.text import TextResponse
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait
from seleniumbase import Driver

from scraping.crawler.proxy import AuthenticatedProxyBridge, UpstreamProxy
from scraping.crawler.rate_limit import production_live

from .http import SeleniumRequest
from .pacing import BrowserPacing


class SeleniumMiddleware:
    def __init__(self, settings, *, driver_factory=Driver, bridge_factory=AuthenticatedProxyBridge):
        self.settings = settings
        self.upstream = None
        self.driver_factory = driver_factory
        self.bridge_factory = bridge_factory
        self.driver = None
        self.bridge = None
        self.pacing = None
        self.crawler = None
        self.browser_lock = threading.Lock()
        self.closed = False

    @classmethod
    def from_crawler(cls, crawler):
        middleware = cls(crawler.settings)
        middleware.crawler = crawler
        crawler.signals.connect(middleware.spider_closed, signals.spider_closed)
        return middleware

    def _ensure_driver(self):
        if self.driver is not None:
            return self.driver
        # Chrome authenticates through a loopback bridge. No credentials go into
        # command-line flags, an extension, a URL, or a browser-visible page.
        self.upstream = UpstreamProxy.from_settings(self.settings)
        self.bridge = self.bridge_factory(self.upstream).start()
        arguments = list(self.settings.get("SELENIUM_DRIVER_ARGUMENTS", []))
        if any("proxy" in arg.lower() for arg in arguments):
            self.bridge.close()
            self.bridge = None
            raise ValueError("Configure the scraping proxy through PROXY_SERVER only")
        arguments.extend(
            [
                "--proxy-bypass-list=<-loopback>",
                "--disable-quic",
                "--disable-background-networking",
                "--dns-prefetch-disable",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            ]
        )
        try:
            self.driver = self.driver_factory(
                uc=False,
                cft=True,
                headless=self.settings.getbool("SELENIUM_HEADLESS", True),
                chromium_arg=",".join(arguments),
                agent=self.settings.get("USER_AGENT"),
                proxy=self.bridge.proxy_string(),
                proxy_bypass_list="<-loopback>",
                host_resolver_rules="MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
            )
            self.driver.set_page_load_timeout(self.settings.getfloat("SELENIUM_PAGE_LOAD_TIMEOUT", 30))
        except Exception:
            self.spider_closed()
            raise
        return self.driver

    async def process_request(self, request, spider=None):
        if not isinstance(request, SeleniumRequest):
            return None
        return await asyncio.to_thread(self._download, request, spider)

    def _download(self, request, spider=None):
        with self.browser_lock:
            if self.closed:
                raise RuntimeError("Browser closed before request started")
            return self._download_page(request, spider)

    def _download_page(self, request, spider=None):
        driver = self._ensure_driver()
        spider = spider or (self.crawler.spider if self.crawler else None)
        if production_live(spider) and self.pacing is None:
            self.pacing = BrowserPacing(driver, logger=spider.logger)
            driver.set_page_load_timeout(self.settings.getfloat("SELENIUM_PAGE_LOAD_TIMEOUT", 3600))
        request.meta["dont_cache"] = True
        # SeleniumBase's UC get() runs a requests.get preflight that can bypass
        # the HTTPS proxy. Use Selenium's navigation command directly instead.
        WebDriver.get(driver, request.url)
        if request.wait_until:
            WebDriverWait(driver, request.wait_time).until(request.wait_until)
        if request.script:
            driver.execute_script(request.script)
        if request.init:
            request.init(driver)
        if self.pacing:
            self.pacing.check()
        if request.screenshot:
            request.meta["screenshot"] = driver.get_screenshot_as_png()
        return TextResponse(driver.current_url, body=driver.page_source.encode(), encoding="utf-8", request=request)

    def spider_closed(self, spider=None, reason=None):
        self.closed = True
        if self.pacing:
            self.pacing.close()
        driver, self.driver = self.driver, None
        try:
            if driver is not None:
                driver.quit()
        finally:
            bridge, self.bridge = self.bridge, None
            if bridge is not None:
                bridge.close()
