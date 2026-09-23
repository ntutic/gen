# HTML list and detail pages

Start with `scraping/crawler/spiders/EXAMPLE.py`, its small
`tests/fixtures/EXAMPLE-list.html`, and `tests/test_EXAMPLE.py`. They demonstrate
explicit native keys, relative links, optional features, and negative cases for
missing IDs and a changed advertised total. Do not copy example-specific labels
or counts into another source.

Use response CSS/XPath directly. Pass `response` to `RecordLoader` for relative
URL resolution. When detail fields are needed, carry the item through
`cb_kwargs`/`meta`, preserving `raw_payload`, then yield once it is complete.
Use an API/embedded collection instead only when it covers the same requested
scope and meaning. Missing list fields do not prove missing source information.

For serial next-link pagination, `CollectionProgress.page(current_url, keys,
next_cursor=next_url)` checks identity uniqueness, progress and explicit
termination. Construct it with `stats=self.crawler.stats` to reject an unvisited
next request at worker completion. Let Scrapy fetch the next request normally. Never treat a safety
limit or failed detail request as successful completion.

Assert identities, resolved links, requested fields/units, missing optional
values and a realistic failure. Compare against the source's advertised count
or another reviewed scope condition, not the roster CSV estimate.
