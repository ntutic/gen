"""Small completion report consumed by the queue worker."""

import json
import os
from pathlib import Path

from scrapy import signals


class CrawlReport:
    def __init__(self, crawler):
        self.crawler = crawler
        crawler.signals.connect(self.closed, signals.spider_closed)

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    def closed(self, spider, reason):
        target = os.environ.get("VCLIST_CRAWL_REPORT")
        if target:
            Path(target).write_text(
                json.dumps(
                    {
                        "reason": reason,
                        "stats": self.crawler.stats.get_stats(),
                    },
                    default=str,
                ),
                encoding="utf-8",
            )


def check_report(report: dict) -> int:
    stats = report["stats"]
    if report["reason"] != "finished":
        raise ValueError(f"Crawl did not complete: {report['reason']}")
    failures = {
        key: count
        for key, count in stats.items()
        if (
            key.startswith("spider_exceptions/")
            or key
            in (
                "item_dropped_count",
                "item_error_count",
                "retry/max_reached",
                "httperror/response_ignored_count",
                "log_count/ERROR",
            )
        )
        and count
    }
    if failures:
        raise ValueError(f"Crawl had errors: {failures}")
    count = stats.get("item_scraped_count", 0)
    if not count:
        raise ValueError("Crawl produced no records")
    return count
