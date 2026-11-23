"""k-apts: 전국 아파트 실거래가 시장 신호.

실행:  streamlit run app.py
메뉴:  오늘의 순위(첫 화면) / 많이 오른 아파트 / 시장 개요 / 지역 시세 / 변동 순위 / 예산 맞춤 / 안내
"""
from pathlib import Path

import pandas as pd
import streamlit as st

import analysis as A
import briefing as B
import core
import style as UI
import views as V
from core import Ctx, Data
from regions import SIDO_SHORT

st.set_page_config(page_title="k-apts 아파트 시장 신호", page_icon="🏢", layout="wide",
                   initial_sidebar_state="collapsed")
UI.apply()
LOGO = Path(__file__).with_name("assets") / "logo.png"


def data() -> Data | None:
    try:
        return Data()
    except Exception as e:  # noqa: BLE001
        st.markdown("## 데이터를 준비하고 있습니다")
        st.write("전국 실거래 수집·집계가 끝나면 이 화면에 자동으로 표시됩니다. 잠시 뒤 다시 열어 주세요.")
        st.caption(f"상세: {type(e).__name__}: {e}")
        return None


def page_head(title: str, lead: str):
    st.markdown(f"<div class='k-home' style='padding-bottom:.4rem'><div class='k-head'>{title}</div>"
                f"<div class='k-lead'>{lead}</div></div>", unsafe_allow_html=True)


# ═════════════════════════ 오늘의 순위 (첫 화면) ═════════════════════════
PERIODS = {"최근 1개월": 30, "최근 3개월": 91, "최근 1년": 365}


def today_page():
    D = data()
    if D is None:
        return
    page_head("오늘의 순위", "시군구마다 가장 비싸게 거래된 아파트 10곳입니다. 매일 새벽 새로 신고된 거래를 반영합니다.")
    core._init_region()
    if st.session_state["sel_sido"] == "00":  # 처음 들어오면 대구
        core.set_region("27")
    sido, sgg = core.region_picker("today", allow_nation=False)
    c1, c2, c3 = st.columns([2, 1, 1])
    per = c1.radio("기간", list(PERIODS), horizontal=True, key="today_per")
    one = c2.checkbox("단지당 1건만", value=True, key="today_one",
                      help="같은 단지에서 여러 건이 거래됐으면 가장 비싼 1건만 순위에 넣습니다.")
    excl = c3.checkbox("직거래 제외", value=True, key="today_excl",
                       help="가족 간 거래처럼 시세와 동떨어진 거래가 섞일 수 있어 기본으로 뺍니다.")
    try:
        t = D.trades(sido)
    except Exception as e:  # noqa: BLE001
        st.warning(f"이 지역 거래 파일을 받지 못했습니다({type(e).__name__}). 잠시 뒤 다시 열어 주세요.")
        return
    d = t[~t["해제"]]
    if excl:
        d = d[d["거래유형"] != "직거래"]
    last = d["계약일자"].max()
    start = last - pd.Timedelta(days=PERIODS[per] - 1)
    d = d[d["계약일자"] >= start]
    if sgg != "all":
        d = d[d["시군구코드"] == sgg]
    st.caption(f"계약일 {start:%Y.%m.%d}~{last:%Y.%m.%d}, 거래 {len(d):,}건. 실거래는 계약 후 30일 안에 신고되므로 "
               "최근 거래는 며칠 뒤에 더 들어올 수 있습니다.")
    if d.empty:
        st.info("이 기간에 거래가 없습니다. 기간을 늘려 보세요.")
        V.footer(D)
        return

    names = D.reg.set_index("sggcd")["sggnm"]
    tops = []
    for code, g in d.groupby("시군구코드"):
        g = g.sort_values(["거래금액", "계약일자"], ascending=[False, False])
        if one:
            g = g.drop_duplicates("단지키")
        tops.append((code, names.get(code, g["시군구"].iloc[0]), g.head(10), len(d[d["시군구코드"] == code])))
    tops.sort(key=lambda x: -x[2]["거래금액"].iloc[0])

    small = dict(width="small")
    cfg = {"순위": st.column_config.NumberColumn(**small),
           "단지명": st.column_config.TextColumn("단지", width="medium"),
           "법정동": st.column_config.TextColumn(**small),
           "전용면적": st.column_config.NumberColumn("전용", format="%.0f㎡", **small),
           "층": st.column_config.NumberColumn(format="%d층", **small),
           "거래금액_억": st.column_config.NumberColumn("거래가", format="%.2f억", **small),
           "계약일자": st.column_config.DateColumn("계약일", format="YY.MM.DD", **small)}
    cols = ["순위", "단지명", "거래금액_억", "전용면적", "계약일자", "층", "법정동"]

    if len(tops) > 1:
        UI.block("시군구별 1위", "가장 비싼 거래가 높은 순서입니다.")
        first = pd.DataFrame([{"시군구": nm, "단지명": top["단지명"].iloc[0], "거래금액_억": top["거래금액_억"].iloc[0],
                               "전용면적": top["전용면적"].iloc[0], "법정동": top["법정동"].iloc[0],
                               "계약일자": top["계약일자"].iloc[0], "거래수": n} for _, nm, top, n in tops])
        st.dataframe(first, hide_index=True, width="stretch", column_config=cfg | {
            "거래수": st.column_config.NumberColumn("기간 거래", format="%d건")},
            height=min(640, 38 + 35 * len(first)))
        UI.block("시군구별 10위까지")
    grid = st.columns(2 if len(tops) > 1 else 1, gap="large")
    for i, (code, nm, top, n) in enumerate(tops):
        with grid[i % len(grid)]:
            UI.section(nm)
            st.caption(f"기간 거래 {n:,}건")
            show = top.assign(순위=range(1, len(top) + 1))[cols]
            st.dataframe(show, hide_index=True, width="stretch", column_config=cfg,
                         height=38 + 35 * len(show))
    V.footer(D)


# ═════════════════════════ 많이 오른 아파트 ═════════════════════════
BANDS = ["30% 이상", "20~30%", "10~20%"]


def risers_page():
    D = data()
    if D is None:
        return
    page_head("많이 오른 아파트", "1년 전보다 10%, 20%, 30% 넘게 오른 아파트를 구간별로 모았습니다.")
    core._init_region()
    if st.session_state["sel_sido"] == "00":
        core.set_region("27")
    sido, sgg = core.region_picker("rise", allow_nation=False)
    ctx = Ctx(D, sido, sgg)
    areas, excl, _ = core.detail_filters("rise")
    base = core.base_trades(D, ctx, areas, excl)
    if base is None:
        return
    c1, c2 = st.columns([1, 3])
    min_n = c1.selectbox("시점별 최소 거래", [1, 2, 3], index=1, format_func=lambda n: f"{n}건 이상",
                         key="rise_min",
                         help="1건만으로 비교하면 층·향 차이로 크게 튀는 경우가 많아 기본 2건으로 둡니다.")
    c2.caption(f"같은 단지·같은 전용면적끼리 최근 3개월({A.window_label(D.ref, 0)}) 중위 거래가와 "
               f"1년 전 같은 3개월({A.window_label(D.ref, 12)}) 중위 거래가를 비교합니다. "
               "두 시점 모두 정한 건수 이상 거래된 곳만 셉니다. 한 단지의 면적이 다르면 따로 셉니다.")
    pc = A.price_changes(base, D.ref)
    pc = pc[(pc["최근_건수"] >= min_n) & (pc["1년전_건수"] >= min_n)].dropna(subset=["1년전대비"]).copy()
    if pc.empty:
        st.info("1년 전과 비교할 수 있는 거래가 없습니다. 최소 거래 건수를 낮춰 보세요.")
        V.footer(D)
        return
    pc["구간"] = pd.cut(pc["1년전대비"], [10, 20, 30, float("inf")], right=False,
                      labels=["10~20%", "20~30%", "30% 이상"])
    cnt = pc["구간"].value_counts()
    UI.figures([
        ("비교한 곳", f"{len(pc):,}곳", "단지·면적 기준"),
        ("오른 곳", f"{int((pc['1년전대비'] > 0).sum()):,}곳", f"{(pc['1년전대비'] > 0).mean() * 100:.0f}%"),
        ("10~20% 오름", f"{int(cnt.get('10~20%', 0)):,}곳", ""),
        ("20~30% 오름", f"{int(cnt.get('20~30%', 0)):,}곳", ""),
        ("30% 이상 오름", f"{int(cnt.get('30% 이상', 0)):,}곳", ""),
        ("내린 곳", f"{int((pc['1년전대비'] < 0).sum()):,}곳", f"{(pc['1년전대비'] < 0).mean() * 100:.0f}%"),
    ], solo=True)

    if ctx.level == "시도":
        UI.block("시군구별 분포", "구간별 곳 수입니다. 30% 이상 오른 곳이 많은 순서입니다.")
        ct = pd.crosstab(pc["시군구"], pc["구간"].astype(str)).reindex(columns=BANDS, fill_value=0)
        ct.columns = list(BANDS)
        ct = ct.reindex(pc["시군구"].unique(), fill_value=0)
        ct["비교한 곳"] = pc.groupby("시군구").size()
        ct["10% 이상 비율"] = ct[BANDS].sum(axis=1) / ct["비교한 곳"] * 100
        ct = ct.sort_values(BANDS, ascending=False).reset_index()
        st.dataframe(ct, hide_index=True, width="stretch", height=min(640, 38 + 35 * len(ct)), column_config={
            "10% 이상 비율": st.column_config.NumberColumn(format="%.0f%%")})

    UI.block("구간별 아파트")
    pc["단지"] = core.name_col(pc)
    pc["지역"] = pc["시군구"] + " " + pc["법정동"]
    cfg = {"단지": st.column_config.TextColumn(width="medium"), "1년전": core.eok("1년 전"),
           "최근": core.eok("최근"), "1년전대비": st.column_config.NumberColumn("변동률", format="%+.1f%%"),
           "건축년도": core.YEAR, "최근_건수": st.column_config.NumberColumn("최근 거래", format="%d건")}
    tabs = st.tabs([f"{b} ({int(cnt.get(b, 0)):,})" for b in BANDS])
    for tab, b in zip(tabs, BANDS):
        with tab:
            sub = pc[pc["구간"] == b].sort_values("1년전대비", ascending=False)
            if sub.empty:
                st.info("이 구간에 해당하는 아파트가 없습니다.")
                continue
            st.dataframe(sub[["단지", "지역", "1년전", "최근", "1년전대비", "건축년도", "최근_건수"]],
                         hide_index=True, width="stretch", column_config=cfg,
                         height=min(700, 38 + 35 * len(sub)))
    st.caption("1년 전·최근은 각각 3개월 중위 거래가(억원)입니다. 거래가 적은 단지는 층·향에 따라 변동률이 크게 "
               "나올 수 있습니다.")
    V.footer(D)


# ═════════════════════════ 시장 개요 ═════════════════════════
def home():
    D = data()
    if D is None:
        return
    s = D.row("00")
    j = s.get("판정", "판단 보류")
    word = {"상승": "오르는 중", "하락": "내리는 중", "보합": "숨 고르는 중"}.get(j, "판단 보류")
    UI.home_hero(f"전국 아파트값,<br>지금은 {word}",
                 f"{D.ref:%Y년 %m월} 기준, 같은 단지끼리 비교한 가격지수는 최근 3개월 {B.p(s.get('3개월'))}, "
                 f"1년 {B.p(s.get('1년'))}입니다. 전국 {D.meta['거래수']:,}건의 실거래로 지역마다 "
                 "오르는지 내리는지, 그리고 왜 그런지를 보여 드립니다.", value=s.get("3개월"))

    reg = D.reg
    options = {"전국": "00"} | {SIDO_SHORT[c]: c for c in core.SIDO_ORDER} | dict(zip(reg["지역명"], reg["sggcd"]))
    c1, _ = st.columns([2, 3])
    pick = c1.selectbox("어느 지역이 궁금하세요?", list(options), index=None, placeholder="예: 대구 수성구",
                        key="home_search")
    if pick:
        core.set_region(options[pick])
        st.switch_page(PAGES["region"])

    V.figures(s, solo=True)

    sg = D.stats[D.stats["단계"] == "시군구"].copy()
    sg["이름"] = sg["코드"].map(D.reg.set_index("sggcd")["지역명"])  # 표시 이름은 지역 마스터 기준
    up = sg[sg["판정"] == "상승"].sort_values("3개월", ascending=False).head(12)
    down = sg[sg["판정"] == "하락"].sort_values("3개월").head(12)
    a, b = st.columns(2, gap="large")
    with a:
        UI.block("지금 오르는 곳", f"최근 3개월 가격지수가 1% 넘게 오른 시군구 {int((sg['판정'] == '상승').sum())}곳 중 "
                 "상승 폭이 큰 순서입니다.")
        UI.plates([(r["이름"], "상승", B.p(r["3개월"]) if pd.notna(r["3개월"]) else "", f"/region?r={r['코드']}")
                   for _, r in up.iterrows()])
    with b:
        UI.block("지금 내리는 곳", f"최근 3개월 가격지수가 1% 넘게 내린 시군구 {int((sg['판정'] == '하락').sum())}곳 중 "
                 "하락 폭이 큰 순서입니다.")
        if len(down):
            UI.plates([(r["이름"], "하락", B.p(r["3개월"]) if pd.notna(r["3개월"]) else "", f"/region?r={r['코드']}")
                       for _, r in down.iterrows()])
        else:
            st.write("지금 뚜렷하게 내리는 시군구는 없습니다.")

    nat = Ctx(D, "00", "all")
    UI.block("시도별 한눈에", "색은 판정, 숫자는 최근 3개월 가격지수 변동입니다. 누르면 그 지역 시세로 갑니다.")
    V.child_plates(nat, legend=False)
    UI.block("전국 지도", "시군구마다 같은 단지끼리 비교한 가격 흐름을 색으로 칠했습니다. 마우스를 올리면 숫자가 보입니다.")
    V.region_map(D, nat, key="home_map", height=640)
    UI.block("이번 달 시장 이야기", f"{D.ref:%Y년 %m월} 전국 실거래를 읽은 요약입니다.")
    V.briefing(D, nat, compact=True)
    V.footer(D)


# ═════════════════════════ 지역 시세 ═════════════════════════
def region_page():
    D = data()
    if D is None:
        return
    sido, sgg = core.region_picker("region")
    ctx = Ctx(D, sido, sgg)
    V.region_header(D, ctx)
    V.child_plates(ctx)
    t = st.tabs(["브리핑", "지도", "가격지수", "단지 찾기"])
    with t[0]:
        V.briefing(D, ctx)
    with t[1]:
        V.region_map(D, ctx, key="region_map")
    with t[2]:
        V.index_chart(D, ctx)
    with t[3]:
        if ctx.level == "전국":
            st.write("시도나 시군구를 고르면 단지 이름으로 찾아 거래 흐름을 볼 수 있습니다.")
        else:
            areas, excl, val = core.detail_filters("cx")
            base = core.base_trades(D, ctx, areas, excl)
            if base is not None and len(base):
                V.complex_detail(D, ctx, base, val)
    V.footer(D)


# ═════════════════════════ 순위 ═════════════════════════
def ranking_page():
    D = data()
    if D is None:
        return
    st.markdown("<div class='k-home' style='padding-bottom:.4rem'><div class='k-head'>변동 순위</div>"
                "<div class='k-lead'>많이 오른 단지, 많이 내린 단지, 최고가와 신고가를 지역별로 봅니다.</div></div>",
                unsafe_allow_html=True)
    sido, sgg = core.region_picker("rank")
    ctx = Ctx(D, sido, sgg)
    areas, excl, _ = core.detail_filters("rank")
    base = core.base_trades(D, ctx, areas, excl)
    V.rankings(D, ctx, base)
    UI.block("신고가")
    V.new_highs(D, ctx, base)
    V.footer(D)


# ═════════════════════════ 예산 맞춤 ═════════════════════════
def budget_page():
    D = data()
    if D is None:
        return
    st.markdown("<div class='k-home' style='padding-bottom:.4rem'><div class='k-head'>예산 맞춤</div>"
                "<div class='k-lead'>가진 돈으로 살 수 있는 단지를 최근 실거래가 기준으로 찾습니다.</div></div>",
                unsafe_allow_html=True)
    sido, sgg = core.region_picker("budget", allow_nation=False)
    ctx = Ctx(D, sido, sgg)
    areas, excl, _ = core.detail_filters("budget")
    base = core.base_trades(D, ctx, areas, excl)
    if base is not None:
        V.budget(D, ctx, base)
    V.footer(D)


# ═════════════════════════ 안내 ═════════════════════════
def guide_page():
    D = data()
    st.markdown("<div class='k-home' style='padding-bottom:.4rem'><div class='k-head'>안내</div>"
                "<div class='k-lead'>숫자를 어떻게 만들고 어떻게 읽으면 되는지 정리했습니다.</div></div>",
                unsafe_allow_html=True)
    UI.block("상승·보합·하락은 이렇게 정합니다")
    st.markdown(
        "- 같은 단지·같은 전용면적 거래끼리만 비교한 **동일단지 가격지수**를 씁니다. 비싼 단지가 많이 팔린 달에 "
        "평균이 오르는 착시를 걷어 내기 위해서입니다.\n"
        "- 최근 3개월 평균 지수가 직전 3개월보다 **1% 넘게 오르면 상승, 1% 넘게 내리면 하락**, 그 사이면 보합입니다.\n"
        "- 거래가 적은 곳은 판단을 보류합니다.")
    UI.block("숫자 읽는 법")
    st.markdown(
        "- **오른 단지**: 1년 전과 비교할 수 있는 단지 중 가격이 오른 비율입니다. 50%보다 높을수록 상승이 넓게 퍼져 있습니다.\n"
        "- **거래량**: 신고 기한(계약 후 30일) 때문에 마지막 달이 덜 잡혀, 기준월 직전 3개월을 전년 같은 기간과 비교합니다.\n"
        "- **신고가**: 같은 단지·면적에서 수집기간 최고가를 넘어선 거래입니다.\n"
        "- **구성 효과**: 중위 평당가와 동일단지 지수가 크게 다르면, 어떤 단지가 많이 팔렸는지가 평균을 움직인 것입니다.")
    UI.block("이 자료로 알 수 없는 것")
    st.markdown("- 호가·매물 수, 전세가(갭), 동·층·향별 선호, 급매 여부\n"
                "- 수집기간(최근 3년) 이전 고점 대비 위치. '최고가'는 이 기간 안의 최고가입니다.\n"
                "- 미래 가격. 이 자료는 과거 계약 기록입니다.")
    UI.block("데이터")
    st.markdown("- 실거래가: 국토교통부 아파트 매매 실거래가 상세 자료(공공데이터포털), 해제 거래 제외, 직거래 기본 제외\n"
                "- 미분양(시군구)·아파트 준공(시도): 국토교통부, 인구 이동(시군구): 국가데이터처 (KOSIS)\n"
                "- 행정구역 경계: 통계청 SGIS, vuski/admdongkor 가공(CC BY 4.0). 2026년 7월 행정구역 개편 반영\n"
                "- 매일 새벽 3시 무렵 새 거래를 받아 다시 계산합니다.")
    if D is not None:
        V.footer(D)


PAGES = {
    "today": st.Page(today_page, title="오늘의 순위", url_path="today", default=True),
    "risers": st.Page(risers_page, title="많이 오른 아파트", url_path="risers"),
    "home": st.Page(home, title="시장 개요", url_path="home"),
    "region": st.Page(region_page, title="지역 시세", url_path="region"),
    "ranking": st.Page(ranking_page, title="변동 순위", url_path="ranking"),
    "budget": st.Page(budget_page, title="예산 맞춤", url_path="budget"),
    "guide": st.Page(guide_page, title="안내", url_path="guide"),
}
if LOGO.exists():
    st.logo(str(LOGO), size="large", link="https://k-apts.streamlit.app")
st.navigation(list(PAGES.values()), position="top").run()
