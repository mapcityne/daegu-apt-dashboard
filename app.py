"""k-apts: 전국 아파트 실거래가 시장 신호.

실행:  streamlit run app.py
메뉴:  홈 / 지역 시세 / 순위 / 예산 맞춤 / 안내 (상단 메뉴, 사이드바 없음)
"""
from pathlib import Path

import pandas as pd
import streamlit as st

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


# ═════════════════════════ 홈 ═════════════════════════
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
                 "오르는지 내리는지, 그리고 왜 그런지를 보여 드립니다.")

    reg = D.reg
    options = {"전국": "00"} | {SIDO_SHORT[c]: c for c in core.SIDO_ORDER} | dict(zip(reg["지역명"], reg["sggcd"]))
    c1, _ = st.columns([2, 3])
    pick = c1.selectbox("어느 지역이 궁금하세요?", list(options), index=None, placeholder="예: 대구 수성구",
                        key="home_search")
    if pick:
        core.set_region(options[pick])
        st.switch_page(PAGES["region"])

    V.figures(s)

    sg = D.stats[D.stats["단계"] == "시군구"].copy()
    sg["이름"] = sg["코드"].map(D.reg.set_index("sggcd")["지역명"])  # 표시 이름은 지역 마스터 기준
    up = sg[sg["판정"] == "상승"].sort_values("3개월", ascending=False).head(12)
    down = sg[sg["판정"] == "하락"].sort_values("3개월").head(12)
    a, b = st.columns(2, gap="large")
    with a:
        UI.block("지금 오르는 곳", f"최근 3개월 가격지수가 1% 넘게 오른 시군구 {int((sg['판정'] == '상승').sum())}곳 중 "
                 "상승 폭이 큰 순서입니다.")
        UI.plates([(r["이름"], "상승", B.p(r["1년"]) if pd.notna(r["1년"]) else "", f"/region?r={r['코드']}")
                   for _, r in up.iterrows()])
    with b:
        UI.block("지금 내리는 곳", f"최근 3개월 가격지수가 1% 넘게 내린 시군구 {int((sg['판정'] == '하락').sum())}곳 중 "
                 "하락 폭이 큰 순서입니다.")
        if len(down):
            UI.plates([(r["이름"], "하락", B.p(r["1년"]) if pd.notna(r["1년"]) else "", f"/region?r={r['코드']}")
                       for _, r in down.iterrows()])
        else:
            st.write("지금 뚜렷하게 내리는 시군구는 없습니다.")

    nat = Ctx(D, "00", "all")
    UI.block("시도별 한눈에", "색은 최근 3개월 판정, 숫자는 1년 변동입니다. 누르면 그 지역 시세로 갑니다.")
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
    st.markdown("<div class='k-home' style='padding-bottom:.4rem'><div class='k-head'>순위</div>"
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
    "home": st.Page(home, title="홈", url_path="home", default=True),
    "region": st.Page(region_page, title="지역 시세", url_path="region"),
    "ranking": st.Page(ranking_page, title="순위", url_path="ranking"),
    "budget": st.Page(budget_page, title="예산 맞춤", url_path="budget"),
    "guide": st.Page(guide_page, title="안내", url_path="guide"),
}
if LOGO.exists():
    st.logo(str(LOGO), size="large", link="https://k-apts.streamlit.app")
st.navigation(list(PAGES.values()), position="top").run()
