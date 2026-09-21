from __future__ import annotations

from datetime import datetime, timezone

from scrapy import Spider

from scraping.crawler.scrapy_selenium import SeleniumRequest
from scraping.crawler.utils.driver import scroll_to_page_bottom, wait_for_selector


class BaseSpider(Spider):
    # Contract: name is the source_id; every spider sets source_kind,
    # key_description, start_urls, and notes.
    source_kind = None
    key_description = None
    notes = "Needs live validation"

    def __init__(self, *args, job_id: str | int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.job_id = int(job_id) if job_id is not None else None
        self.timestamp = datetime.now(timezone.utc).isoformat()

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        spider.scrape_job = None
        if spider.job_id is not None:
            from scrapectl.db import Session
            from scrapectl.models import ScrapeJob

            with Session() as session:
                spider.scrape_job = session.get(ScrapeJob, spider.job_id)
            if spider.scrape_job is None:
                raise ValueError("Scrape job does not exist")
            spider.timestamp = spider.scrape_job.observed_at.replace(tzinfo=timezone.utc).isoformat()
        return spider


class SeleniumBaseSpider(BaseSpider):
    """Browser start URLs; override prepare_page for site-specific interactions."""

    ready_selector = "body"
    page_timeout = 20
    scroll_to_bottom = False

    async def start(self):
        for url in self.start_urls:
            yield SeleniumRequest(
                url=url,
                callback=self.parse,
                init=self.prepare_page,
                meta={"dont_cache": True},
            )

    def prepare_page(self, driver) -> None:
        wait_for_selector(driver, self.ready_selector, timeout=self.page_timeout)
        if self.scroll_to_bottom:
            scroll_to_page_bottom(driver)
