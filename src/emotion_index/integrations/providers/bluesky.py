"""Bluesky public search for the broad market and four US styles."""

from urllib.parse import urlencode

from emotion_index.integrations.providers.http import Feed, json_list

QUERIES = (
    ("broad_market", "$SPY OR $QQQ"),
    ("tech_growth", "$XLK OR $SMH"),
    ("cyclical_finance", "$XLF OR $XLI"),
    ("defensive_dividend", "$XLU OR $SCHD"),
    ("high_beta_theme", "$IWM OR $ARKK"),
)


def feeds() -> tuple[Feed, ...]:
    base = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?"
    validate = json_list("posts")
    return tuple(
        Feed("bluesky", name, base + urlencode({"q": query, "sort": "latest", "limit": 50}), validate)
        for name, query in QUERIES
    )
