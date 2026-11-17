"""Watched channels published straight to YouTube (server/youtube_automation.py)."""

import json

import pytest

from core.state import StateDB
from server import youtube_automation as yta
from server import youtube_service


class FakeWorker:
    def __init__(self):
        self.notified = 0

    def notify(self):
        self.notified += 1


@pytest.fixture
def d(tmp_path):
    db = StateDB(tmp_path / "state.db")
    yield db
    db.close()


@pytest.fixture
def ready(monkeypatch, d):
    youtube_service.save_settings(d, {"enabled": True, "privacy": "public"})
    monkeypatch.setattr(youtube_service, "default_channel_id", lambda _d: "UCchannel")
    return d


def clip(d, tmp_path, start=10.0, *, rendered=True, title="Big moment"):
    d.upsert_video("vid1", title="Stream")
    path = tmp_path / f"clip_{start}.mp4"
    if rendered:
        path.write_bytes(b"x")
    return d.add_clip("vid1", start, start + 30, 90, "hook text", path=str(path),
                      title=title, description="What happened.", hashtags='["clutch"]')


def requests(d):
    return [json.loads(r["request"]) for r in d.conn.execute("SELECT request FROM publish_jobs ORDER BY id")]


def test_not_set_up_is_not_retryable(d, tmp_path):
    publish = yta.youtube_publisher(FakeWorker())
    with pytest.raises(yta.NotReady) as e:
        publish(d, clip_ids=[clip(d, tmp_path)], platforms=["youtube"])
    assert getattr(e.value, "retryable", True) is False


def test_queues_a_publish_job_now(ready, tmp_path):
    worker = FakeWorker()
    cid = clip(ready, tmp_path)
    out = yta.youtube_publisher(worker)(
        ready, clip_ids=[cid], platforms=["youtube"], lead_hashtags=["#creator"], footer="Live on Twitch",
    )
    assert [s["clip_id"] for s in out["started"]] == [cid]
    assert out["started"][0]["scheduled_for"] == ""
    assert worker.notified == 1
    (req,) = requests(ready)
    assert req["title"] == "Big moment"
    assert req["privacy"] == "public"
    assert req["publish_at"] is None
    assert req["channel_id"] == "UCchannel"
    assert req["description"] == "What happened.\n\nLive on Twitch\n\n#creator"


def test_never_sends_a_clip_twice(ready, tmp_path):
    cid = clip(ready, tmp_path)
    publish = yta.youtube_publisher(FakeWorker())
    publish(ready, clip_ids=[cid], platforms=["youtube"])
    again = publish(ready, clip_ids=[cid], platforms=["youtube"])
    assert again["started"] == []
    assert again["skipped"] == [{"clip_id": cid, "reason": "already sent"}]
    assert len(requests(ready)) == 1


def test_failed_job_can_be_sent_again(ready, tmp_path):
    cid = clip(ready, tmp_path)
    publish = yta.youtube_publisher(FakeWorker())
    publish(ready, clip_ids=[cid], platforms=["youtube"])
    job = ready.conn.execute("SELECT id FROM publish_jobs").fetchone()
    ready.finish_publish_job(job["id"], "failed", error="boom")
    assert len(publish(ready, clip_ids=[cid], platforms=["youtube"])["started"]) == 1


def test_unrendered_clip_is_skipped(ready, tmp_path):
    cid = clip(ready, tmp_path, rendered=False)
    out = yta.youtube_publisher(FakeWorker())(ready, clip_ids=[cid], platforms=["youtube"])
    assert out["skipped"] == [{"clip_id": cid, "reason": "no rendered file yet"}]


def test_spread_schedules_private_with_publish_times(ready, tmp_path):
    ids = [clip(ready, tmp_path, start=s) for s in (10.0, 100.0, 200.0)]
    out = yta.youtube_publisher(FakeWorker())(
        ready, clip_ids=ids, platforms=["youtube"], per_day=2, gap_hours=2,
    )
    times = [s["scheduled_for"] for s in out["started"]]
    assert all(times) and times == sorted(times) and len(set(times)) == 3
    for req in requests(ready):
        assert req["privacy"] == "private"
        assert req["publish_at"]


def test_privacy_override(ready, tmp_path):
    cid = clip(ready, tmp_path)
    yta.youtube_publisher(FakeWorker())(
        ready, clip_ids=[cid], platforms=["youtube"], overrides={"youtube": {"privacy": "unlisted"}},
    )
    assert requests(ready)[0]["privacy"] == "unlisted"


def test_only_own_hashtags_flag(ready, tmp_path):
    cid = clip(ready, tmp_path)
    yta.youtube_publisher(FakeWorker())(ready, clip_ids=[cid], platforms=["youtube"], ai_hashtags=False)
    assert requests(ready)[0]["skip_clip_hashtags"] is True


def test_routing_splits_youtube_from_other_platforms(ready):
    calls = []

    def direct(_d, **kw):
        calls.append(("direct", kw["platforms"]))
        return {"started": [{"clip_id": 1}], "skipped": []}

    def woop(_d, **kw):
        calls.append(("woop", kw["platforms"]))
        return {"started": [{"clip_id": 1}], "skipped": []}

    out = yta.routing_publisher(direct, woop)(ready, clip_ids=[1], platforms=["youtube", "tiktok"])
    assert calls == [("direct", ["youtube"]), ("woop", ["tiktok"])]
    assert len(out["started"]) == 2


def test_routing_uses_woop_for_youtube_when_direct_is_off(d):
    calls = []
    yta.routing_publisher(
        lambda _d, **kw: calls.append("direct") or {},
        lambda _d, **kw: calls.append(("woop", kw["platforms"])) or {},
    )(d, clip_ids=[1], platforms=["youtube"])
    assert calls == [("woop", ["youtube"])]


def test_worker_drops_clip_hashtags_when_asked(d, tmp_path):
    from server.publisher import _describe

    cid = clip(d, tmp_path)
    row = d.get_clip(cid)
    assert "#clutch" in _describe(d, row, "Body")
    assert "#clutch" not in _describe(d, row, "Body", clip_hashtags=False)
