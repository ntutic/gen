import pytest
from sqlalchemy import select

from scrapectl import project_contract
from scrapectl.identity import source_key
from scrapectl.models import Record, Scraper, Source
from scrapectl.processing import normalize_payload, publish_job, stage_result
from scrapectl.queue import claim, enqueue


def test_domain_validation_uses_the_project_owned_hook(monkeypatch):
    def require_period(payload):
        if "Period" not in payload["features"]:
            raise ValueError("A period fact requires its reporting period")

    monkeypatch.setattr(project_contract, "validate_payload", require_period)
    item = {"source_key": source_key("event", "1"), "name": "Period fact"}
    with pytest.raises(ValueError, match="reporting period"):
        normalize_payload(item, "EXAMPLE")
    item["features"] = {"Period": {"value": "2026-Q2", "unit": None}}
    assert normalize_payload(item, "EXAMPLE")["source_key"] == item["source_key"]


def test_two_facts_in_one_document_survive_publication(sessions):
    document_url = "https://example.invalid/report.pdf"
    with sessions() as session:
        session.add(Source(id="EXAMPLE", name="Example"))
        session.add(Scraper(id="EXAMPLE", source_id="EXAMPLE",
                            module="scraping.crawler.spiders.EXAMPLE", enabled=True))
        session.commit()
        job = enqueue(session, "EXAMPLE")
        session.commit()
        claim(session, job.id)
        session.commit()
        for kind in ("authorization", "execution"):
            stage_result(session, job.id, {"source_key": source_key("fact", "program-1", kind),
                                          "name": kind, "url": document_url})
        publish_job(session, job.id)
        session.commit()
        rows = list(session.scalars(select(Record)))
        assert len(rows) == 2
        assert {row.url for row in rows} == {document_url}
        assert len({row.source_key for row in rows}) == 2
