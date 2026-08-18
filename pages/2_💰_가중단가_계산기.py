import datetime

import numpy as np
import pandas as pd
import streamlit as st

from excel_utils import TEMPLATE_HEADERS, build_excel, build_template_excel, comma
from weights import WEIGHT_LABELS, WEIGHTS
from youtube_api import YouTubeAPIError, collect_channel_videos, resolve_channel

st.set_page_config(page_title="가중단가 계산기", page_icon="💰", layout="wide")
st.title("💰 가중단가 계산기")
st.caption(
    "옵션(채널 조합)별로 집행금액을 입력한 엑셀을 업로드하면, 채널 유형별 배점을 적용한 "
    "가중조회수와 가중단가(효율지표)를 자동 계산해 랭킹으로 보여줍니다."
)

api_key = st.secrets.get("YOUTUBE_API_KEY")
if not api_key:
    st.error("YOUTUBE_API_KEY가 설정되지 않았습니다. Streamlit Secrets에 등록해주세요.")
    st.stop()

with st.expander("배점 기준 보기"):
    st.table(pd.DataFrame({"채널 유형": list(WEIGHTS.keys()), "배점": [WEIGHT_LABELS[k] for k in WEIGHTS]}))

st.download_button(
    "📥 입력 템플릿 다운로드",
    data=build_template_excel(),
    file_name="가중단가_입력_템플릿.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

st.markdown(
    """
**작성 방법**
- `NO`, `유튜버`, `옵션명`, `집행금액`은 같은 옵션(채널 조합)의 **첫 행에만** 입력하면 됩니다. (아래 행은 비워두세요)
- `채널유형`이 **유튜브 롱폼 / 유튜브 숏츠**이고 `채널URL`을 입력했다면, `평균조회수`는 비워둬도 자동으로 수집됩니다.
- `채널유형`이 **인스타 릴스 / 틱톡 숏폼**이면 자동 수집이 안 되니 `평균조회수`를 직접 입력해주세요.
"""
)

n_auto = st.number_input("유튜브 자동수집 시 평균 낼 최신 영상 개수", min_value=1, max_value=30, value=5)

uploaded = st.file_uploader("작성한 엑셀 업로드", type=["xlsx"])


@st.cache_data(ttl=1800, show_spinner=False)
def _channel_type_average(_api_key, url, channel_type, n):
    info = resolve_channel(_api_key, url)
    n_longform = n if channel_type == "유튜브 롱폼" else 0
    n_shorts = n if channel_type == "유튜브 숏츠" else 0
    result = collect_channel_videos(_api_key, info["uploads_playlist_id"], n_longform=n_longform, n_shorts=n_shorts)
    videos = result["longform"] if channel_type == "유튜브 롱폼" else result["shorts"]
    views = [v["view_count"] for v in videos if v["view_count"] is not None]
    if not views:
        return None
    return round(sum(views) / len(views))


if uploaded:
    try:
        df = pd.read_excel(uploaded)
    except Exception as e:  # noqa: BLE001
        st.error(f"엑셀을 읽는 중 오류가 발생했습니다: {e}")
        st.stop()

    missing_cols = [c for c in TEMPLATE_HEADERS if c not in df.columns]
    if missing_cols:
        st.error(f"템플릿과 컬럼이 다릅니다. 누락된 컬럼: {missing_cols}")
        st.stop()

    df = df.dropna(how="all").reset_index(drop=True)
    df[["NO", "유튜버", "옵션명", "집행금액(VAT제외, 옵션당 1행만 입력)"]] = df[
        ["NO", "유튜버", "옵션명", "집행금액(VAT제외, 옵션당 1행만 입력)"]
    ].ffill()

    avg_col = "평균조회수(비워두면 유튜브는 자동수집)"
    row_errors = []
    computed_avg = []
    weighted_views = []

    progress = st.progress(0.0, text="가중조회수 계산 중...")
    total = len(df)

    for idx, row in df.iterrows():
        ch_type = row["채널유형"]
        url = row.get("채널URL")
        avg = row.get(avg_col)

        if ch_type not in WEIGHTS:
            row_errors.append(f"{int(row['NO']) if pd.notna(row['NO']) else '?'}행: 채널유형 '{ch_type}' 을(를) 인식할 수 없습니다.")
            computed_avg.append(None)
            weighted_views.append(None)
            progress.progress((idx + 1) / total)
            continue

        if pd.isna(avg):
            if ch_type in ("유튜브 롱폼", "유튜브 숏츠") and isinstance(url, str) and url.strip():
                try:
                    avg = _channel_type_average(api_key, url.strip(), ch_type, int(n_auto))
                    if avg is None:
                        row_errors.append(f"{row['유튜버']} / {ch_type}: 해당 유형의 영상을 찾지 못했습니다.")
                except YouTubeAPIError as e:
                    row_errors.append(f"{row['유튜버']} / {ch_type}: {e}")
                    avg = None
            else:
                row_errors.append(f"{row['유튜버']} / {ch_type}: 평균조회수가 비어있고 자동수집 대상도 아닙니다. 직접 입력해주세요.")
                avg = None

        computed_avg.append(avg)
        weighted_views.append(avg * WEIGHTS[ch_type] if avg is not None else None)
        progress.progress((idx + 1) / total, text=f"{idx + 1}/{total}행 처리")

    progress.empty()

    df["평균조회수(계산값)"] = computed_avg
    df["배점"] = df["채널유형"].map(WEIGHT_LABELS)
    df["가중조회수"] = weighted_views
    df = df.drop(columns=[avg_col])  # 원본 입력값은 계산값 컬럼으로 대체

    if row_errors:
        st.warning("일부 행을 계산하지 못했습니다:\n\n" + "\n".join(f"- {e}" for e in row_errors))

    valid_df = df.dropna(subset=["가중조회수"])
    exec_col = "집행금액(VAT제외, 옵션당 1행만 입력)"

    grouped = (
        valid_df.groupby("NO")
        .agg(
            유튜버=("유튜버", "first"),
            옵션명=("옵션명", "first"),
            채널구성=("채널유형", lambda s: " + ".join(s)),
            가중조회수합산=("가중조회수", "sum"),
            집행금액=(exec_col, "first"),
        )
        .reset_index()
    )

    incomplete_no = set(df["NO"]) - set(valid_df["NO"])
    if incomplete_no:
        grouped = grouped[~grouped["NO"].isin(incomplete_no)]
        st.info(f"일부 채널 데이터가 누락되어 다음 옵션은 랭킹에서 제외했습니다: NO {sorted(incomplete_no)}")

    grouped["가중단가"] = grouped["집행금액"] / grouped["가중조회수합산"]
    grouped = grouped.sort_values("가중단가").reset_index(drop=True)
    grouped.insert(0, "순위", range(1, len(grouped) + 1))

    st.subheader("가중단가 랭킹 (낮을수록 효율적)")
    display_ranking = grouped.copy()
    for c in ["가중조회수합산", "집행금액"]:
        display_ranking[c] = display_ranking[c].apply(comma)
    display_ranking["가중단가"] = display_ranking["가중단가"].apply(lambda x: f"{x:,.1f}")
    st.dataframe(display_ranking, width='stretch', hide_index=True)

    st.subheader("채널별 상세 계산 내역")
    display_detail = df.copy()
    display_detail["평균조회수(계산값)"] = display_detail["평균조회수(계산값)"].apply(comma)
    display_detail["가중조회수"] = display_detail["가중조회수"].apply(
        lambda x: f"{x:,.1f}" if pd.notna(x) else "N/A"
    )
    st.dataframe(display_detail, width='stretch', hide_index=True)

    excel_bytes = build_excel(
        sheets={"가중단가_랭킹": grouped, "채널별_상세": df},
        comma_cols={
            "가중단가_랭킹": ["가중조회수합산", "집행금액"],
            "채널별_상세": ["평균조회수(계산값)"],
        },
        decimal_cols={
            "가중단가_랭킹": ["가중단가"],
            "채널별_상세": ["가중조회수"],
        },
    )
    st.download_button(
        "엑셀로 다운로드",
        data=excel_bytes,
        file_name=f"가중단가_계산결과_{datetime.date.today().isoformat()}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )
