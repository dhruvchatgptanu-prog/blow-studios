import os

import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        item.add_marker(pytest.mark.live)
        if os.environ.get('LIVE_TESTS') != '1':
            item.add_marker(pytest.mark.skip(reason='Live tests run only with LIVE_TESTS=1'))


def need(*names):
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        pytest.skip('Missing ' + ', '.join(missing))
