from __future__ import annotations

import logging

from scrapy.exceptions import DropItem

from scrapectl.db import Session
from scrapectl.processing import stage_source

logger = logging.getLogger(__name__)


class StagedRecordPipeline:
    @classmethod
    def from_crawler(cls, crawler):
        pipeline = cls()
        pipeline.crawler = crawler
        return pipeline

    def open_spider(self) -> None:
        job_id = getattr(self.crawler.spider, "job_id", None)
        if job_id is None:
            raise RuntimeError("scrapers must run through a scrape job")
        self.job_id = int(job_id)
        self.session = Session()

    def close_spider(self) -> None:
        if hasattr(self, "session"):
            self.session.close()

    def process_item(self, item):
        try:
            stage_source(self.session, self.job_id, dict(item))
            self.session.commit()
            return item
        except Exception as exc:
            self.session.rollback()
            logger.exception("failed to stage scraped record")
            raise DropItem(f"failed to stage scraped record: {exc}") from exc
