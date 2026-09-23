# Explicit JSON pagination

Use `records(response.json(), "data.records")` from
`scraping/crawler/script_extraction.py` for a required collection. Optional
values use `data_value`. Missing or wrong-shaped collections must fail rather
than become an invented empty list. For embedded JSON, select the exact block
with `json_script`; for a selected JavaScript literal use `parse_js_literal`.

Keep requests as ordinary Scrapy requests. Use the small, tested
`CollectionProgress` helper rather than rewriting cursor, duplicate-ID and
incomplete-total checks in every spider:

```python
from scraping.crawler.collection import CollectionProgress

# The source's own advertised total, not a CSV estimate.
progress = CollectionProgress(expected_total=3, max_pages=100)
assert not progress.page("first", ["native-1", "native-2"], next_cursor="cursor-2")
assert progress.page("cursor-2", ["native-3"], next_cursor=None)
```

In the spider, construct with `stats=self.crawler.stats` so the worker rejects
unfinished traversal even when Scrapy filters a next request. The helper tracks
pending collections in native crawl statistics; all must reach explicit
exhaustion. The plain example above omits stats for an offline unit example.
Derive the keys with `source_key` and supply the actual native
cursor or next URL. Carry the progress object through the serial request chain
with `cb_kwargs`. Supply `None` only when the API explicitly indicates exhaustion.
Do not infer termination from a guessed page length. A known empty collection can
be described by the helper, but the worker's no-empty-publication policy remains.

`tests/test_collection.py` covers repeated/skipped cursors, cross-page duplicate
identities, empty nonterminal pages, incorrect totals and safety-limit failures.
Add a real source fixture asserting field meaning, not just the progress helper.
