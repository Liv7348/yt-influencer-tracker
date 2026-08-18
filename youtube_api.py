"""YouTube Data API v3 헬퍼 함수 모음."""

import re
import time
from urllib.parse import urlparse

import requests

API_BASE = "https://www.googleapis.com/youtube/v3"

# YouTube 쇼츠는 세로/정사각 영상이며 최대 3분(180초)까지 허용된다 (2024년 기준 정책).
SHORT_MAX_SECONDS = 180


class YouTubeAPIError(Exception):
    pass


def _get(path: str, api_key: str, **params):
    params["key"] = api_key
    resp = requests.get(f"{API_BASE}/{path}", params=params, timeout=15)
    data = resp.json()
    if "error" in data:
        msg = data["error"].get("message", str(data["error"]))
        raise YouTubeAPIError(f"{path} 호출 실패: {msg}")
    return data


def parse_channel_ref(url_or_handle: str) -> dict:
    """채널 URL/핸들 문자열을 channels.list 조회 파라미터로 변환."""
    s = url_or_handle.strip()
    if not s:
        raise YouTubeAPIError("채널 URL이 비어 있습니다.")

    if s.startswith("@"):
        return {"forHandle": s}

    if not s.startswith("http"):
        # 순수 채널명/핸들로 간주
        return {"forHandle": f"@{s}"}

    parsed = urlparse(s)
    path_parts = [p for p in parsed.path.split("/") if p]

    if not path_parts:
        raise YouTubeAPIError(f"채널 URL을 해석할 수 없습니다: {url_or_handle}")

    first = path_parts[0]
    if first.startswith("@"):
        return {"forHandle": first}
    if first == "channel" and len(path_parts) > 1:
        return {"id": path_parts[1]}
    if first == "c" and len(path_parts) > 1:
        return {"customUrlSearch": path_parts[1]}
    if first == "user" and len(path_parts) > 1:
        return {"forUsername": path_parts[1]}

    # 알 수 없는 형식이면 첫 경로를 핸들로 시도
    return {"forHandle": f"@{first}"}


def resolve_channel(api_key: str, url_or_handle: str) -> dict:
    """채널 URL/핸들 -> 채널 기본 정보(구독자수, 업로드 재생목록 등)."""
    ref = parse_channel_ref(url_or_handle)
    part = "snippet,statistics,contentDetails"

    if "customUrlSearch" in ref:
        # /c/커스텀명 형식은 공식 조회 파라미터가 없어 search로 채널 ID를 먼저 찾는다.
        search = _get("search", api_key, part="snippet", q=ref["customUrlSearch"], type="channel", maxResults=1)
        items = search.get("items", [])
        if not items:
            raise YouTubeAPIError(f"채널을 찾을 수 없습니다: {url_or_handle}")
        data = _get("channels", api_key, part=part, id=items[0]["snippet"]["channelId"])
    else:
        data = _get("channels", api_key, part=part, **ref)

    items = data.get("items", [])
    if not items:
        raise YouTubeAPIError(f"채널을 찾을 수 없습니다: {url_or_handle}")

    ch = items[0]
    stats = ch.get("statistics", {})
    return {
        "channel_id": ch["id"],
        "title": ch["snippet"]["title"],
        "handle": ch["snippet"].get("customUrl", ""),
        "thumbnail": ch["snippet"]["thumbnails"]["default"]["url"],
        "subscriber_count": int(stats.get("subscriberCount", 0)) if not stats.get("hiddenSubscriberCount") else None,
        "total_view_count": int(stats.get("viewCount", 0)),
        "video_count": int(stats.get("videoCount", 0)),
        "uploads_playlist_id": ch["contentDetails"]["relatedPlaylists"]["uploads"],
    }


def _parse_duration(iso_duration: str) -> int:
    """ISO 8601 duration (PT1H2M3S) -> 초."""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration)
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def fetch_videos_stats(api_key: str, video_ids: list) -> list:
    """videos.list로 여러 영상의 통계/길이를 한 번에 조회 (최대 50개씩)."""
    results = []
    for chunk in _chunks(video_ids, 50):
        data = _get(
            "videos",
            api_key,
            part="snippet,contentDetails,statistics",
            id=",".join(chunk),
        )
        for v in data.get("items", []):
            stats = v.get("statistics", {})
            duration_sec = _parse_duration(v["contentDetails"]["duration"])
            results.append(
                {
                    "video_id": v["id"],
                    "title": v["snippet"]["title"],
                    "published_at": v["snippet"]["publishedAt"][:10],
                    "duration_sec": duration_sec,
                    "is_short": duration_sec > 0 and duration_sec <= SHORT_MAX_SECONDS,
                    "view_count": int(stats["viewCount"]) if "viewCount" in stats else None,
                    "like_count": int(stats["likeCount"]) if "likeCount" in stats else None,
                    "comment_count": int(stats["commentCount"]) if "commentCount" in stats else None,
                    "url": f"https://www.youtube.com/watch?v={v['id']}",
                }
            )
    return results


def collect_channel_videos(
    api_key: str,
    uploads_playlist_id: str,
    n_longform: int,
    n_shorts: int,
    max_scan: int = 200,
    progress_cb=None,
) -> dict:
    """업로드 재생목록을 최신순으로 스캔하며 롱폼 n개 + 숏츠 n개를 채울 때까지 수집."""
    longform, shorts = [], []
    scanned = 0
    page_token = None

    while scanned < max_scan and (len(longform) < n_longform or len(shorts) < n_shorts):
        data = _get(
            "playlistItems",
            api_key,
            part="contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=50,
            pageToken=page_token or "",
        )
        items = data.get("items", [])
        if not items:
            break

        ids = [i["contentDetails"]["videoId"] for i in items]
        stats = fetch_videos_stats(api_key, ids)
        for v in stats:
            scanned += 1
            if v["is_short"]:
                if len(shorts) < n_shorts:
                    shorts.append(v)
            else:
                if len(longform) < n_longform:
                    longform.append(v)

        if progress_cb:
            progress_cb(scanned, len(longform), len(shorts))

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return {"longform": longform, "shorts": shorts, "scanned": scanned}
