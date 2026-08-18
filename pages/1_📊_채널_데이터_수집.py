import datetime

import pandas as pd
import streamlit as st

from excel_utils import build_excel, comma
from youtube_api import YouTubeAPIError, collect_channel_videos, resolve_channel

st.set_page_config(page_title="채널 데이터 수집", page_icon="📊", layout="wide")
st.title("📊 채널 데이터 수집")
st.caption("유튜브 채널 URL을 한 줄에 하나씩 입력하면, 최신 롱폼·숏츠 영상의 조회수/좋아요/댓글을 자동으로 모아드립니다.")

api_key = st.secrets.get("YOUTUBE_API_KEY")
if not api_key:
    st.error("YOUTUBE_API_KEY가 설정되지 않았습니다. Streamlit Secrets에 등록해주세요.")
    st.stop()

urls_text = st.text_area(
    "채널 URL 목록 (한 줄에 하나씩)",
    height=160,
    placeholder="https://www.youtube.com/@채널명1\nhttps://www.youtube.com/@채널명2",
)

col1, col2, col3 = st.columns(3)
with col1:
    n_longform = st.number_input("채널당 수집할 롱폼 영상 개수", min_value=0, max_value=30, value=5)
with col2:
    n_shorts = st.number_input("채널당 수집할 숏츠 영상 개수", min_value=0, max_value=30, value=5)
with col3:
    max_scan = st.number_input("채널당 최대 스캔 영상 수 (탐색 상한)", min_value=20, max_value=500, value=200, step=20)

run = st.button("수집 시작", type="primary")


def _detail_row(channel_title, kind, v):
    return {
        "채널명": channel_title,
        "유형": kind,
        "제목": v["title"],
        "업로드일": v["published_at"],
        "조회수": v["view_count"],
        "좋아요": v["like_count"],
        "댓글": v["comment_count"],
        "영상길이(초)": v["duration_sec"],
        "URL": v["url"],
    }


if run:
    urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
    if not urls:
        st.warning("채널 URL을 최소 1개 입력해주세요.")
        st.stop()

    summary_rows = []
    detail_rows = []
    errors = []

    overall_progress = st.progress(0.0, text="채널 수집 준비 중...")
    status_box = st.empty()

    for idx, url in enumerate(urls):
        try:
            status_box.info(f"[{idx + 1}/{len(urls)}] {url} — 채널 정보 조회 중...")
            info = resolve_channel(api_key, url)

            def _progress_cb(scanned, got_lf, got_sh, _url=url, _idx=idx):
                status_box.info(
                    f"[{_idx + 1}/{len(urls)}] {info['title']} — 스캔 {scanned}개 "
                    f"(롱폼 {got_lf}/{n_longform}, 숏츠 {got_sh}/{n_shorts})"
                )

            result = collect_channel_videos(
                api_key,
                info["uploads_playlist_id"],
                n_longform=int(n_longform),
                n_shorts=int(n_shorts),
                max_scan=int(max_scan),
                progress_cb=_progress_cb,
            )

            lf, sh = result["longform"], result["shorts"]
            lf_views = [v["view_count"] for v in lf if v["view_count"] is not None]
            sh_views = [v["view_count"] for v in sh if v["view_count"] is not None]

            summary_rows.append(
                {
                    "채널명": info["title"],
                    "핸들": info["handle"],
                    "구독자수": info["subscriber_count"],
                    "롱폼_수집개수": len(lf),
                    "롱폼_평균조회수": round(sum(lf_views) / len(lf_views)) if lf_views else None,
                    "숏츠_수집개수": len(sh),
                    "숏츠_평균조회수": round(sum(sh_views) / len(sh_views)) if sh_views else None,
                    "스캔한_영상수": result["scanned"],
                    "채널URL": url,
                }
            )

            for v in lf:
                detail_rows.append(_detail_row(info["title"], "롱폼", v))
            for v in sh:
                detail_rows.append(_detail_row(info["title"], "숏츠", v))

        except YouTubeAPIError as e:
            errors.append(f"{url}: {e}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url}: 알 수 없는 오류 - {e}")

        overall_progress.progress((idx + 1) / len(urls), text=f"{idx + 1}/{len(urls)} 채널 처리 완료")

    status_box.empty()
    overall_progress.empty()

    if errors:
        st.error("일부 채널을 처리하지 못했습니다:\n\n" + "\n".join(f"- {e}" for e in errors))

    if summary_rows:
        summary_df = pd.DataFrame(summary_rows)
        detail_df = pd.DataFrame(detail_rows)

        st.subheader("채널 요약")
        display_summary = summary_df.copy()
        for c in ["구독자수", "롱폼_평균조회수", "숏츠_평균조회수"]:
            display_summary[c] = display_summary[c].apply(comma)
        st.dataframe(display_summary, width='stretch', hide_index=True)

        st.subheader("영상별 상세")
        display_detail = detail_df.copy()
        for c in ["조회수", "좋아요", "댓글"]:
            display_detail[c] = display_detail[c].apply(comma)
        st.dataframe(display_detail, width='stretch', hide_index=True)

        excel_bytes = build_excel(
            sheets={"채널요약": summary_df, "영상별상세": detail_df},
            comma_cols={
                "채널요약": ["구독자수", "롱폼_평균조회수", "숏츠_평균조회수"],
                "영상별상세": ["조회수", "좋아요", "댓글"],
            },
        )
        st.download_button(
            "엑셀로 다운로드",
            data=excel_bytes,
            file_name=f"채널데이터_{datetime.date.today().isoformat()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )

        st.session_state["last_collection_summary"] = summary_df
    elif not errors:
        st.warning("수집된 데이터가 없습니다.")
