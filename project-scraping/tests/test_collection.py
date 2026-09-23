import pytest

from scraping.crawler.collection import CollectionProgress
from scraping.crawler.report import check_report


def test_complete_collection():
    progress = CollectionProgress(expected_total=3)
    assert not progress.page("first", ["a", "b"], next_cursor="second")
    assert progress.page("second", ["c"], next_cursor=None)
    assert progress.keys == {"a", "b", "c"}


@pytest.mark.parametrize("next_cursor", ["first", ""])
def test_bad_next_cursor(next_cursor):
    with pytest.raises(ValueError):
        CollectionProgress().page("first", ["a"], next_cursor=next_cursor)


def test_repeated_earlier_cursor():
    progress = CollectionProgress()
    progress.page("first", ["a"], next_cursor="second")
    with pytest.raises(ValueError, match="repeated"):
        progress.page("second", ["b"], next_cursor="first")


def test_missing_page():
    progress = CollectionProgress()
    progress.page("first", ["a"], next_cursor="second")
    with pytest.raises(ValueError, match="skipped"):
        progress.page("third", ["b"], next_cursor=None)


@pytest.mark.parametrize("keys", [["a", "a"], [""], [None]])
def test_invalid_identities(keys):
    with pytest.raises(ValueError):
        CollectionProgress().page("first", keys, next_cursor=None)


def test_duplicate_across_pages():
    progress = CollectionProgress()
    progress.page("first", ["a"], next_cursor="second")
    with pytest.raises(ValueError, match="Duplicate"):
        progress.page("second", ["a"], next_cursor=None)


@pytest.mark.parametrize("total", [1, 3])
def test_incorrect_advertised_total(total):
    with pytest.raises(ValueError):
        CollectionProgress(expected_total=total).page("first", ["a", "b"], next_cursor=None)


def test_limit_is_not_successful_truncation():
    with pytest.raises(ValueError, match="safety limit"):
        CollectionProgress(max_pages=1).page("first", ["a"], next_cursor="second")


def test_empty_nonterminal_page():
    with pytest.raises(ValueError, match="no record progress"):
        CollectionProgress().page("first", [], next_cursor="second")


def test_explicit_zero_total_can_be_understood_without_relaxing_publication():
    assert CollectionProgress(expected_total=0).page("first", [], next_cursor=None)


def test_finished_collection_is_closed():
    progress = CollectionProgress()
    progress.page("first", ["a"], next_cursor=None)
    with pytest.raises(ValueError, match="already completed"):
        progress.page("second", ["b"], next_cursor=None)


class Stats:
    def __init__(self):
        self.values = {"item_scraped_count": 1}

    def inc_value(self, key, count=1):
        self.values[key] = self.values.get(key, 0) + count


def test_unvisited_next_request_fails_worker_completion():
    stats = Stats()
    progress = CollectionProgress(stats=stats)
    progress.page("first", ["a"], next_cursor="second")
    # Scrapy may filter the next request without calling the parser again.
    with pytest.raises(ValueError, match="collection/incomplete"):
        check_report({"reason": "finished", "stats": stats.values})


def test_multiple_collections_must_all_complete():
    stats = Stats()
    first, second = CollectionProgress(stats=stats), CollectionProgress(stats=stats)
    first.page("a", ["1"], next_cursor=None)
    with pytest.raises(ValueError, match="collection/incomplete"):
        check_report({"reason": "finished", "stats": stats.values})
    second.page("b", ["2"], next_cursor=None)
    assert check_report({"reason": "finished", "stats": stats.values}) == 1


def test_single_key_string_is_not_a_collection():
    with pytest.raises(ValueError, match="single string"):
        CollectionProgress().page("first", "abc", next_cursor=None)
