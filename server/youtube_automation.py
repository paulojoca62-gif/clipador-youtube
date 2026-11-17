"""Watched channels published straight to YouTube, no third-party service.

The hands-off path in server/automation.py hands finished clips to a
`publisher(d, **kwargs)` callable. Upstream that callable is WoopSocial, a paid
service, even when the only destination is YouTube, while the app already has
its own direct YouTube upload (server/youtube_api.py + server/publisher.py)
used from the editor. This module joins the two: for the "youtube" platform a
watched channel's clips become ordinary publish jobs, uploaded by the same
PublishWorker the editor's Upload button uses, with the same quota ledger,
credential store and post-upload checks.

Routing (see `routing_publisher`):
  - "youtube" goes direct whenever direct YouTube publishing is switched on
    and a channel is connected (Settings -> Publish to YouTube).
  - Every other platform still goes through WoopSocial, unchanged.

Spacing: the YouTube Data API's default quota allows about six uploads a day
(publish/quota.py keeps the count). When a watch spreads its posts, each clip
is uploaded right away as private with a scheduled publish time, so YouTube
releases it on the day and nothing here has to stay running.
"""

from collections.abc import Callable
from datetime import datetime, timezone

from core.state import StateDB
from server import youtube_service as service

PLATFORM = "youtube"

# publish_jobs states that hold a clip: on its way, or already up.
_HOLDING = ("queued", "running", "done")

# YouTube's limits on a video's metadata.
TITLE_MAX = 100
DESCRIPTION_MAX = 5000


class NotReady(RuntimeError):
    """Direct YouTube publishing isn't set up. Only a person can fix this, so
    automation leaves the video waiting with this message (not retryable)."""

    retryable = False


def is_ready(d: StateDB) -> bool:
    """Switched on, with at least one connected channel to upload to."""
    return service.is_enabled(d) and bool(service.default_channel_id(d))


def already_held(d: StateDB, clip) -> bool:
    """Whether this clip already has a YouTube publish job queued, running or
    done. Matched on the clip's lasting identity too, because a re-render gives
    the same clip a new id. A failed or cancelled job holds nothing: that is
    exactly what a retry is for."""
    marks = ",".join("?" * len(_HOLDING))
    row = d.conn.execute(
        f"SELECT 1 FROM publish_jobs WHERE status IN ({marks}) AND "
        "(clip_id = ? OR (video_id != '' AND video_id = ? AND start_s = ? AND end_s = ?)) LIMIT 1",
        (*_HOLDING, clip["id"], clip["video_id"] or "", clip["start_s"], clip["end_s"]),
    ).fetchone()
    return row is not None


def committed_times(d: StateDB) -> list[str]:
    """Publish times YouTube jobs already hold, so a new video's clips queue
    behind the previous one's instead of doubling up on the same days."""
    import json

    out: list[str] = []
    for row in d.conn.execute(
        "SELECT request FROM publish_jobs WHERE status IN ('queued', 'running', 'done')"
    ):
        try:
            when = (json.loads(row["request"] or "{}") or {}).get("publish_at")
        except (TypeError, ValueError):
            continue
        if when:
            out.append(when)
    return out


def schedule(d: StateDB, count: int, *, per_day: int, gap_hours: float, day_start: str) -> list[str]:
    """When each of `count` clips goes public, on a daily budget. Empty when
    the watch posts everything now (per_day 0)."""
    from publish.schedule import MIN_LEAD_SECONDS, daily_after, to_rfc3339
    from server.woopsocial_service import first_slot_at

    if not count or not per_day:
        return []
    from datetime import timedelta

    first = datetime.now(timezone.utc) + timedelta(seconds=MIN_LEAD_SECONDS + 60)
    if day_start:
        first = first_slot_at(day_start)
    return daily_after(
        committed_times(d), int(count), int(per_day), float(gap_hours or 1),
        to_rfc3339(first), local_start=bool(day_start),
    )


def _hashtag_line(tags: list[str]) -> str:
    seen: list[str] = []
    for tag in tags:
        cleaned = str(tag).lstrip("#").strip()
        if cleaned and cleaned.lower() not in {t.lower() for t in seen}:
            seen.append(cleaned)
    return " ".join("#" + t for t in seen)


def build_request(clip, *, settings: dict, privacy: str, publish_at: str | None,
                  lead_hashtags: list[str], footer: str, channel_id: str | None) -> dict:
    """The publish job's request, in the shape POST /clips/{id}/publish stores.

    The clip's own hashtags are added by the worker (publisher._describe), so
    only the creator's standing tags and the watch's footer are added here."""
    title = (clip["title"] if "title" in clip.keys() else "") or clip["hook"] or "Clip"
    title = " ".join(str(title).split())[:TITLE_MAX]
    body = (clip["description"] if "description" in clip.keys() else "") or ""
    parts = [p for p in (body.strip(), footer.strip(), _hashtag_line(lead_hashtags)) if p]
    description = "\n\n".join(parts)[:DESCRIPTION_MAX]
    return {
        "title": title,
        "description": description,
        "tags": [],
        "category_id": str(settings.get("category_id") or "22"),
        # A scheduled video must go up private; YouTube flips it on the day.
        "privacy": "private" if publish_at else privacy,
        "publish_at": publish_at,
        "made_for_kids": bool(settings.get("made_for_kids")),
        "contains_synthetic_media": False,
        "embeddable": True,
        "public_stats_viewable": True,
        "license": "youtube",
        "default_language": None,
        "notify_subscribers": bool(settings.get("notify_subscribers", True)),
        "playlist_id": None,
        "thumbnail": None,
        "channel_id": channel_id,
    }


def youtube_publisher(publish_worker) -> Callable[..., dict]:
    """A `publisher` for automation.install that queues direct YouTube uploads.

    Answers in the shape automation.publish reads: `started` (one entry per
    clip queued, with `scheduled_for`) and `skipped` (with a `reason`)."""

    def publish(
        d: StateDB,
        *,
        clip_ids: list[int],
        platforms: list[str] | None = None,
        lead_hashtags: list[str] | None = None,
        ai_hashtags: bool = True,
        per_day: int = 0,
        gap_hours: float = 1,
        day_start: str = "",
        overrides: dict | None = None,
        footer: str = "",
        **_ignored,
    ) -> dict:
        import json
        from pathlib import Path

        if not is_ready(d):
            raise NotReady(
                "YouTube publishing isn't set up. Turn it on and connect your channel in "
                "Settings -> Publish to YouTube."
            )
        settings = service.load_settings(d)
        wanted = ((overrides or {}).get(PLATFORM) or {}).get("privacy") or settings.get("privacy")
        privacy = wanted if wanted in ("public", "unlisted", "private") else "private"
        channel_id = service.default_channel_id(d)

        started: list[dict] = []
        skipped: list[dict] = []
        sendable = []
        for clip_id in clip_ids:
            clip = d.get_clip(clip_id)
            if clip is None:
                skipped.append({"clip_id": clip_id, "reason": "no such clip"})
            elif not (clip["path"] and Path(clip["path"]).exists()):
                skipped.append({"clip_id": clip_id, "reason": "no rendered file yet"})
            elif already_held(d, clip):
                skipped.append({"clip_id": clip_id, "reason": "already sent"})
            else:
                sendable.append(clip)

        times = schedule(d, len(sendable), per_day=per_day, gap_hours=gap_hours, day_start=day_start)
        for index, clip in enumerate(sendable):
            when = times[index] if index < len(times) else None
            request = build_request(
                clip, settings=settings, privacy=privacy, publish_at=when,
                lead_hashtags=list(lead_hashtags or []), footer=footer, channel_id=channel_id,
            )
            if not ai_hashtags:
                # The worker adds the clip's own hashtags from this column.
                request["skip_clip_hashtags"] = True
            job_id = d.add_publish_job(
                int(clip["id"]), json.dumps(request), video_id=clip["video_id"] or "",
                start_s=clip["start_s"], end_s=clip["end_s"],
            )
            started.append({"clip_id": int(clip["id"]), "publish_job_id": job_id,
                            "platform": PLATFORM, "scheduled_for": when or ""})
        if started and publish_worker is not None:
            publish_worker.notify()
        return {"started": started, "skipped": skipped}

    return publish


def routing_publisher(direct: Callable[..., dict], fallback: Callable[..., dict] | None) -> Callable[..., dict]:
    """Send "youtube" direct when that is set up; everything else (and YouTube
    when direct publishing is off) through `fallback`, which is WoopSocial."""

    def publish(d: StateDB, **kwargs) -> dict:
        platforms = list(kwargs.get("platforms") or [])
        if not platforms:
            raise NotReady("Choose where this channel's clips go.")
        go_direct = PLATFORM in platforms and is_ready(d)
        rest = [p for p in platforms if p != PLATFORM] if go_direct else platforms
        if rest and fallback is None:
            if PLATFORM in rest:
                # Nothing else could take YouTube: say what to set up.
                return direct(d, **kwargs)
            raise NotReady("Publishing to " + ", ".join(rest) + " needs WoopSocial, which isn't set up.")
        out: dict = {"started": [], "skipped": []}
        if go_direct:
            got = direct(d, **{**kwargs, "platforms": [PLATFORM]})
            out["started"] += got.get("started", [])
            out["skipped"] += got.get("skipped", [])
        if rest:
            got = fallback(d, **{**kwargs, "platforms": rest})
            out["started"] += got.get("started", [])
            out["skipped"] += got.get("skipped", [])
        return out

    return publish
