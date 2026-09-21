"""Spider files are the catalog. Database registrations mirror these definitions."""

from scrapy.spiderloader import SpiderLoader
from scrapy.utils.project import get_project_settings
from sqlalchemy import select

from scrapectl.models import Scraper, Source


def definitions():
    loader = SpiderLoader(get_project_settings())
    return {name: loader.load(name) for name in sorted(loader.list())}


def sync_scrapers(session):
    spiders = definitions()
    for name, spider in spiders.items():
        if spider.name != name or spider.__module__ != f"scraping.crawler.spiders.{name}":
            raise ValueError(f"Spider {name} must use its source ID as its name and filename")
        if session.get(Source, name) is None:
            session.add(Source(id=name, name=getattr(spider, "source_name", name),
                               source_kind=getattr(spider, "source_kind", None)))
            session.flush()
        row = session.get(Scraper, name)
        if row is None:
            row = Scraper(id=name, source_id=name, module=spider.__module__)
            session.add(row)
        row.enabled = spider.enabled
        row.module = spider.__module__
    for row in session.scalars(select(Scraper)):
        if row.id not in spiders:
            row.enabled = False
    session.flush()
