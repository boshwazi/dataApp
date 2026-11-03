"""Shared test setup.

A TestClient used outside `with` starts a new event loop for every request, and on Windows each loop opens
loopback sockets. Across a full run that was thousands of them, which now and then ran out of socket buffer
space (WinError 10055) in whichever test came next. Here every client keeps one event loop for all its
requests, and is closed when its test ends.
"""

import fastapi.testclient
import pytest
from starlette.testclient import TestClient

open_clients: list[TestClient] = []


class OneLoopClient(TestClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.__enter__()
        open_clients.append(self)


fastapi.testclient.TestClient = OneLoopClient


@pytest.fixture(autouse=True)
def close_clients():
    yield
    while open_clients:
        open_clients.pop().__exit__(None, None, None)


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    """Every test starts with empty rate-limit counters (the tests act as one person, many times over)."""
    from app import protect

    protect.WRITES.reset()
    protect.AI_CALLS.reset()
    yield
