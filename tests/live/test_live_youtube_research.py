"""Live: one real YouTube Data API search + details call, through the quota ledger."""
from datetime import datetime, timedelta, timezone

from blox.research import shorts, youtube_api as Y

from .conftest import need


def test_search_and_details(db):
    need('YOUTUBE_API_KEY')
    now = datetime.now(timezone.utc)
    body = Y.search('roblox animation story', (now - timedelta(days=2)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                    now.strftime('%Y-%m-%dT%H:%M:%SZ'), cache_minutes=0)
    ids = [it['id']['videoId'] for it in body.get('items', [])][:5]
    assert ids, 'search returned nothing for the last two days'
    items = Y.videos(ids)
    assert items and all('statistics' in it for it in items)
    labels = [shorts.classify(it)['label'] for it in items]
    assert set(labels) <= {'likely_short', 'possible_short', 'unlikely_short'}
    assert Y.used('search', db) == 1 and Y.used('shared', db) >= 1
