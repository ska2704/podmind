from datetime import UTC, datetime, timedelta

from app.window import WindowStore


def _ts(offset_s: int) -> datetime:
    base = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
    return base + timedelta(seconds=offset_s)


def test_fresh_pod_creates_new_window():
    store = WindowStore(window_s=60)
    assert len(store) == 0

    store.add_sample("sh-edge", "hvac-1", _ts(0), 0.1)

    w = store.get("sh-edge", "hvac-1")
    assert w is not None
    assert len(w) == 1
    assert w.values() == [0.1]


def test_window_drops_samples_older_than_window_s():
    store = WindowStore(window_s=60)
    store.add_sample("ns", "pod-a", _ts(0), 0.1)
    store.add_sample("ns", "pod-a", _ts(30), 0.2)
    store.add_sample("ns", "pod-a", _ts(70), 0.3)

    w = store.get("ns", "pod-a")
    assert w is not None
    # _ts(0) is 70s before _ts(70), outside the 60s window — must be dropped.
    assert w.values() == [0.2, 0.3]


def test_two_pods_have_independent_windows():
    store = WindowStore(window_s=60)
    store.add_sample("ns", "a", _ts(0), 1.0)
    store.add_sample("ns", "b", _ts(0), 2.0)
    store.add_sample("ns", "a", _ts(10), 1.1)

    a = store.get("ns", "a")
    b = store.get("ns", "b")
    assert a is not None and b is not None
    assert a.values() == [1.0, 1.1]
    assert b.values() == [2.0]


def test_evict_stale_drops_unseen_pods():
    store = WindowStore(window_s=60)
    store.add_sample("ns", "ghost", _ts(0), 0.1)
    store.add_sample("ns", "live", _ts(0), 0.2)

    # now() puts us 200s after _ts(0); 2 * window_s == 120s, so ghost
    # should evict. "live" gets touched at _ts(150), still within
    # 2*window_s of "now" (=_ts(200)).
    store.add_sample("ns", "live", _ts(150), 0.3)
    evicted = store.evict_stale(_ts(200))

    assert evicted == 1
    assert store.get("ns", "ghost") is None
    assert store.get("ns", "live") is not None


def test_evict_keeps_recently_seen_pods():
    store = WindowStore(window_s=60)
    store.add_sample("ns", "pod-a", _ts(0), 0.1)
    # 90s later — within 2 * 60 = 120s — must not evict.
    evicted = store.evict_stale(_ts(90))
    assert evicted == 0
    assert store.get("ns", "pod-a") is not None
