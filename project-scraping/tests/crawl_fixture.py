"""A tiny real Scrapy crawl, served exclusively by the test's local proxy."""

import os

from scrapy.crawler import CrawlerProcess
from scrapy.spiderloader import SpiderLoader
from scrapy.utils.project import get_project_settings

from scraping.crawler.base_spider import BaseSpider
from scraping.crawler.loaders.record_loader import RecordLoader


class FixtureSpider(BaseSpider):
    name = "ABC"
    start_urls = ["http://portfolio.invalid/"]

    def parse(self, response):
        loader = RecordLoader(spider=self, response=response)
        loader.add_css("name", "h1::text")
        item = loader.load_item()
        item["source_key"] = "id:1"
        yield item
        if os.environ.get("FIXTURE_FAIL"):
            raise ValueError("Detail page extraction failed after the first item")


if __name__ == "__main__":
    settings = get_project_settings()
    settings.set("LOG_LEVEL", "ERROR", priority="cmdline")
    settings.set("HTTPCACHE_ENABLED", False, priority="cmdline")
    wanted = os.environ.get("FIXTURE_SPIDER", "ABC")
    spider_cls = FixtureSpider if wanted == FixtureSpider.name else SpiderLoader(settings).load(wanted)
    process = CrawlerProcess(settings)
    process.crawl(spider_cls, job_id=os.environ["FIXTURE_JOB_ID"])
    process.start()
