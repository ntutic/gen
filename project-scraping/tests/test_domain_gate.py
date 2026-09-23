import multiprocessing
import time

import pytest

from scraping.crawler.domain_gate import DomainGate, pacing_directory, retry_after_seconds


def take_lock(root, output):
    gate = DomainGate(root, interval=0.04)
    stream = gate.acquire("https://same.example.invalid/path")
    output.put(time.monotonic())
    time.sleep(0.03)
    gate.release(stream)


def test_capture_directories_do_not_partition_limits(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("VCLIST_RATE_LIMIT_DIR", raising=False)
    monkeypatch.setenv("VCLIST_CAPTURE_DIR", str(tmp_path / "agent-a"))
    first = DomainGate().root
    monkeypatch.setenv("VCLIST_CAPTURE_DIR", str(tmp_path / "agent-b"))
    assert DomainGate().root == first == tmp_path / "state/scraping/rate-limit"


def test_explicit_shared_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("VCLIST_RATE_LIMIT_DIR", str(tmp_path / "shared"))
    assert pacing_directory() == tmp_path / "shared"


def test_processes_serialize_and_keep_the_cooldown(tmp_path):
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    processes = [context.Process(target=take_lock, args=(tmp_path, output)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        times = sorted(output.get(timeout=10) for _ in processes)
        for process in processes:
            process.join(timeout=5)
            assert process.exitcode == 0
        assert times[1] - times[0] >= 0.06
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
                process.join()
        output.close()


def test_exception_releases_lock(tmp_path):
    gate = DomainGate(tmp_path, interval=0.01)
    with pytest.raises(ValueError):
        gate.run("https://example.invalid", lambda: (_ for _ in ()).throw(ValueError("broken")))
    stream = gate.acquire("http://example.invalid")
    gate.release(stream)


def test_retry_after():
    assert retry_after_seconds(b"23") == 23
    assert retry_after_seconds(b"invalid") == 0
    assert retry_after_seconds(None) == 0


def test_invalid_interval(tmp_path):
    with pytest.raises(ValueError):
        DomainGate(tmp_path, interval=0)
