"""대구 아파트 실거래가 대시보드.

실행:  streamlit run app.py
"""
import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_folium import st_folium

import analysis as A
from common import (AREA_LABELS, COORDS_CSV, DAEGU_SIGUNGU, SAMPLE_DIR, TRADES_CSV)
from mapview import build_map

st.set_page_config(page_title="대구 아파트 실거래가", page_icon="🏢", layout="wide")
GU_ORDER = list(DAEGU_SIGUNGU.values())
CHART = dict(margin=dict(l=0, r=0, t=10, b=0), legend_title=None)


# ───────────────────────── 데이터 ─────────────────────────
@st.cache_data
def load(trades_path: str, coords_path: str):
    df = pd.read_csv(trades_path, dtype={"지번": str, "단지일련번호": str, "단지키": str},
                     parse_dates=["계약일자", "계약월"])
    df["평형대"] = pd.Categorical(df["평형대"], categories=AREA_LABELS, ordered=True)
    df["해제"] = df["해제"].astype(bool)
    df = A.prepare(df)
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
    m_from, m_to = st.select_slider("기간 (지도·추이·TOP10)", options=months,
                                    value=(months[0], months[-1]),
                                    format_func=lambda p: p.strftime("%Y.%m"))
    # 마지막 달이 월초 며칠분뿐이면 직전 달을 기본 기준월로
    last_day = df_all["계약일자"].max()
    default_ref = months[-2] if len(months) > 1 and last_day.day < 25 else months[-1]
    ref_p = st.select_slider("분석 기준월", options=months, value=default_ref,
                             format_func=lambda p: p.strftime("%Y.%m"),
                             help="상승·하락, 신고가, 예산 찾기의 '최근 3개월'이 끝나는 달. "
                                  "최근 달은 신고 기한(30일) 때문에 거래가 덜 잡히므로 "
                                  "한 달 앞당겨 보는 것도 방법이다.")
    metric = st.radio("가격 기준", ["평당가 (만원/전용3.3㎡)", "거래금액 (억원)"])
    smooth = st.checkbox("3개월 이동평균", value=True)
    excl_direct = st.checkbox("직거래 제외", value=True,
                              help="가족 간 증여성 거래 등 시세와 동떨어진 거래가 섞일 수 있음")
    st.caption(f"데이터: {df_all['계약일자'].min():%Y.%m.%d} ~ {df_all['계약일자'].max():%Y.%m.%d}"
               "\n\n해제된 거래는 모든 분석에서 제외.")

VAL = "평당가" if metric.startswith("평당가") else "거래금액_억"
UNIT = "만원/3.3㎡" if VAL == "평당가" else "억원"
REF = ref_p.to_timestamp()

# base: 기간 제외 필터만 적용 (변동률 계산에 과거 데이터 필요)
mask = df_all["구군"].isin(gus) & df_all["평형대"].isin(areas)
raw_base = df_all[mask]                       # 해제 포함 (해제율 계산용)
base = raw_base[~raw_base["해제"]]
if excl_direct and "거래유형" in base.columns:
    base = base[base["거래유형"] != "직거래"]
base = base[base["계약월"] <= REF]
df = base[base["계약월"].between(m_from.to_timestamp(), m_to.to_timestamp())]

# ───────────────────────── 헤더·KPI ─────────────────────────
st.title("대구 아파트 매매 실거래가")
if is_sample:
    st.warning("⚠️ 화면 확인용 **가상 샘플 데이터**입니다. 실제 거래가 아닙니다.")
if base.empty or df.empty:
    st.info("조건에 맞는 거래가 없습니다. 필터를 넓혀 보세요.")
    st.stop()


@st.cache_data
def cached_analyses(key, _base, ref):
    pc = A.price_changes(_base, ref, min_n=1)
    highs = A.mark_new_highs(_base)
    peak = A.peak_recovery(_base, ref)
    return pc, highs, peak


fkey = (tuple(gus), tuple(areas), excl_direct, str(REF), is_sample)
pc_all, highs, peak = cached_analyses(fkey, base, REF)
now = A.window(highs, REF, 0)
ago = A.window(highs, REF, 12)
p12 = pc_all["1년전대비"].dropna()
p6 = pc_all["6개월전대비"].dropna()

st.caption(f"최근 3개월 = {A.window_label(REF, 0)} · 6개월 전 = {A.window_label(REF, 6)} · "
           f"1년 전 = {A.window_label(REF, 12)} · 변동률은 같은 단지·같은 면적끼리 비교한 중위값")
k = st.columns(6)
k[0].metric("최근 3개월 거래", f"{len(now):,}건",
            f"{(len(now) / len(ago) - 1) * 100:+.0f}% 전년동기" if len(ago) else None)
k[1].metric("중위 거래가", f"{now['거래금액_억'].median():,.2f}억")
k[2].metric("중위 평당가", f"{now['평당가'].median():,.0f}만원")
k[3].metric("6개월 변동 (동일 단지)", f"{p6.median():+.1f}%" if len(p6) else "-")
k[4].metric("1년 변동 (동일 단지)", f"{p12.median():+.1f}%" if len(p12) else "-",
            f"상승 단지 {(p12 > 0).mean() * 100:.0f}%" if len(p12) else None, delta_color="off")
k[5].metric("신고가 비율", f"{now['신고가'].mean() * 100:.1f}%" if len(now) else "-",
            help="최근 3개월 거래 중 같은 단지·면적의 수집기간 내 최고가를 경신한 거래 비율. "
                 "높을수록 매수세가 강하다는 신호.")


def monthly(d, by=None):
    keys = ["계약월"] + ([by] if by else [])
    g = d.groupby(keys, observed=True).agg(값=(VAL, "median"), 건수=(VAL, "size")).reset_index()
    if smooth:
        g = g.sort_values(keys)
        if by:
            g["값"] = g.groupby(by, observed=True)["값"].transform(
                lambda s: s.rolling(3, min_periods=1).mean())
        else:
            g["값"] = g["값"].rolling(3, min_periods=1).mean()
    return g


def name_col(t):
    return t["단지명"] + " " + t["면적타입"].astype(str) + "㎡"


PCT = st.column_config.NumberColumn(format="%+.1f%%")
EOK = st.column_config.NumberColumn(format="%.2f억")


def eok(label):
    return st.column_config.NumberColumn(label, format="%.2f억")

MAN = st.column_config.NumberColumn("평당가(만원)", format="localized")
DATE = st.column_config.DateColumn(format="YYYY.MM.DD")

tabs = st.tabs(["📊 시장 요약", "🗺️ 지도·단지", "🏆 구별 TOP 10", "📈 상승·하락",
                "🔥 신고가·전고점", "💰 예산으로 찾기"])

# ═════════════════════════ 1. 시장 요약 ═════════════════════════
with tabs[0]:
    st.subheader("구·군별 시장 지표")
    gs = A.gu_summary(base, REF, [g for g in GU_ORDER if g in gus])
    gs["해제 비율(%)"] = gs["구군"].map(A.cancel_rate(raw_base[raw_base["계약월"] <= REF],
                                                    REF, GU_ORDER))
    styled = (gs.style
              .format({"최근 거래(3개월)": "{:,.0f}", "중위 거래가(억)": "{:.2f}",
                       "중위 평당가(만원)": "{:,.0f}", "거래량 증감(전년동기)": "{:+.0f}%",
                       "6개월 변동(%)": "{:+.1f}", "1년 변동(%)": "{:+.1f}",
                       "1년 상승 단지 비율(%)": "{:.0f}", "신고가 비율(%)": "{:.1f}",
                       "직거래 비율(%)": "{:.1f}", "해제 비율(%)": "{:.1f}"}, na_rep="-")
              .background_gradient("RdBu_r", subset=["6개월 변동(%)", "1년 변동(%)"],
                                   vmin=-10, vmax=10)
              .background_gradient("RdBu_r", subset=["거래량 증감(전년동기)"], vmin=-50, vmax=50)
              .background_gradient("Oranges", subset=["중위 평당가(만원)", "신고가 비율(%)"]))
    st.dataframe(styled, hide_index=True, width="stretch")
    with st.expander("지표 읽는 법"):
        st.markdown(
            "- **거래량 증감**: 거래량은 가격보다 먼저 움직이는 경향이 있다. 증가 + 가격 상승이면 상승 국면.\n"
            "- **6개월·1년 변동**: 같은 단지·같은 면적의 3개월 중위가 비교의 중위값. 단지 구성 차이를 걷어낸 값.\n"
            "- **1년 상승 단지 비율**: 50% 이상이면 상승이 일부 단지가 아니라 넓게 퍼졌다는 뜻.\n"
            "- **신고가 비율**: 수집기간(약 3년) 내 최고가를 경신한 거래 비중. 매수 심리 지표.\n"
            "- **직거래·해제 비율**: 높으면 특수 거래나 가격 띄우기 가능성. 개별 거래 해석 시 주의.")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**구·군별 월별 중위 {'평당가' if VAL == '평당가' else '거래가'} 추이**")
        g = monthly(df, "구군")
        fig = px.line(g, x="계약월", y="값", color="구군", category_orders={"구군": GU_ORDER},
                      labels={"값": UNIT, "계약월": ""}, hover_data={"건수": True})
        fig.update_layout(height=330, hovermode="x unified", **CHART)
        st.plotly_chart(fig, width="stretch")
    with c2:
        st.markdown("**월별 거래량**")
        vol = df.groupby(["계약월", "구군"], observed=True).size().reset_index(name="건수")
        fig2 = px.bar(vol, x="계약월", y="건수", color="구군", category_orders={"구군": GU_ORDER},
                      labels={"계약월": ""})
        fig2.update_layout(height=330, **CHART)
        st.plotly_chart(fig2, width="stretch")

    c3, c4 = st.columns(2)
    with c3:
        st.markdown("**연식별 중위 평당가** (최근 12개월) — 신축 프리미엄 확인")
        w12 = A.window(base, REF, 0, span=12)
        age = (w12.groupby(["구군", "연식구간"], observed=True)["평당가"]
               .agg(["median", "size"]).reset_index())
        age = age[age["size"] >= 5]
        fig3 = px.bar(age, x="구군", y="median", color="연식구간", barmode="group",
                      category_orders={"구군": GU_ORDER, "연식구간": A.AGE_LABELS},
                      labels={"median": "만원/3.3㎡", "구군": ""},
                      color_discrete_sequence=px.colors.sequential.Viridis_r)
        fig3.update_layout(height=330, **CHART)
        st.plotly_chart(fig3, width="stretch")
        st.caption("구간별 거래 5건 미만은 생략.")
    with c4:
        st.markdown("**가격대별 거래 구성** (최근 12개월) — 내 예산대의 거래가 어디서 일어나나")
        pb = (w12.groupby(["구군", "가격대"], observed=True).size()
              .reset_index(name="건수"))
        pb["비중"] = pb["건수"] / pb.groupby("구군")["건수"].transform("sum") * 100
        fig4 = px.bar(pb, x="구군", y="비중", color="가격대",
                      category_orders={"구군": GU_ORDER, "가격대": A.PRICE_LABELS},
                      labels={"비중": "%", "구군": ""}, hover_data={"건수": True},
                      color_discrete_sequence=["#c6dbef", "#6baed6", "#3182bd", "#08519c", "#08306b"])
        fig4.update_layout(height=330, **CHART)
        st.plotly_chart(fig4, width="stretch")

# ═════════════════════════ 2. 지도·단지 ═════════════════════════
with tabs[1]:
    lastdeal = df.sort_values("계약일자").groupby("단지키").tail(1)
    summary = (df.groupby("단지키").agg(거래수=("단지키", "size"), 중위평당가=("평당가", "median"))
               .join(lastdeal.set_index("단지키")[["단지명", "구군", "법정동", "계약일자",
                                                 "거래금액_억", "전용면적", "층", "건축년도"]])
               .reset_index().merge(coords, on="단지키", how="left"))
    on_map = summary.dropna(subset=["lat", "lon"])
    missing = len(summary) - len(on_map)
    clicked = None
    if on_map.empty:
        st.info("좌표가 없습니다. `python geocode.py` 를 먼저 실행하세요.")
    else:
        out = st_folium(build_map(on_map), height=500, use_container_width=True,
                        returned_objects=["last_object_clicked_tooltip"], key="map")
        clicked = (out or {}).get("last_object_clicked_tooltip")
    st.caption("점 크기 = 기간 내 거래 수, 색 = 중위 평당가. 점을 누르면 아래 단지 상세가 바뀐다."
               + (f" 좌표 없는 단지 {missing}개는 표시 안 됨." if missing else ""))

    st.subheader("단지 상세")
    opts = summary.sort_values("거래수", ascending=False)
    labels = dict(zip(opts["단지명"] + " · " + opts["구군"] + " " + opts["법정동"], opts["단지키"]))
    if st.session_state.get("cx") not in labels:
        st.session_state.pop("cx", None)
    if clicked in labels and st.session_state.get("_last_click") != clicked:
        st.session_state["_last_click"] = clicked
        st.session_state["cx"] = clicked
    pick = st.selectbox("단지 선택 (지도에서 눌러도 됨, 입력해서 검색 가능)", list(labels), key="cx")
    key = labels[pick]
    cx_all = base[base["단지키"] == key]
    cx = df[df["단지키"] == key]

    info = cx_all.iloc[-1]
    pcx = pc_all[pc_all["단지키"] == key]
    pkx = peak[peak["단지키"] == key]
    m = st.columns(4)
    m[0].metric("준공", f"{int(info['건축년도'])}년" if pd.notna(info["건축년도"]) else "-")
    m[1].metric("기간 내 거래", f"{len(cx)}건")
    m[2].metric("1년 변동 (면적별 중위)",
                f"{pcx['1년전대비'].median():+.1f}%" if pcx["1년전대비"].notna().any() else "-")
    m[3].metric("전고점 대비 (면적별 중위)",
                f"{pkx['고점대비(%)'].median():.0f}%" if len(pkx) else "-")

    c1, c2 = st.columns([1.4, 1])
    with c1:
        fig5 = px.scatter(cx, x="계약일자", y=VAL, color="평형대",
                          category_orders={"평형대": AREA_LABELS},
                          hover_data={"전용면적": ":.1f", "층": True, "거래금액_억": ":.2f"},
                          labels={VAL: UNIT, "계약일자": ""})
        g5 = monthly(cx)
        fig5.add_scatter(x=g5["계약월"], y=g5["값"], mode="lines", name="월 중위",
                         line=dict(color="gray", dash="dot"))
        fig5.update_layout(height=340, **CHART)
        st.plotly_chart(fig5, width="stretch")
    with c2:
        st.markdown("**면적별 시세 요약**")
        t = (pcx[["면적타입", "최근", "6개월전", "1년전", "1년전대비"]]
             .merge(pkx[["면적타입", "최고가", "고점대비(%)"]], on="면적타입", how="outer")
             .sort_values("면적타입"))
        st.dataframe(t, hide_index=True, width="stretch", column_config={
            "면적타입": st.column_config.NumberColumn("전용㎡"), "최근": EOK, "6개월전": EOK,
            "1년전": EOK, "최고가": EOK, "1년전대비": PCT,
            "고점대비(%)": st.column_config.NumberColumn(format="%.0f%%")})
        st.caption("최근·6개월전·1년전 = 각 3개월 중위 거래가")

    st.markdown("**최근 거래**")
    show = cx_all.sort_values("계약일자", ascending=False)[
        ["계약일자", "전용면적", "층", "거래금액_억", "평당가", "거래유형"]].head(30)
    st.dataframe(show, hide_index=True, width="stretch", column_config={
        "계약일자": DATE, "거래금액_억": eok("거래가"), "평당가": MAN,
        "전용면적": st.column_config.NumberColumn(format="%.2f㎡")})

# ═════════════════════════ 3. 구별 TOP 10 ═════════════════════════
with tabs[2]:
    gu_pick = st.radio("구·군", ["대구 전체"] + [g for g in GU_ORDER if g in gus], horizontal=True)
    d3 = df if gu_pick == "대구 전체" else df[df["구군"] == gu_pick]
    st.caption(f"기간: {m_from.strftime('%Y.%m')} ~ {min(m_to, ref_p).strftime('%Y.%m')} "
               "(사이드바 기간·기준월 적용)")
    one = st.toggle("최고가 TOP 10은 단지당 1건만", value=True)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**최고가 거래 TOP 10**")
        tt = A.top_trades(d3, one_per_complex=one)
        tt.insert(0, "순위", range(1, len(tt) + 1))
        st.dataframe(tt[["순위", "단지명", "구군", "법정동", "전용면적", "층", "거래금액_억",
                         "평당가", "계약일자"]],
                     hide_index=True, width="stretch", column_config={
                         "거래금액_억": eok("거래가"), "평당가": MAN, "계약일자": DATE,
                         "전용면적": st.column_config.NumberColumn(format="%.1f㎡")})
    with c2:
        st.markdown("**평당가 상위 단지 TOP 10** (거래 3건 이상)")
        tc = A.top_complexes(d3)
        tc.insert(0, "순위", range(1, len(tc) + 1))
        st.dataframe(tc[["순위", "단지명", "구군", "법정동", "건축년도", "거래수", "중위평당가",
                         "중위거래가", "최고거래가"]],
                     hide_index=True, width="stretch", column_config={
                         "중위평당가": MAN, "중위거래가": EOK, "최고거래가": EOK,
                         "건축년도": st.column_config.NumberColumn(format="%d")})

    with st.expander("구·군별 평당가 1~10위 한눈에 보기", expanded=gu_pick == "대구 전체"):
        grid = {}
        for g_ in [g for g in GU_ORDER if g in gus]:
            t_ = A.top_complexes(df[df["구군"] == g_])
            grid[g_] = (t_["단지명"] + " (" + t_["중위평당가"].map("{:,.0f}".format) + ")").tolist()
        grid = pd.DataFrame({k: pd.Series(v, dtype=object) for k, v in grid.items()})
        grid.index = [f"{i}위" for i in range(1, len(grid) + 1)]
        st.dataframe(grid, width="stretch")
        st.caption("괄호 안은 기간 내 중위 평당가(만원/전용3.3㎡), 거래 3건 이상 단지.")

    st.markdown("**거래량 TOP 10 단지** — 수요가 꾸준한(환금성 좋은) 단지")
    tv = (d3.groupby("단지키").agg(단지명=("단지명", "last"), 구군=("구군", "last"),
                                  법정동=("법정동", "last"), 건축년도=("건축년도", "last"),
                                  거래수=("거래금액", "size"), 중위거래가=("거래금액_억", "median"),
                                  중위평당가=("평당가", "median"))
          .nlargest(10, "거래수").reset_index(drop=True).round({"중위평당가": 0}))
    tv.insert(0, "순위", range(1, len(tv) + 1))
    st.dataframe(tv, hide_index=True, width="stretch", column_config={
        "중위평당가": MAN, "중위거래가": EOK,
        "건축년도": st.column_config.NumberColumn(format="%d")})

# ═════════════════════════ 4. 상승·하락 ═════════════════════════
with tabs[3]:
    c1, c2, c3 = st.columns([1, 1, 2])
    basis = c1.radio("비교 시점", ["1년 전", "6개월 전"], horizontal=True)
    min_n = c2.number_input("창별 최소 거래건수", 1, 5, 2,
                            help="건수가 적으면 한두 건의 특이 거래로 순위가 흔들린다.")
    col = "1년전대비" if basis == "1년 전" else "6개월전대비"
    ref_col = "1년전" if basis == "1년 전" else "6개월전"
    pc = A.price_changes(base, REF, min_n=int(min_n)).dropna(subset=[col])
    c3.caption(f"최근 {A.window_label(REF, 0)} 중위가 vs "
               f"{basis} {A.window_label(REF, 12 if basis == '1년 전' else 6)} 중위가. "
               f"같은 단지·같은 전용면적만 비교. 비교 가능 {len(pc):,}개")

    if pc.empty:
        st.info("비교 가능한 단지가 없습니다. 최소 거래건수를 낮춰 보세요.")
    else:
        fig6 = px.histogram(pc, x=col, nbins=40, color="구군", category_orders={"구군": GU_ORDER},
                            labels={col: f"{basis} 대비 변동률(%)"})
        fig6.add_vline(x=0, line_dash="dot", line_color="gray")
        fig6.update_layout(height=230, bargap=0.05, **CHART)
        st.plotly_chart(fig6, width="stretch")
        up, down = (pc[col] > 0).sum(), (pc[col] < 0).sum()
        st.caption(f"상승 {up:,}개 · 하락 {down:,}개 · 중위 {pc[col].median():+.1f}%")

        cfg = {"단지": st.column_config.TextColumn(width="medium"), "최근": EOK, ref_col: EOK,
               col: st.column_config.NumberColumn("변동률", format="%+.1f%%"), "건축년도": st.column_config.NumberColumn(format="%d")}
        cols = ["순위", "단지", "구군", col, ref_col, "최근", "건축년도", "법정동",
                f"{ref_col}_건수", "최근_건수"]
        cfg.update({f"{ref_col}_건수": st.column_config.NumberColumn(f"{basis} 건수"),
                    "최근_건수": st.column_config.NumberColumn("최근 건수")})
        a, b = st.columns(2)
        for box, title, sub in [(a, "📈 상승률 TOP 20", pc[pc[col] > 0].sort_values(col, ascending=False)),
                                (b, "📉 하락률 TOP 20", pc[pc[col] < 0].sort_values(col))]:
            t = sub.head(20).copy()
            if t.empty:
                box.markdown(f"**{title}**")
                box.info("해당 단지 없음")
                continue
            t["단지"] = name_col(t)
            t["순위"] = range(1, len(t) + 1)
            box.markdown(f"**{title}**")
            box.dataframe(t[cols], hide_index=True, width="stretch", column_config=cfg,
                          height=min(740, 38 + 35 * len(t)))

# ═════════════════════════ 5. 신고가·전고점 ═════════════════════════
with tabs[4]:
    st.markdown(f"**최근 3개월({A.window_label(REF, 0)}) 신고가 거래** — "
                "같은 단지·면적의 수집기간 내 최고가 경신 (이전 거래 2건 이상)")
    nh = now[now["신고가"]].copy()
    if nh.empty:
        st.info("최근 3개월 신고가 거래가 없습니다.")
    else:
        nh["직전최고가_억"] = nh["직전최고가"] / 10_000
        nh["경신폭(%)"] = (nh["거래금액"] / nh["직전최고가"] - 1) * 100
        nh["단지"] = name_col(nh)
        cnt = nh.groupby("구군").size().reindex(GU_ORDER).dropna()
        st.caption(" · ".join(f"{g} {int(n)}건" for g, n in cnt.items()))
        st.dataframe(nh.sort_values("계약일자", ascending=False)
                     [["계약일자", "단지", "구군", "법정동", "층", "직전최고가_억", "거래금액_억",
                       "경신폭(%)"]],
                     hide_index=True, width="stretch", height=360, column_config={
                         "계약일자": DATE, "직전최고가_억": eok("직전 최고가"), "거래금액_억": eok("거래가"),
                         "경신폭(%)": PCT, "단지": st.column_config.TextColumn(width="medium")})

    st.markdown("**전고점 대비 현재 가격** — 수집기간 최고가 대비 최근 3개월 중위가")
    c1, c2 = st.columns([1, 3])
    lo_rec = c1.slider("전고점 대비 범위(%)", 50, 110, (50, 90))
    pk = peak[peak["고점대비(%)"].between(*lo_rec)].copy()
    pk["단지"] = name_col(pk)
    c2.caption("고점 대비가 낮다고 저평가라는 뜻은 아니다. 고점이 일시적 과열이었거나, "
               "입지·노후도 요인일 수 있으므로 단지 상세에서 거래 흐름을 함께 확인할 것.")
    st.dataframe(pk.sort_values("고점대비(%)")
                 [["단지", "구군", "법정동", "건축년도", "최고가", "최고가일", "최근중위",
                   "최근건수", "고점대비(%)"]],
                 hide_index=True, width="stretch", height=420, column_config={
                     "최고가": EOK, "최근중위": eok("최근 중위"), "최고가일": DATE,
                     "최근건수": st.column_config.NumberColumn("최근 건수"),
                     "건축년도": st.column_config.NumberColumn(format="%d"),
                     "고점대비(%)": st.column_config.ProgressColumn(
                         format="%.0f%%", min_value=0, max_value=110),
                     "단지": st.column_config.TextColumn(width="medium")})

# ═════════════════════════ 6. 예산으로 찾기 ═════════════════════════
with tabs[5]:
    c1, c2, c3, c4 = st.columns(4)
    budget = c1.slider("예산 (억원)", 0.5, 20.0, (3.0, 5.0), step=0.25)
    sp = c2.select_slider("시세 기준 기간", [3, 6, 12], value=6, format_func=lambda v: f"최근 {v}개월")
    min_year = c3.number_input("준공 연도 이후", 1970, 2030, 2000, step=5)
    sort_by = c4.selectbox("정렬", ["평당가 높은 순", "1년 상승률 높은 순", "최근 거래 순",
                                   "거래 많은 순", "신축 순"])
    bs = A.budget_search(base, REF, budget[0], budget[1], months=sp)
    bs = bs[bs["건축년도"].fillna(0) >= min_year]
    order = {"평당가 높은 순": ("평당가", False), "1년 상승률 높은 순": ("1년전대비", False),
             "최근 거래 순": ("최근거래일", False), "거래 많은 순": ("거래수", False),
             "신축 순": ("건축년도", False)}[sort_by]
    bs = bs.sort_values(order[0], ascending=order[1], na_position="last")
    bs["단지"] = name_col(bs)
    st.caption(f"{budget[0]:.2f}~{budget[1]:.2f}억, {min_year}년 이후 준공 · "
               f"후보 {len(bs):,}개 (단지·면적 기준) · 사이드바 구·군·평형대 필터 적용")
    if not bs.empty:
        by_gu = bs.groupby("구군").size().reindex(GU_ORDER).dropna()
        fig7 = px.bar(x=by_gu.index, y=by_gu.values, labels={"x": "", "y": "후보 수"})
        fig7.update_layout(height=200, **CHART)
        st.plotly_chart(fig7, width="stretch")
    st.dataframe(bs[["단지", "구군", "법정동", "건축년도", "중위거래가", "최근거래가", "최근거래일",
                     "거래수", "평당가", "1년전대비"]],
                 hide_index=True, width="stretch", height=480, column_config={
                     "중위거래가": EOK, "최근거래가": EOK, "최근거래일": DATE, "평당가": MAN,
                     "1년전대비": st.column_config.NumberColumn("1년 변동", format="%+.1f%%"),
                     "건축년도": st.column_config.NumberColumn(format="%d"),
                     "단지": st.column_config.TextColumn(width="medium")})
    st.caption("1년 변동 = 같은 길이 기간의 1년 전 중위가 대비. 거래가에는 취득세·중개보수 등 부대비용이 빠져 있다. 실거래가는 계약 기준이며 "
               "호가·급매 여부는 반영되지 않는다.")

st.divider()
st.caption("출처: 국토교통부 아파트 매매 실거래가 (공공데이터포털). 평당가 = 거래금액 ÷ 전용면적(평). "
           "투자 판단의 참고 자료이며 정확성을 보증하지 않는다.")
