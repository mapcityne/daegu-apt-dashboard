"""대구 아파트 실거래가 대시보드 (2·3단계).

실행:  streamlit run app.py
"""
import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_folium import st_folium

from common import (AREA_LABELS, COORDS_CSV, DAEGU_SIGUNGU, SAMPLE_DIR, TRADES_CSV)
from mapview import build_map

st.set_page_config(page_title="대구 아파트 실거래가", page_icon="🏢", layout="wide")
GU_ORDER = list(DAEGU_SIGUNGU.values())


# ───────────────────────── 데이터 ─────────────────────────
@st.cache_data
def load(trades_path: str, coords_path: str):
    df = pd.read_csv(trades_path, dtype={"지번": str, "단지일련번호": str, "단지키": str},
                     parse_dates=["계약일자", "계약월"])
    df["평형대"] = pd.Categorical(df["평형대"], categories=AREA_LABELS, ordered=True)
    df["해제"] = df["해제"].astype(bool)
    coords = (pd.read_csv(coords_path, dtype={"단지키": str})
              if coords_path else pd.DataFrame(columns=["단지키", "lat", "lon"]))
    coords = coords.dropna(subset=["lat", "lon"])
    return df, coords[["단지키", "lat", "lon"]]


if TRADES_CSV.exists():
    df_all, coords = load(str(TRADES_CSV), str(COORDS_CSV) if COORDS_CSV.exists() else "")
    is_sample = False
elif (SAMPLE_DIR / "daegu_apt_trades.csv").exists():
    df_all, coords = load(str(SAMPLE_DIR / "daegu_apt_trades.csv"),
                          str(SAMPLE_DIR / "complex_coords.csv"))
    is_sample = True
else:
    st.error("데이터가 없습니다. `python collect.py` 로 수집하거나 "
             "`python make_sample.py` 로 샘플을 만드세요.")
    st.stop()

# ───────────────────────── 사이드바 필터 ─────────────────────────
with st.sidebar:
    st.header("필터")
    gus = st.multiselect("구·군", GU_ORDER, default=GU_ORDER)
    areas = st.multiselect("평형대 (전용면적)", AREA_LABELS, default=AREA_LABELS)
    months = sorted(df_all["계약월"].dt.to_period("M").unique())
    m_from, m_to = st.select_slider("기간", options=months, value=(months[0], months[-1]),
                                    format_func=lambda p: p.strftime("%Y.%m"))
    metric = st.radio("가격 기준", ["평당가 (만원/전용3.3㎡)", "거래금액 (억원)"])
    smooth = st.checkbox("3개월 이동평균", value=True)
    excl_cancel = st.checkbox("해제된 거래 제외", value=True)
    excl_direct = st.checkbox("직거래 제외", value=False,
                              help="가족 간 증여성 거래 등 시세와 동떨어진 거래가 섞일 수 있음")
    st.caption(f"데이터: {df_all['계약일자'].min():%Y.%m.%d} ~ {df_all['계약일자'].max():%Y.%m.%d}"
               f"\n\n최근 1~2개월은 신고 기한(30일) 때문에 거래가 덜 잡힌다.")

VAL = "평당가" if metric.startswith("평당가") else "거래금액_억"
UNIT = "만원/3.3㎡" if VAL == "평당가" else "억원"
FMT = (lambda v: f"{v:,.0f}") if VAL == "평당가" else (lambda v: f"{v:,.2f}")

df = df_all[df_all["구군"].isin(gus) & df_all["평형대"].isin(areas)
            & df_all["계약월"].between(m_from.to_timestamp(), m_to.to_timestamp())]
if excl_cancel:
    df = df[~df["해제"]]
if excl_direct and "거래유형" in df.columns:
    df = df[df["거래유형"] != "직거래"]

# ───────────────────────── 헤더·KPI ─────────────────────────
st.title("대구 아파트 매매 실거래가")
if is_sample:
    st.warning("⚠️ 화면 확인용 **가상 샘플 데이터**입니다. 실제 거래가 아닙니다. "
               "`python collect.py` 실행 후 실제 데이터로 바뀝니다.")
if df.empty:
    st.info("조건에 맞는 거래가 없습니다. 필터를 넓혀 보세요.")
    st.stop()

last = df["계약월"].max()
recent = df[df["계약월"] > last - pd.DateOffset(months=3)]
prev = df[(df["계약월"] > last - pd.DateOffset(months=15)) & (df["계약월"] <= last - pd.DateOffset(months=12))]
yoy = (recent["평당가"].median() / prev["평당가"].median() - 1) * 100 if len(prev) and len(recent) else None

k1, k2, k3, k4 = st.columns(4)
k1.metric("거래 건수", f"{len(df):,}건")
k2.metric("중위 거래금액", f"{df['거래금액_억'].median():,.2f}억")
k3.metric("중위 평당가", f"{df['평당가'].median():,.0f}만원")
k4.metric("최근 3개월 평당가 (전년 동기 대비)",
          f"{recent['평당가'].median():,.0f}만원" if len(recent) else "-",
          f"{yoy:+.1f}%" if yoy is not None else None)


def monthly(d, by=None):
    keys = ["계약월"] + ([by] if by else [])
    g = d.groupby(keys, observed=True).agg(값=(VAL, "median"), 건수=(VAL, "size")).reset_index()
    if smooth:
        g = g.sort_values(keys)
        grp = g.groupby(by, observed=True)["값"] if by else g["값"]
        g["값"] = grp.transform(lambda s: s.rolling(3, min_periods=1).mean()) if by \
            else g["값"].rolling(3, min_periods=1).mean()
    return g


# ───────────────────────── 지도 + 구별 추이 ─────────────────────────
left, right = st.columns([1.05, 1])

# 단지별 요약 (기간 내 최근 거래 기준)
lastdeal = df.sort_values("계약일자").groupby("단지키").tail(1)
summary = (df.groupby("단지키").agg(거래수=("단지키", "size"), 중위평당가=("평당가", "median"))
           .join(lastdeal.set_index("단지키")[["단지명", "구군", "법정동", "계약일자", "거래금액_억",
                                             "전용면적", "층", "건축년도"]])
           .reset_index().merge(coords, on="단지키", how="left"))

with left:
    st.subheader("단지 지도")
    on_map = summary.dropna(subset=["lat", "lon"])
    missing = len(summary) - len(on_map)
    if on_map.empty:
        st.info("좌표가 없습니다. `python geocode.py` 를 먼저 실행하세요.")
        clicked = None
    else:
        m = build_map(on_map)
        out = st_folium(m, height=520, use_container_width=True,
                        returned_objects=["last_object_clicked_tooltip"], key="map")
        clicked = (out or {}).get("last_object_clicked_tooltip")
    st.caption("점 크기 = 기간 내 거래 수, 색 = 중위 평당가. 점을 누르면 아래 단지 상세가 바뀐다."
               + (f" 좌표 없는 단지 {missing}개는 표시 안 됨." if missing else ""))

with right:
    st.subheader(f"구·군별 월별 중위 {VAL.replace('_억', '')} 추이")
    g = monthly(df, "구군")
    fig = px.line(g, x="계약월", y="값", color="구군", markers=False,
                  category_orders={"구군": GU_ORDER}, labels={"값": UNIT, "계약월": ""},
                  hover_data={"건수": True})
    fig.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=0), legend_title=None,
                      hovermode="x unified")
    st.plotly_chart(fig, width="stretch")

    vol = df.groupby(["계약월", "구군"], observed=True).size().reset_index(name="건수")
    fig2 = px.bar(vol, x="계약월", y="건수", color="구군", category_orders={"구군": GU_ORDER},
                  labels={"계약월": ""})
    fig2.update_layout(height=200, margin=dict(l=0, r=0, t=10, b=0), showlegend=False)
    st.markdown("**월별 거래량**")
    st.plotly_chart(fig2, width="stretch")

# ───────────────────────── 단지 상세 ─────────────────────────
st.divider()
st.subheader("단지 상세")
opts = summary.sort_values("거래수", ascending=False)
labels = dict(zip(opts["단지명"] + " · " + opts["구군"] + " " + opts["법정동"], opts["단지키"]))
names = list(labels)
if st.session_state.get("cx") not in labels:
    st.session_state.pop("cx", None)
if clicked in labels and st.session_state.get("_last_click") != clicked:
    st.session_state["_last_click"] = clicked
    st.session_state["cx"] = clicked
pick = st.selectbox("단지 선택 (지도에서 눌러도 됨, 입력해서 검색 가능)", names, key="cx")
cx = df[df["단지키"] == labels[pick]]

c1, c2 = st.columns([1.4, 1])
with c1:
    fig3 = px.scatter(cx, x="계약일자", y=VAL, color="평형대",
                      category_orders={"평형대": AREA_LABELS},
                      hover_data={"전용면적": ":.1f", "층": True, "거래금액_억": ":.2f"},
                      labels={VAL: UNIT, "계약일자": ""})
    g3 = monthly(cx)
    fig3.add_scatter(x=g3["계약월"], y=g3["값"], mode="lines", name="월 중위",
                     line=dict(color="gray", dash="dot"))
    fig3.update_layout(height=340, margin=dict(l=0, r=0, t=10, b=0), legend_title=None)
    st.plotly_chart(fig3, width="stretch")
with c2:
    by_area = (cx.groupby("평형대", observed=True)
               .agg(건수=("거래금액_억", "size"), 중위_억=("거래금액_억", "median"),
                    최고_억=("거래금액_억", "max"), 최저_억=("거래금액_억", "min"))
               .round(2).reset_index())
    st.markdown("**평형대별 요약**")
    st.dataframe(by_area, hide_index=True, width="stretch")

st.markdown("**최근 거래**")
cols = ["계약일자", "전용면적", "층", "거래금액_억", "평당가", "거래유형", "해제여부"]
show = cx.sort_values("계약일자", ascending=False)[[c for c in cols if c in cx.columns]].head(30)
st.dataframe(show.style.format({"계약일자": "{:%Y.%m.%d}", "전용면적": "{:.2f}",
                                "거래금액_억": "{:.2f}", "평당가": "{:,.0f}"}),
             hide_index=True, width="stretch")
st.caption("출처: 국토교통부 아파트 매매 실거래가 (공공데이터포털). 평당가 = 거래금액 ÷ 전용면적(평).")
