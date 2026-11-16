from geopipe import bus
from geopipe.bus import backoff


def test_backoff_doubles_per_delivery_and_is_capped():
    assert [backoff(n, 2) for n in (1, 2, 3, 4, 5)] == [2, 4, 8, 16, 16]
    assert backoff(1, 15) == 15
    assert backoff(10, 15) == 120


class Recorder:
    def __init__(self, fail=False):
        self.rows, self.fail = [], fail

    def record(self, job_id, status):
        if self.fail:
            raise RuntimeError("database is down")
        self.rows.append((job_id, status))


class StatusRedis:
    def __init__(self):
        self.h = {}

    def hset(self, key, mapping):
        self.h.setdefault(key, {}).update(mapping)

    def expire(self, key, ttl):
        pass

    def hgetall(self, key):
        return dict(self.h[key])


def test_only_terminal_statuses_reach_the_history_and_with_the_whole_hash():
    rec, r = Recorder(), StatusRedis()
    bus.use_history(rec)
    try:
        bus.set_status(r, "j", "running", tenant_id="acme", done=1)
        assert rec.rows == []
        bus.set_status(r, "j", "done", output_uri="s3://x")
        assert rec.rows == [("j", {"state": "done", "tenant_id": "acme", "done": "1",
                                   "output_uri": "s3://x"})]
    finally:
        bus.use_history(None)


def test_a_history_failure_does_not_fail_the_job():
    r = StatusRedis()
    bus.use_history(Recorder(fail=True))
    try:
        bus.set_status(r, "j", "error", tenant_id="acme", error="x")
        assert r.h["job:j:status"]["state"] == "error"
    finally:
        bus.use_history(None)
