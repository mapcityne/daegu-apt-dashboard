"""전국 아파트 실거래가 시장 신호 대시보드.

실행:  streamlit run app.py
데이터: data/out (로컬) → 없으면 GitHub Release 'data'에서 내려받는다.
"""
import io
import json
import os
import re
import urllib.request

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import analysis as A
import briefing as B
from common import AREA_LABELS, OUT_DIR, enrich
import mapview as MV
from regions import GEOJSON, SIDO_SHORT, regions

st.set_page_config(page_title="아파트 시장 신호", page_icon="🏢", layout="wide")
RELEASE = os.environ.get(
    "DATA_URL", "https://github.com/mapcityne/daegu-apt-dashboard/releases/download/data")
CHART = dict(margin=dict(l=0, r=0, t=10, b=0), legend_title=None)
UP, DOWN, FLAT = "#d6336c", "#1c7ed6", "#868e96"


# ───────────────────────── 데이터 ─────────────────────────
def _read(name: str) -> bytes:
    local = OUT_DIR / name
    if local.exists():
        return local.read_bytes()
    with urllib.request.urlopen(f"{RELEASE}/{name}", timeout=60) as r:
        return r.read()


@st.cache_data(ttl=3 * 3600, show_spinner="데이터 불러오는 중…")
def load_table(name: str) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(_read(name)))


@st.cache_data(ttl=3 * 3600)
def load_meta() -> dict:
    return json.loads(_read("meta.json"))


@st.cache_data(ttl=3 * 3600, show_spinner="지역 거래 불러오는 중…")
def load_trades(sido: str) -> pd.DataFrame:
    df = pd.read_parquet(io.BytesIO(_read(f"trades_{sido}.parquet")))
    df = A.prepare(enrich(df))
    df["평형대"] = pd.Categorical(df["평형대"], categories=AREA_LABELS, ordered=True)
    return df


@st.cache_data
def load_geo() -> dict:
    return json.loads(GEOJSON.read_text(encoding="utf-8"))


try:
    meta = load_meta()
    stats = load_table("stats.parquet")
    index = load_table("index.parquet")
except Exception as e:  # noqa: BLE001
    st.title("아파트 실거래가 시장 신호")
    st.info("📦 전국 데이터를 처음 수집하고 있습니다. 수집·집계가 끝나면(보통 1~2시간, 한도에 걸리면 "
            "다음 날까지) 이 화면에 자동으로 표시됩니다.\n\n"
            "진행 상황: GitHub 저장소 Actions 탭의 `update-data` 실행 기록")
    st.caption(f"상세: {type(e).__name__}: {e}")
    st.stop()

REF = pd.Timestamp(meta["기준월"] + "-01")
REG = regions()
S = stats.set_index("코드")


BOLD = re.compile(r"\*\*(.+?)\*\*")


def md_list(lines):
    """마크다운 **굵게**가 한글 조사 앞에서 깨지는 문제를 피해 <b>로 렌더링."""
    items = [BOLD.sub(r"<b>\1</b>", x).replace("~", "\\~") for x in lines]
    st.markdown("\n".join(f"- {x}" for x in items), unsafe_allow_html=True)


def srow(code) -> dict:
    return B.row_dict(S.loc[code]) if code in S.index else {"판정": "판단 보류"}


# ───────────────────────── 사이드바: 지역 선택 ─────────────────────────
with st.sidebar:
    st.header("지역")
    sidos = ["00"] + sorted(REG["sidocd"].unique(), key=lambda c: list(SIDO_SHORT).index(c))
    sido = st.selectbox("시도", sidos, format_func=lambda c: "전국" if c == "00" else SIDO_SHORT[c],
                        key="sido")
    sgg = "all"
    if sido != "00":
        subs = REG[REG["sidocd"] == sido].sort_values("sggcd")
        opts = ["all"] + subs["sggcd"].tolist()
        names = dict(zip(subs["sggcd"], subs["sggnm"]))
        sgg = st.selectbox("시군구", opts, format_func=lambda c: "전체" if c == "all" else names[c],
                           key="sgg")
    st.divider()
    st.header("상세 탭 필터")
    areas = st.multiselect("평형대 (전용면적)", AREA_LABELS, default=AREA_LABELS)
    excl_direct = st.checkbox("직거래 제외", value=True,
                              help="가족 간 증여성 거래 등 시세와 동떨어진 거래가 섞일 수 있음")
    metric = st.radio("가격 기준", ["평당가 (만원/전용3.3㎡)", "거래금액 (억원)"])
    st.divider()
    col = meta.get("수집") or {}
    st.caption(f"기준월 **{REF:%Y.%m}** · 데이터 {meta['데이터_시작'][:7]} ~ {meta['데이터_끝']}\n\n"
               f"거래 {meta['거래수']:,}건 · 갱신 {meta['생성'][:16].replace('T', ' ')}")
    if col and not col.get("complete", True):
        st.warning(f"전국 수집 진행 중: {col['cells_done']:,}/{col['cells_total']:,}칸 "
                   f"({col['cells_done'] / col['cells_total']:.0%}). 빠진 지역·기간은 매일 자동으로 채워집니다.")

level = "전국" if sido == "00" else ("시도" if sgg == "all" else "시군구")
code = sido if sgg == "all" else sgg
s = srow(code)
title = "전국" if level == "전국" else s.get("이름", code)
parent = srow("00") if level == "시도" else srow(sido) if level == "시군구" else None
VAL = "평당가" if metric.startswith("평당가") else "거래금액_억"
UNIT = "만원/3.3㎡" if VAL == "평당가" else "억원"

if level == "전국":
    children = stats[stats["단계"] == "시도"].copy()
    child_word = "시도"
elif level == "시도":
    children = stats[(stats["단계"] == "시군구") & (stats["상위"] == sido)].copy()
    children["이름"] = children["코드"].map(REG.set_index("sggcd")["sggnm"])
    child_word = "시군구"
else:
    dong = load_table("dong.parquet")
    children = dong[dong["시군구코드"] == sgg].rename(
        columns={"법정동": "이름", "동일단지_1년": "1년"}).copy()
    children = children[children["거래수_3개월"].fillna(0) >= 3]
    child_word = "법정동"

# ───────────────────────── 헤더: 방향 신호 ─────────────────────────
j = s.get("판정", "판단 보류")
st.markdown(
    f"<div style='display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:4px 0 2px'>"
    f"<span style='font-size:2rem;font-weight:700'>{title}</span>"
    f"<span style='background:{B.COLOR[j]};color:white;border-radius:8px;padding:4px 14px;"
    f"font-size:1.4rem;font-weight:700'>{B.ARROW[j]} {j}</span>"
    f"<span style='color:#868e96'>기준 {REF:%Y.%m} · 같은 단지·같은 면적 가격지수 기준</span></div>",
    unsafe_allow_html=True)
note = A.drivers(s, parent)
if j != "판단 보류":
    st.caption("판정: 최근 3개월 평균 지수가 직전 3개월보다 +1% 이상이면 상승, −1% 이하면 하락, "
               "그 사이면 보합.")

k = st.columns(3) + st.columns(3)
k[0].metric("3개월 변동", B.p(s.get("3개월")))
k[1].metric("6개월 변동", B.p(s.get("6개월")))
k[2].metric("1년 변동", B.p(s.get("1년")))
k[3].metric("상승 단지 비율", B.p(s.get("상승비율"), 0, False) if s.get("상승비율") is not None else "-",
            help="1년 전과 비교 가능한 단지·면적 중 가격이 오른 비율. 50%보다 높을수록 상승이 넓게 퍼짐")
k[4].metric("거래량 (전년 동기)", B.p(s.get("거래량증감"), 0),
            help="신고 지연(계약 후 30일)을 감안해 기준월 직전 3개월로 비교")
k[5].metric("신고가 비율", B.p(s.get("신고가비율"), 1, False) if s.get("신고가비율") is not None else "-",
            help="최근 3개월 거래 중 같은 단지·면적의 수집기간 최고가를 경신한 비율")

# 하위 지역 신호 칩
if len(children) and level != "시군구":
    chips = []
    for _, r in children.sort_values("1년", ascending=False, na_position="last").iterrows():
        jj = r.get("판정") if isinstance(r.get("판정"), str) else "판단 보류"
        ch = r.get("1년")
        chips.append(f"<span style='display:inline-block;margin:2px;padding:3px 9px;border-radius:14px;"
                     f"border:1px solid {B.COLOR[jj]};color:{B.COLOR[jj]};font-size:.85rem'>"
                     f"{B.ARROW[jj]} {r['이름']} {B.p(ch) if pd.notna(ch) else ''}</span>")
    st.markdown(f"<div style='margin:6px 0 4px'><b>{child_word}별 신호</b> "
                f"<span style='color:#868e96;font-size:.8rem'>(화살표=3개월 판정, 숫자=1년 변동)</span>"
                f"<br>{''.join(chips)}</div>", unsafe_allow_html=True)

need_trades = level != "전국"
trades = None
if need_trades:
    try:
        trades = load_trades(sido)
    except Exception as e:  # noqa: BLE001
        st.warning(f"이 지역 거래 파일을 아직 받지 못했습니다 ({e}). 수집이 끝나면 표시됩니다.")

tabs = st.tabs(["🧭 브리핑", "🗺️ 지도", "📈 지수 추이", "🏆 TOP·상승하락", "🔥 신고가·전고점",
                "💰 예산으로 찾기", "🏢 단지 상세"])

# ═════════════════════════ 1. 브리핑 ═════════════════════════
with tabs[0]:
    st.subheader(f"{title} 시장 브리핑 ({REF:%Y.%m} 기준)")
    st.markdown("**1. 시장 전체**")
    md_list(B.market_section(s, child_word))

    if len(children):
        ctab, cnotes = B.children_section(children)
        if len(ctab):
            st.markdown(f"**2. {child_word}별 차별화**")
            show = ctab[["구분", "이름", "1년", "특징"]].rename(columns={"이름": child_word, "1년": "1년 변동"})
            st.dataframe(show.head(40), hide_index=True, width="stretch", column_config={
                "1년 변동": st.column_config.NumberColumn(format="%+.1f%%")})
            if cnotes:
                md_list(cnotes)

    st.markdown("**3. 왜 오르나·내리나 — 데이터로 본 주도 요인**")
    if note:
        md_list(note)
    else:
        st.markdown("- 뚜렷한 주도 요인이 보이지 않습니다(연식·면적·지역 간 차이가 작음).")
    lead = s.get("상승주도") or []
    if lead:
        st.markdown("상승 폭이 큰 단지(양쪽 시점 각 2건 이상 거래): " +
                    ", ".join(f"{x['단지']}({x['지역']}) {B.p(x['변동'])}" for x in lead[:5]))
    top = s.get("거래상위") or []
    if top:
        st.markdown("거래가 가장 많은 대장 단지(최근 12개월): " +
                    ", ".join(f"{x['단지']}({x['지역']}, {x['거래수']}건"
                              + (f", 1년 {B.p(x['변동'])}" if x.get("변동") is not None else "") + ")"
                              for x in top))
    ext = A.external_factors(s, SIDO_SHORT.get(sido) if level == "시군구" else None)
    if ext:
        st.markdown("**공공통계로 본 수요·공급 요인**")
        md_list(ext)
        st.caption("출처: 국토교통부 시·군·구별 미분양현황, 주택건설 준공실적(시도) · 국가데이터처 시군구별 이동자수 "
                   "(KOSIS). '시 전체 값'은 일반구 통계가 없어 시 단위 수치를 보여 준 것입니다.")

    sec = B.age_section(s)
    if sec:
        st.markdown("**4. 신축 쏠림**")
        md_list(sec)
    sec = B.area_section(s)
    if sec:
        st.markdown("**5. 면적별**")
        md_list(sec)
    sec = B.buyer_section(s)
    if sec:
        st.markdown("**6. 매수자 관점 참고 수치**")
        md_list(sec)
    with st.expander("이 자료로 알 수 없는 것"):
        st.markdown("- 호가·매물 수, 전세가(갭), 동·층·향별 선호, 급매 여부\n"
                    f"- 수집기간({meta['데이터_시작'][:7]}~) 이전 고점 대비 위치. '전고점'은 이 기간 안의 최고가\n"
                    "- 미래 가격. 이 자료는 과거 계약 기록입니다.")

# ═════════════════════════ 2. 지도 ═════════════════════════
with tabs[1]:
    if level == "시군구":
        st.info("시군구 안에서는 법정동별 비교표를 보여 줍니다. 단지 지도는 좌표 변환이 끝나면 추가됩니다.")
        if len(children):
            fig = px.bar(children.dropna(subset=["1년"]).sort_values("1년"), x="1년", y="이름",
                         orientation="h", color="1년", color_continuous_scale=["#1c7ed6", "#f1f3f5", "#d6336c"],
                         color_continuous_midpoint=0, labels={"1년": "1년 변동(%)", "이름": ""})
            fig.update_layout(height=max(300, 22 * len(children)), coloraxis_showscale=False, **CHART)
            st.plotly_chart(fig, width="stretch")
    else:
        mopt = {"1년 변동(%)": "1년", "3개월 변동(%)": "3개월", "상승 단지 비율(%)": "상승비율",
                "거래량 증감(%)": "거래량증감", "중위 평당가(만원)": "중위평당가"}
        mname = st.radio("색 기준", list(mopt), horizontal=True)
        mcol = mopt[mname]
        sg = stats[stats["단계"] == "시군구"].copy()
        if level == "시도":
            sg = sg[sg["상위"] == sido]
        geo = load_geo()
        feats = [f for f in geo["features"] if f["id"] in set(sg["코드"])]
        geo_sub = {"type": "FeatureCollection", "features": feats}
        sg["판정표시"] = sg["판정"].map(lambda x: f"{B.ARROW.get(x, '')} {x}")
        diverging = mcol in ("1년", "3개월", "거래량증감")
        rng = {"1년": 8, "3개월": 3, "거래량증감": 50}.get(mcol)
        scale = (["#1c7ed6", "#f1f3f5", "#d6336c"] if diverging else "Oranges")
        if diverging:
            vmin, vmax = -rng, rng
        else:
            q = sg[mcol].dropna()
            vmin, vmax = (q.quantile(0.05), q.quantile(0.95)) if len(q) else (0, 1)

        def _hv(r):
            f = lambda v, fmt: (fmt.format(v) if pd.notna(v) else "-")  # noqa: E731
            return (f"<b>{r['이름']}</b> {B.ARROW.get(r['판정'], '')} {r['판정']}<br>"
                    f"1년 {f(r['1년'], '{:+.1f}%')} · 3개월 {f(r['3개월'], '{:+.1f}%')}<br>"
                    f"상승 단지 {f(r['상승비율'], '{:.0f}%')} · 거래량 {f(r['거래량증감'], '{:+.0f}%')}<br>"
                    f"평당 {f(r['중위평당가'], '{:,.0f}만원')} · 최근 3개월 {f(r['거래수_3개월'], '{:,.0f}건')}")
        fig = MV.choropleth(geo_sub, dict(zip(sg["코드"], sg[mcol])),
                            {r["코드"]: _hv(r) for _, r in sg.iterrows()}, scale=scale, vmin=vmin,
                            vmax=vmax, title=mname, height=680 if level == "전국" else 560)
        st.plotly_chart(fig, width="stretch", config={"scrollZoom": True})
        st.caption("거래가 적은 지역은 회색(판단 보류)일 수 있습니다. "
                   "경계: 통계청 SGIS 행정구역(2026.10) · vuski/admdongkor 가공(CC BY 4.0)")
        rank = sg.dropna(subset=[mcol]).sort_values(mcol, ascending=False)
        c1, c2 = st.columns(2)
        cols = ["이름", "판정", "1년", "3개월", "상승비율", "거래량증감", "중위평당가", "거래수_3개월"]
        cfg = {"1년": st.column_config.NumberColumn("1년", format="%+.1f%%"),
               "3개월": st.column_config.NumberColumn("3개월", format="%+.1f%%"),
               "상승비율": st.column_config.NumberColumn("상승 단지", format="%.0f%%"),
               "거래량증감": st.column_config.NumberColumn("거래량", format="%+.0f%%"),
               "중위평당가": st.column_config.NumberColumn("평당가(만원)", format="localized"),
               "거래수_3개월": st.column_config.NumberColumn("거래(3개월)")}
        c1.markdown(f"**{mname} 상위 15**")
        c1.dataframe(rank[cols].head(15), hide_index=True, width="stretch", column_config=cfg)
        c2.markdown(f"**{mname} 하위 15**")
        c2.dataframe(rank[cols].tail(15).iloc[::-1], hide_index=True, width="stretch", column_config=cfg)

# ═════════════════════════ 3. 지수 추이 ═════════════════════════
with tabs[2]:
    st.markdown("**동일단지 가격지수** — 같은 단지·같은 면적의 가격 변화만 모은 지수 "
                "(수집 첫 달 = 100, 3개월 이동평균)")
    lines = [("전국", "00")]
    if level != "전국":
        lines.append((SIDO_SHORT[sido], sido))
    if level == "시군구":
        lines.append((title, sgg))
    pick = st.multiselect(f"비교할 {child_word if level != '시군구' else '지역'} 추가",
                          children["이름"].tolist() if level != "시군구" else [], key="idx_pick")
    name2code = dict(zip(children["이름"], children.get("코드", children["이름"])))
    lines += [(n, name2code[n]) for n in pick]
    rows = []
    for n, c in lines:
        x = index[index["코드"] == c].sort_values("계약월")
        if len(x):
            rows.append(pd.DataFrame({"계약월": x["계약월"], "지수": x["지수"].rolling(3, min_periods=1).mean(),
                                      "지역": n}))
    if rows:
        d = pd.concat(rows)
        fig = px.line(d, x="계약월", y="지수", color="지역", labels={"계약월": ""})
        fig.add_hline(y=100, line_dash="dot", line_color="#adb5bd")
        fig.update_layout(height=420, hovermode="x unified", **CHART)
        st.plotly_chart(fig, width="stretch")
        x = index[index["코드"] == code].sort_values("계약월")
        if len(x):
            fig2 = px.bar(x, x="계약월", y="거래수", labels={"계약월": "", "거래수": "거래(건)"})
            fig2.update_layout(height=200, **CHART)
            st.markdown(f"**{title} 월별 거래량** (마지막 1~2개월은 신고 지연으로 적게 잡힘)")
            st.plotly_chart(fig2, width="stretch")
    else:
        st.info("거래가 적어 지수를 계산하지 못했습니다.")


# ───────────────────────── 상세 탭 공통 ─────────────────────────
def detail_base():
    if trades is None:
        return None
    d = trades[trades["평형대"].isin(areas) & ~trades["해제"] & (trades["계약월"] <= REF)]
    if excl_direct:
        d = d[d["거래유형"] != "직거래"]
    if level == "시군구":
        d = d[d["시군구코드"] == sgg]
    return d


PCT = st.column_config.NumberColumn(format="%+.1f%%")
EOK = st.column_config.NumberColumn(format="%.2f억")
MAN = st.column_config.NumberColumn("평당가(만원)", format="localized")
DATE = st.column_config.DateColumn(format="YYYY.MM.DD")
YEAR = st.column_config.NumberColumn(format="%d")


def eok(label):
    return st.column_config.NumberColumn(label, format="%.2f억")


def name_col(t):
    return t["단지명"] + " " + t["면적타입"].astype(str) + "㎡"


base = detail_base()

# ═════════════════════════ 4. TOP·상승하락 ═════════════════════════
with tabs[3]:
    basis = st.radio("상승·하락 비교 시점", ["1년 전", "6개월 전"], horizontal=True)
    colc = "1년전대비" if basis == "1년 전" else "6개월전대비"
    refc = "1년전" if basis == "1년 전" else "6개월전"
    if level == "전국":
        pc = load_table("nat_risers.parquet")
        tt = load_table("nat_top_trades.parquet").head(10)
    elif base is not None:
        pc = A.price_changes(base, REF)
        tt = A.top_trades(A.window(base, REF, 0, span=12))
    else:
        pc, tt = None, None
    if pc is not None:
        pc = pc[(pc["최근_건수"] >= 2) & (pc[f"{refc}_건수"] >= 2)].dropna(subset=[colc]).copy()
        pc["단지"] = name_col(pc)
        pc["지역"] = pc["시도코드"].map(SIDO_SHORT) + " " + pc["시군구"] + " " + pc["법정동"]
        st.caption(f"최근 {A.window_label(REF, 0)} 중위가 vs {basis} 중위가 · 같은 단지·같은 전용면적, "
                   f"양쪽 시점 각 2건 이상 거래된 {len(pc):,}개 비교")
        cfg = {"단지": st.column_config.TextColumn(width="medium"), "최근": EOK, refc: EOK,
               colc: st.column_config.NumberColumn("변동률", format="%+.1f%%"), "건축년도": YEAR}
        cols = ["순위", "단지", "지역", colc, refc, "최근", "건축년도"]
        a, b = st.columns(2)
        for box, ttl, sub in [(a, "📈 상승률 TOP 20", pc[pc[colc] > 0].nlargest(20, colc)),
                              (b, "📉 하락률 TOP 20", pc[pc[colc] < 0].nsmallest(20, colc))]:
            box.markdown(f"**{ttl}**")
            if sub.empty:
                box.info("해당 단지 없음")
                continue
            sub = sub.assign(순위=range(1, len(sub) + 1))
            box.dataframe(sub[cols], hide_index=True, width="stretch", column_config=cfg,
                          height=min(740, 38 + 35 * len(sub)))
        st.markdown("**최근 12개월 최고가 거래 TOP 10** (단지당 1건)")
        tt = tt.copy()
        tt["지역"] = tt["시도코드"].map(SIDO_SHORT) + " " + tt["시군구"] + " " + tt["법정동"]
        tt.insert(0, "순위", range(1, len(tt) + 1))
        st.dataframe(tt[["순위", "단지명", "지역", "전용면적", "층", "거래금액_억", "평당가", "계약일자"]],
                     hide_index=True, width="stretch", column_config={
                         "거래금액_억": eok("거래가"), "평당가": MAN, "계약일자": DATE,
                         "전용면적": st.column_config.NumberColumn(format="%.1f㎡")})
        if base is not None:
            st.markdown("**평당가 상위 단지 TOP 10** (최근 12개월, 거래 3건 이상)")
            tc = A.top_complexes(A.window(base, REF, 0, span=12))
            tc.insert(0, "순위", range(1, len(tc) + 1))
            st.dataframe(tc[["순위", "단지명", "시군구", "법정동", "건축년도", "거래수", "중위평당가",
                             "중위거래가", "최고거래가"]], hide_index=True, width="stretch",
                         column_config={"중위평당가": MAN, "중위거래가": EOK, "최고거래가": EOK,
                                        "건축년도": YEAR})

# ═════════════════════════ 5. 신고가·전고점 ═════════════════════════
with tabs[4]:
    st.markdown(f"**최근 3개월({A.window_label(REF, 0)}) 신고가 거래** — "
                "같은 단지·면적의 수집기간 최고가 경신 (이전 거래 2건 이상)")
    if level == "전국":
        nh = load_table("nat_new_highs.parquet").copy()
    elif base is not None:
        b2 = A.mark_new_highs(base)
        nh = A.window(b2, REF, 0)
        nh = nh[nh["신고가"]].copy()
    else:
        nh = None
    if nh is not None and len(nh):
        nh["경신폭(%)"] = (nh["거래금액"] / nh["직전최고가"] - 1) * 100
        nh["단지"] = name_col(nh)
        nh["지역"] = nh["시도코드"].map(SIDO_SHORT) + " " + nh["시군구"] + " " + nh["법정동"]
        nh["직전최고가_억"], nh["거래가_억"] = nh["직전최고가"] / 1e4, nh["거래금액"] / 1e4
        cnt = nh.groupby("지역").size().sort_values(ascending=False).head(10)
        st.caption("신고가가 많은 곳: " + " · ".join(f"{g} {n}건" for g, n in cnt.items()))
        st.dataframe(nh.sort_values("계약일자", ascending=False)
                     [["계약일자", "단지", "지역", "층", "직전최고가_억", "거래가_억", "경신폭(%)"]].head(500),
                     hide_index=True, width="stretch", height=380, column_config={
                         "계약일자": DATE, "직전최고가_억": eok("직전 최고가"), "거래가_억": eok("거래가"),
                         "경신폭(%)": PCT, "단지": st.column_config.TextColumn(width="medium")})
    elif nh is not None:
        st.info("최근 3개월 신고가 거래가 없습니다.")
    if base is not None:
        st.markdown("**전고점 대비 현재 가격** — 수집기간 최고가 대비 최근 3개월 중위가")
        c1, c2 = st.columns([1, 3])
        lo_rec = c1.slider("전고점 대비 범위(%)", 50, 110, (50, 90))
        c2.caption("고점 대비가 낮다고 저평가라는 뜻은 아닙니다. 고점이 일시적 과열이었거나 "
                   "입지·노후도 요인일 수 있으므로 단지 상세에서 흐름을 함께 확인하세요.")
        pk = A.peak_recovery(base, REF)
        pk = pk[pk["고점대비(%)"].between(*lo_rec)].copy()
        pk["단지"] = name_col(pk)
        st.dataframe(pk.sort_values("고점대비(%)")
                     [["단지", "시군구", "법정동", "건축년도", "최고가", "최고가일", "최근중위", "최근건수",
                       "고점대비(%)"]], hide_index=True, width="stretch", height=420, column_config={
                         "최고가": EOK, "최근중위": eok("최근 중위"), "최고가일": DATE, "건축년도": YEAR,
                         "최근건수": st.column_config.NumberColumn("최근 건수"),
                         "고점대비(%)": st.column_config.ProgressColumn(format="%.0f%%", min_value=0,
                                                                     max_value=110),
                         "단지": st.column_config.TextColumn(width="medium")})
    elif level == "전국":
        st.caption("전고점 대비 목록은 시도를 선택하면 볼 수 있습니다.")

# ═════════════════════════ 6. 예산으로 찾기 ═════════════════════════
with tabs[5]:
    if base is None:
        st.info("왼쪽에서 시도를 선택하면 예산에 맞는 단지를 찾을 수 있습니다.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        budget = c1.slider("예산 (억원)", 0.5, 30.0, (3.0, 5.0), step=0.25)
        sp = c2.select_slider("시세 기준 기간", [3, 6, 12], value=6, format_func=lambda v: f"최근 {v}개월")
        min_year = c3.number_input("준공 연도 이후", 1970, 2030, 2000, step=5)
        sort_by = c4.selectbox("정렬", ["1년 상승률 높은 순", "평당가 높은 순", "최근 거래 순",
                                       "거래 많은 순", "신축 순"])
        bs = A.budget_search(base, REF, budget[0], budget[1], months=sp)
        bs = bs[bs["건축년도"].astype(float).fillna(0) >= min_year]
        key_ = {"평당가 높은 순": "평당가", "1년 상승률 높은 순": "1년전대비", "최근 거래 순": "최근거래일",
                "거래 많은 순": "거래수", "신축 순": "건축년도"}[sort_by]
        bs = bs.sort_values(key_, ascending=False, na_position="last")
        bs["단지"] = name_col(bs)
        st.caption(f"{budget[0]:.2f}~{budget[1]:.2f}억, {min_year}년 이후 준공 · 후보 {len(bs):,}개(단지·면적)")
        if not bs.empty and level == "시도":
            by = bs.groupby("시군구").size().sort_values(ascending=False)
            fig = px.bar(x=by.index, y=by.values, labels={"x": "", "y": "후보 수"})
            fig.update_layout(height=200, **CHART)
            st.plotly_chart(fig, width="stretch")
        st.dataframe(bs[["단지", "시군구", "법정동", "건축년도", "중위거래가", "최근거래가", "최근거래일",
                         "거래수", "평당가", "1년전대비"]], hide_index=True, width="stretch", height=480,
                     column_config={"중위거래가": EOK, "최근거래가": EOK, "최근거래일": DATE, "평당가": MAN,
                                    "1년전대비": st.column_config.NumberColumn("1년 변동", format="%+.1f%%"),
                                    "건축년도": YEAR, "단지": st.column_config.TextColumn(width="medium")})
        st.caption("1년 변동 = 같은 길이 기간의 1년 전 중위가 대비. 취득세·중개보수 등 부대비용 제외. "
                   "실거래가는 계약 기준이며 호가·급매 여부는 반영되지 않습니다.")

# ═════════════════════════ 7. 단지 상세 ═════════════════════════
with tabs[6]:
    if base is None:
        st.info("왼쪽에서 시도(또는 시군구)를 선택하면 단지를 검색할 수 있습니다.")
    else:
        cnt = base.groupby("단지키").agg(단지명=("단지명", "last"), 시군구=("시군구", "last"),
                                       법정동=("법정동", "last"), n=("단지키", "size"))
        cnt = cnt.sort_values("n", ascending=False)
        labels = dict(zip(cnt["단지명"] + " · " + cnt["시군구"] + " " + cnt["법정동"], cnt.index))
        pick = st.selectbox("단지 검색 (입력해서 찾기)", list(labels), key=f"cx_{code}")
        cx = base[base["단지키"] == labels[pick]]
        pcx = A.price_changes(cx, REF)
        pkx = A.peak_recovery(cx, REF)
        info = cx.iloc[-1]
        m = st.columns(4)
        m[0].metric("준공", f"{int(info['건축년도'])}년" if pd.notna(info["건축년도"]) else "-")
        m[1].metric("거래 (전체 기간)", f"{len(cx)}건")
        m[2].metric("1년 변동 (면적별 중위)",
                    B.p(pcx["1년전대비"].median()) if pcx["1년전대비"].notna().any() else "-")
        m[3].metric("전고점 대비", f"{pkx['고점대비(%)'].median():.0f}%" if len(pkx) else "-")
        c1, c2 = st.columns([1.4, 1])
        with c1:
            fig = px.scatter(cx, x="계약일자", y=VAL, color="평형대", category_orders={"평형대": AREA_LABELS},
                             hover_data={"전용면적": ":.1f", "층": True, "거래금액_억": ":.2f"},
                             labels={VAL: UNIT, "계약일자": ""})
            g = cx.groupby("계약월")[VAL].median().rolling(3, min_periods=1).mean()
            fig.add_trace(go.Scatter(x=g.index, y=g.values, mode="lines", name="월 중위",
                                     line=dict(color="gray", dash="dot")))
            fig.update_layout(height=340, **CHART)
            st.plotly_chart(fig, width="stretch")
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
        st.dataframe(cx.sort_values("계약일자", ascending=False)
                     [["계약일자", "전용면적", "층", "거래금액_억", "평당가", "거래유형"]].head(30),
                     hide_index=True, width="stretch", column_config={
                         "계약일자": DATE, "거래금액_억": eok("거래가"), "평당가": MAN,
                         "전용면적": st.column_config.NumberColumn(format="%.2f㎡")})

st.divider()
st.caption("출처: 국토교통부 아파트 매매 실거래가(공공데이터포털) · 행정구역 경계: 통계청 SGIS "
           "(vuski/admdongkor, CC BY 4.0). 평당가 = 거래금액 ÷ 전용면적(평). "
           "투자 판단의 참고 자료이며 정확성을 보증하지 않습니다.")
