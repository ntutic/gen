BOT_NAME = "vclist"
SPIDER_MODULES = ["scraping.crawler.spiders"]
NEWSPIDER_MODULE = "scraping.crawler.spiders"

USER_AGENT = "Vclist/1.0 (+record indexing)"
COOKIES_ENABLED = False
ROBOTSTXT_OBEY = False
TELNETCONSOLE_ENABLED = False

AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1
AUTOTHROTTLE_MAX_DELAY = 30
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
DOWNLOAD_DELAY = 1
CONCURRENT_REQUESTS_PER_DOMAIN = 1

HTTPCACHE_ENABLED = False

SELENIUM_DRIVER_ARGUMENTS = ["--disable-dev-shm-usage", "--no-sandbox"]
SELENIUM_HEADLESS = True
DOWNLOAD_TIMEOUT = 30
CLOSESPIDER_TIMEOUT = 900

ITEM_PIPELINES = {"scrapectl.scrapy_pipeline.StagedRecordPipeline": 100}
DOWNLOADER_MIDDLEWARES = {
    "scrapy.downloadermiddlewares.httpproxy.HttpProxyMiddleware": None,
    "scraping.crawler.capture.CaptureMiddleware": 540,
    "scraping.crawler.proxy.ProxyMiddleware": 750,
    "scraping.crawler.scrapy_selenium.middlewares.SeleniumMiddleware": 800,
    "scraping.crawler.capture.CaptureResponseMiddleware": 900,
}
EXTENSIONS = {"scraping.crawler.report.CrawlReport": 100}

REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
FEED_EXPORT_ENCODING = "utf-8"

DOWNLOAD_HANDLERS = {
    "http": "scraping.crawler.rate_limit.PacedHTTPDownloadHandler",
    "https": "scraping.crawler.rate_limit.PacedHTTPDownloadHandler",
}
