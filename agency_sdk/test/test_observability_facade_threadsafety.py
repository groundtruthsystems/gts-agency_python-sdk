"""Phase 9 / H1: AgencyClient.observability() must be thread-safe.

The lazy build is documented to return the same instance on repeated calls. Under
concurrency the unguarded check-then-set could construct several Observability
objects (and, once each is init()'d, attach duplicate root log handlers). This
test forces the race and asserts a single construction.
"""

import threading
import time

import agency_sdk.observability as obs_mod
from agency_sdk.client import AgencyClient


def test_observability_is_thread_safe(monkeypatch, fake_credentials):
    real_cls = obs_mod.Observability
    count = {"n": 0}
    count_lock = threading.Lock()

    def counting(*args, **kwargs):
        with count_lock:
            count["n"] += 1
        time.sleep(0.01)  # widen the race window so an unguarded build builds N times
        return real_cls(*args, **kwargs)

    monkeypatch.setattr(obs_mod, "Observability", counting)

    client = AgencyClient(token_supplier=fake_credentials, base_url="http://cp.test")

    n_threads = 8
    results: list = []
    results_lock = threading.Lock()
    barrier = threading.Barrier(n_threads)

    def worker():
        barrier.wait()  # release all threads together
        instance = client.observability("gts-x")
        with results_lock:
            results.append(instance)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert count["n"] == 1  # constructed exactly once despite concurrent callers
    assert len(results) == n_threads
    assert all(instance is results[0] for instance in results)  # all share one instance
