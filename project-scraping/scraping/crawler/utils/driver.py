from __future__ import annotations

import time
from collections.abc import Callable

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


class IncompleteCollection(RuntimeError):
    """The browser could not establish that collection finished."""


def wait_for_selector(driver, selector: str, *, timeout: float = 20):
    """Wait for actual page content, raising if it never appears."""
    return WebDriverWait(driver, timeout).until(lambda page: page.find_elements(By.CSS_SELECTOR, selector))


def scroll_to_page_bottom(driver, *, max_steps: int = 100, timeout: float = 60, pause: float = 0.5) -> None:
    """Scroll a normal document until its bottom remains stable across three polls."""
    deadline = time.monotonic() + timeout
    previous = None
    stable = 0
    for _ in range(max_steps):
        if time.monotonic() >= deadline:
            break
        driver.execute_script("window.scrollTo(0, document.documentElement.scrollHeight);")
        time.sleep(pause)
        state = driver.execute_script(
            "return [window.scrollY, document.documentElement.scrollHeight, window.innerHeight];"
        )
        top, height, viewport = state
        stable = stable + 1 if state == previous and top + viewport >= height - 1 else 0
        if stable >= 2:
            return
        previous = state
    raise IncompleteCollection("Page scrolling exceeded its step/time limit")


def click_load_more(
    driver,
    button_selector: str,
    *,
    item_selector: str,
    max_clicks: int = 100,
    timeout: float = 60,
    wait_time: float = 10,
) -> None:
    """Click while a load-more control exists; require new items after each click."""
    deadline = time.monotonic() + timeout

    def button():
        return next(
            (item for item in driver.find_elements(By.CSS_SELECTOR, button_selector) if item.is_displayed()), None
        )

    for attempt in range(max_clicks + 1):
        control = button()
        if control is None:
            return
        if attempt == max_clicks or time.monotonic() >= deadline:
            raise IncompleteCollection("Load-more exceeded its click/time limit")
        if not control.is_enabled():
            try:
                WebDriverWait(driver, min(wait_time, max(0, deadline - time.monotonic()))).until(
                    lambda page: (current := button()) is None or current.is_enabled()
                )
            except TimeoutException:
                raise IncompleteCollection("Load-more control remained disabled") from None
            control = button()
            if control is None:
                return
        before = len(driver.find_elements(By.CSS_SELECTOR, item_selector))
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", control)
        control.click()
        try:
            WebDriverWait(driver, min(wait_time, max(0, deadline - time.monotonic()))).until(
                lambda page, before=before: len(page.find_elements(By.CSS_SELECTOR, item_selector)) > before
            )
        except TimeoutException:
            raise IncompleteCollection("Load-more produced no additional items") from None


def collect_scroll_container_items(
    driver,
    container_selector: str,
    item_selector: str,
    *,
    key: Callable | None = None,
    link_selector: str = "a[href]",
    max_steps: int = 100,
    timeout: float = 60,
    pause: float = 0.25,
) -> int:
    """Capture virtualized cards by stable key, then expose them in a separate DOM node.

    Default identity is the card's link. Supply key(element) for native site IDs.
    Moving styles/HTML are never identity. Read results with
    ``[data-vclist-scroll-collection] > *`` after this returns successfully.
    """
    container = wait_for_selector(driver, container_selector, timeout=min(20, timeout))[0]
    deadline = time.monotonic() + timeout
    collected: dict[str, str] = {}
    previous_bottom = None
    stable = 0
    for _ in range(max_steps):
        if time.monotonic() >= deadline:
            break
        for element in driver.find_elements(By.CSS_SELECTOR, item_selector):
            identity = (
                key(element) if key else element.find_element(By.CSS_SELECTOR, link_selector).get_attribute("href")
            )
            if not identity:
                raise IncompleteCollection("Virtualized card has no stable key")
            collected[str(identity)] = element.get_attribute("outerHTML")
        top, height, viewport = driver.execute_script(
            "return [arguments[0].scrollTop, arguments[0].scrollHeight, arguments[0].clientHeight];", container
        )
        if viewport <= 0:
            raise IncompleteCollection("Scroll container has no visible height")
        maximum = max(0, height - viewport)
        if top >= maximum - 1:
            state = (height, frozenset(collected))
            stable = stable + 1 if state == previous_bottom else 0
            previous_bottom = state
            if stable >= 2:
                if not collected:
                    raise IncompleteCollection("Scroll container contained no cards")
                driver.execute_script(
                    """
                    document.querySelector('[data-vclist-scroll-collection]')?.remove();
                    const collection = document.createElement('div');
                    collection.setAttribute('data-vclist-scroll-collection', '');
                    collection.innerHTML = arguments[0].join('');
                    document.body.appendChild(collection);
                    """,
                    list(collected.values()),
                )
                return len(collected)
        else:
            stable = 0
            next_top = min(maximum, top + max(1, int(viewport * 0.8)))
            driver.execute_script("arguments[0].scrollTop = arguments[1];", container, next_top)
        time.sleep(pause)
    raise IncompleteCollection("Virtualized scrolling stalled or exceeded its step/time limit")
