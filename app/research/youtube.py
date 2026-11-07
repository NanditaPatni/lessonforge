"""YouTube discovery.

* YouTube Data API v3 when YOUTUBE_API_KEY is set (accurate durations / view counts),
  otherwise DuckDuckGo video search (no key).
* Transcripts (youtube-transcript-api) are fetched so a video's relevance is judged on
  what is actually *said* in it, not just its title — and so QC can quote it as evidence.
"""
from __future__ import annotations

import re

import httpx

from ..config import get_settings
from .credibility import score_video

_ID_RE = re.compile(r"(?:v=|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})")


def video_id(url: str) -> str | None:
    m = _ID_RE.search(url or "")
    return m.group(1) if m else None


def _iso_duration(s: str) -> int:
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def _clock_duration(s: str) -> int:
    parts = [int(p) for p in re.findall(r"\d+", s or "")]
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


def search_videos(query: str, n: int = 6) -> list[dict]:
    key = get_settings().youtube_key
    return _yt_api(query, n, key) if key else _ddg_videos(query, n)


def _yt_api(query: str, n: int, key: str) -> list[dict]:
    r = httpx.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={"part": "snippet", "q": query, "type": "video", "maxResults": n, "key": key,
                "relevanceLanguage": "en", "videoEmbeddable": "true", "safeSearch": "strict"},
        timeout=20,
    )
    r.raise_for_status()
    items = r.json().get("items", [])
    ids = [i["id"]["videoId"] for i in items if i.get("id", {}).get("videoId")]
    if not ids:
        return []
    d = httpx.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"part": "contentDetails,statistics,snippet", "id": ",".join(ids), "key": key},
        timeout=20,
    )
    d.raise_for_status()
    out = []
    for v in d.json().get("items", []):
        sn, st = v.get("snippet", {}), v.get("statistics", {})
        views = int(st.get("viewCount", 0) or 0)
        out.append({
            "type": "video", "video_id": v["id"], "title": sn.get("title", ""),
            "url": f"https://www.youtube.com/watch?v={v['id']}", "channel": sn.get("channelTitle", ""),
            "published": sn.get("publishedAt", "")[:10], "duration_sec": _iso_duration(v["contentDetails"]["duration"]),
            "views": views, "description": sn.get("description", "")[:600],
            "credibility": score_video(sn.get("channelTitle", ""), views),
        })
    return out


def _ddg_videos(query: str, n: int) -> list[dict]:
    from ddgs import DDGS
    out = []
    from .web import with_deadline
    for x in with_deadline(lambda: DDGS(timeout=15).videos(query, max_results=n * 2) or [], 45):
        url = x.get("content", "")
        vid = video_id(url)
        if not vid:
            continue
        views = int((x.get("statistics") or {}).get("viewCount") or 0)
        channel = x.get("uploader") or x.get("publisher") or ""
        out.append({
            "type": "video", "video_id": vid, "title": x.get("title", ""),
            "url": f"https://www.youtube.com/watch?v={vid}", "channel": channel,
            "published": (x.get("published") or "")[:10], "duration_sec": _clock_duration(x.get("duration", "")),
            "views": views, "description": (x.get("description") or "")[:600],
            "credibility": score_video(channel, views),
        })
        if len(out) >= n:
            break
    return out


def search_videos_via_web(query: str, n: int = 6) -> list[dict]:
    """Fallback when video search returns nothing: a normal web search limited to youtube.com."""
    from .web import search_web
    out, seen = [], set()
    for r in search_web(f"{query} site:youtube.com", n=n * 2):
        vid = video_id(r.get("url", ""))
        if not vid or vid in seen:
            continue
        seen.add(vid)
        title = re.sub(r"\s*[-|]\s*YouTube\s*$", "", r.get("title", "")).strip()
        out.append({
            "type": "video", "video_id": vid, "title": title or query, "url": f"https://www.youtube.com/watch?v={vid}",
            "channel": "", "published": "", "duration_sec": 0, "views": 0, "description": (r.get("snippet") or "")[:600],
            "credibility": score_video("", None),
        })
        if len(out) >= n:
            break
    return out


def get_transcript(vid: str) -> str | None:
    from .web import with_deadline
    try:
        return with_deadline(_get_transcript, 30, vid)
    except Exception:
        return None


def _get_transcript(vid: str) -> str | None:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return None
    try:
        api = YouTubeTranscriptApi()
        if hasattr(api, "fetch"):  # v1.x
            fetched = api.fetch(vid, languages=["en", "en-US", "en-GB"])
            return " ".join(s.text for s in fetched)
        rows = YouTubeTranscriptApi.get_transcript(vid, languages=["en"])  # legacy 0.x
        return " ".join(r["text"] for r in rows)
    except Exception:
        return None
