"""Small completion report consumed by the queue worker."""

import json
import os
from pathlib import Path


class CrawlReport:
    def __init__(self, crawler):
        from scrapy import signals

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
                    {"reason": reason, "stats": self.crawler.stats.get_stats()},
                    default=str,
                ),
                encoding="utf-8",
            )


def check_report(report: dict) -> int:
    if not isinstance(report, dict) or not isinstance(report.get("stats"), dict):
        raise ValueError("Crawl completion report is missing or malformed")
    stats = report["stats"]
    if report.get("reason") != "finished":
        raise ValueError(f"Crawl did not complete: {report.get('reason')}")
    failures = {
        key: count
        for key, count in stats.items()
        if (
            key.startswith("spider_exceptions/")
            or key in (
                "item_dropped_count", "item_error_count", "retry/max_reached",
                "httperror/response_ignored_count", "log_count/ERROR", "capture/missing", "collection/incomplete",
            )
        ) and count
    }
    if failures:
        raise ValueError(f"Crawl had errors: {failures}")
    count = stats.get("item_scraped_count", 0)
    if type(count) is not int or count < 1:
        raise ValueError("Crawl produced no records or an invalid item count")
    return count
