"""Fan-out threads must not share a client instance across roles."""
import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from core.llm import RoleClient


class _FakeCostedClient:
    """A client whose only state is the price its last call set, like the real ones."""

    def __init__(self):
        self.model = "fake"
        self.last_cost = None

    def stream(self, price):
        self.last_cost = None
        self.last_cost = price


def test_two_fan_out_groups_do_not_swap_each_others_price():
    """
    `_first_pass` gives every group its own `copy.copy(llm)`, expecting a
    private client per thread. A `RoleClient` copy that only copies the
    wrapper hands every thread the same underlying client, so the slower
    group's price can still be sitting there when the faster group reads it.
    """
    shared_design = _FakeCostedClient()
    table = RoleClient(roles={"design": shared_design}, caller=shared_design)
    slow_done = threading.Event()

    def fast_group():
        client = copy.copy(table).for_role("design")
        client.stream("fast-price")
        assert slow_done.wait(timeout=1)
        return client.last_cost

    def slow_group():
        time.sleep(0.02)
        client = copy.copy(table).for_role("design")
        client.stream("slow-price")
        slow_done.set()
        return client.last_cost

    with ThreadPoolExecutor(max_workers=2) as pool:
        fast_future = pool.submit(fast_group)
        slow_future = pool.submit(slow_group)
        fast_cost = fast_future.result()
        slow_cost = slow_future.result()

    assert fast_cost == "fast-price"
    assert slow_cost == "slow-price"
