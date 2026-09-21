from scrapy import Request


class SeleniumRequest(Request):
    """A proxied browser page, with an optional bounded wait and preparation hook."""

    attributes = (*Request.attributes, "wait_time", "wait_until", "screenshot", "script", "init")

    def __init__(self, *args, wait_time=20, wait_until=None, screenshot=False, script=None, init=None, **kwargs):
        self.wait_time = wait_time
        self.wait_until = wait_until
        self.screenshot = screenshot
        self.script = script
        self.init = init
        super().__init__(*args, **kwargs)
        self.meta["dont_cache"] = True
