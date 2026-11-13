from geopipe.bus import backoff


def test_backoff_doubles_per_delivery_and_is_capped():
    assert [backoff(n, 2) for n in (1, 2, 3, 4, 5)] == [2, 4, 8, 16, 16]
    assert backoff(1, 15) == 15
    assert backoff(10, 15) == 120
