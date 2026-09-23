from scrapectl.identity import source_key
from scraping.crawler.base_spider import BaseSpider
from scraping.crawler.loaders.record_loader import RecordLoader
from scraping.crawler.processors import positive_number


class ExampleSpider(BaseSpider):
    name = "EXAMPLE"
    source_kind = "static-html"
    key_description = "Native listing data-id"
    notes = "Canvas fixture spider; verified against tests/fixtures/EXAMPLE-list.html."
    start_urls = ["http://example.invalid/list"]

    def parse(self, response):
        advertised = response.css("p.total::text").re_first(r"(\d+)")
        rows = response.css("ul.records li.record")
        if advertised is None or int(advertised) != len(rows):
            raise ValueError(f"EXAMPLE listing changed: advertised {advertised}, parsed {len(rows)}")
        for row in rows:
            native_id = row.attrib.get("data-id")
            if not native_id:
                raise ValueError("EXAMPLE record without native data-id")
            loader = RecordLoader(spider=self, response=response, selector=row)
            loader.add_css("name", "a.name::text")
            loader.add_css("url", "a.name::attr(href)")
            capacity = row.css("span.capacity::text").get()
            if capacity is not None:
                loader.add_feature(
                    "Capacity",
                    positive_number(capacity),
                    row.css("span.capacity::attr(data-unit)").get(),
                )
            item = loader.load_item()
            item["source_key"] = source_key("example-id", native_id)
            yield item
