import json
from urllib.parse import urlencode

import pytest

from app.config import Env, load_config
from app.sources.base import FetchError, FetchResponse, TaskContext


@pytest.fixture(scope="session")
def cfg():
    return load_config()


class FakeClient:
    """Routes URL substrings to canned payloads so source adapters run without network."""

    def __init__(self, routes):
        self.routes = routes  # list of (substring, payload, content_type)
        self.requested: list[str] = []

    def _match(self, url, params):
        full = url + ("?" + urlencode(params) if params else "")
        self.requested.append(full)
        for key, payload, content_type in self.routes:
            if key in full:
                text = payload if isinstance(payload, str) else json.dumps(payload)
                return FetchResponse(url=url, status=200, content_type=content_type, text=text)
        raise FetchError(f"no fake route for {full}", 404)

    def get(self, url, *, params=None, headers=None, check_robots=False, accept=""):
        return self._match(url, params)

    def post_json(self, url, payload, headers=None):
        return self._match(url, None)


@pytest.fixture
def make_ctx(cfg):
    def factory(routes, extractor=None):
        return TaskContext(client=FakeClient(routes), cfg=cfg, env=Env.load(), run_id=1, extractor=extractor)

    return factory
