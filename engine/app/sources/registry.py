"""Maps fetch-task (kind, source) pairs to adapter functions."""
from __future__ import annotations

from . import ats, feeds, hackernews, local_boards, ojiiz, page, search
from .base import Handler

FEED_HANDLERS: dict[str, Handler] = {
    "remotive": feeds.fetch_remotive,
    "remoteok": feeds.fetch_remoteok,
    "jobicy": feeds.fetch_jobicy,
    "himalayas": feeds.fetch_himalayas,
    "weworkremotely": feeds.fetch_weworkremotely,
    "workingnomads": feeds.fetch_workingnomads,
    "arbeitnow": feeds.fetch_arbeitnow,
    "ojiiz": ojiiz.fetch_ojiiz,
}


def get_handler(kind: str, source: str) -> Handler | None:
    if kind == "feed":
        return FEED_HANDLERS.get(source)
    if kind == "hn":
        return hackernews.fetch_hackernews
    if kind == "local_board":
        return local_boards.fetch_local_board
    if kind == "ats_board":
        return ats.BOARD_HANDLERS.get(source)
    if kind == "search":
        return search.run_search
    if kind == "page":
        return page.fetch_page
    return None  # ai_discovery is executed by services.fetching (needs Claude)
