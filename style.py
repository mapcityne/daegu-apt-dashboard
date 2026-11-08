"""화면 디자인: 시세 리서치 리포트.

감정평가서·리서치센터 시세 보고서의 시각 언어.
- 서체 하나(IBM Plex Sans KR), 숫자는 고정폭 숫자(tabular)로 줄 맞춤.
- 남색 잉크 + 흰 지면 + 옅은 청회색 바탕. 상승=빨강, 하락=파랑(국내 시세 관례).
- 눈에 띄는 요소는 하나: 지역 머리의 '가격 신호계'(하락~보합~상승 눈금 위 현재 위치).
"""
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

PAGE = "#EEF1F4"      # 바탕
PAPER = "#FFFFFF"     # 지면
INK = "#14202E"
MUTED = "#5B6675"
RULE = "#D5DBE2"
NAVY = "#1B3A5B"
RISE = "#C8372D"
FALL = "#2160B8"
FLAT = "#8C96A3"
HOLD = "#B8C0CA"

# 예전 이름 호환
WALL, CONCRETE, GREEN, BRICK, GLASS = PAGE, "#E3E8EE", NAVY, RISE, FALL

VERDICT = {"상승": RISE, "하락": FALL, "보합": FLAT, "판단 보류": HOLD}
DIVERGING = [FALL, "#A9C1E3", "#F4F5F7", "#EBB2AC", RISE]
SEQUENTIAL = ["#EEF2F7", "#C3D1E2", "#7F9CBE", "#3D6391", NAVY]

FONT = "'IBM Plex Sans KR', 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap');

html, body, .stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea,
.stApp button, .stApp div, .stApp td, .stApp th, .stApp h1, .stApp h2, .stApp h3, .stApp h4 {{
  font-family: {FONT};
}}
[data-testid="stIconMaterial"], .material-symbols-rounded, [class*="material-symbols"] {{
  font-family: 'Material Symbols Rounded' !important;
}}
.stApp {{ background: {PAGE}; color: {INK}; font-variant-numeric: tabular-nums; }}
[data-testid="stDecoration"], #MainMenu, footer, [data-testid="stAppDeployButton"] {{ display: none; }}
.block-container {{ padding-top: 4.4rem; max-width: 1200px; }}

h1, h2, h3, h4 {{ font-weight: 700; letter-spacing: -0.02em; color: {INK}; }}
h3 {{ font-size: 1.3rem; }}
.stMarkdown p, .stMarkdown li {{ line-height: 1.72; max-width: 44em; font-size: 1rem; }}
.stMarkdown b, .stMarkdown strong {{ font-weight: 600; color: {INK}; }}

/* 넓은 화면은 상단 메뉴만. 휴대폰에선 메뉴가 사이드 메뉴로 접히므로 살려 둔다 */
@media (min-width: 641px) {{
  [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="stExpandSidebarButton"] {{ display: none !important; }}
}}
[data-testid="stSidebar"] {{ background: {PAPER}; border-right: 1px solid {RULE}; }}

/* 상단 메뉴: 흰 띠 + 남색 밑줄 */
[data-testid="stHeader"] {{ background: {PAPER}; border-bottom: 1px solid {RULE}; }}
[data-testid="stTopNavLink"] span, [data-testid="stTopNavLink"] p {{ font-size: .95rem; color: {MUTED}; font-weight: 500; }}
[data-testid="stTopNavLink"][aria-current="page"] span, [data-testid="stTopNavLink"][aria-current="page"] p {{ color: {NAVY}; font-weight: 700; }}
[data-testid="stTopNavLink"][aria-current="page"] {{ box-shadow: inset 0 -2px 0 {NAVY}; border-radius: 0; background: transparent; }}

/* 홈 첫 화면 */
.k-home {{ padding: 2rem 0 1rem; }}
.k-home .k-head {{ font-size: clamp(2rem, 4.6vw, 3.3rem); font-weight: 700; line-height: 1.18;
  letter-spacing: -0.035em; color: {INK}; }}
.k-home .k-lead {{ text-wrap: pretty; font-size: 1.05rem; color: {MUTED}; margin-top: .9rem; max-width: 38em; line-height: 1.7; }}

/* 구획 제목 */
.k-block {{ margin-top: 2.6rem; padding-top: 1rem; border-top: 1px solid {RULE}; }}
.stApp .k-block-title {{ font-size: 1.3rem; font-weight: 700; letter-spacing: -0.02em; color: {INK}; }}
.k-block-sub {{ color: {MUTED}; font-size: .92rem; margin: .2rem 0 .8rem; }}

/* 바닥글 */
.k-footer {{ margin-top: 4rem; padding: 1.8rem 1.6rem 1.4rem; background: {NAVY}; color: #C9D4E2; font-size: .86rem; }}
.stApp .k-footer .k-brand {{ font-size: 1.15rem; font-weight: 700; color: #fff; margin-bottom: .5rem; }}
.k-footer p {{ max-width: 56em; line-height: 1.7; margin: .2rem 0; color: #C9D4E2; }}

/* 지역 머리: 보고서 표지 */
.k-hero {{ background: {PAPER}; border: 1px solid {RULE}; border-top: 3px solid {NAVY};
  padding: 1.4rem 1.6rem 1.2rem; margin: .4rem 0 0; }}
.k-hero .k-top {{ display: flex; justify-content: space-between; align-items: flex-end; gap: 1rem; flex-wrap: wrap; }}
.k-hero .k-parent {{ font-size: .95rem; color: {MUTED}; font-weight: 500; }}
.k-hero .k-name {{ font-size: clamp(2rem, 5vw, 3rem); font-weight: 700; line-height: 1.15; letter-spacing: -0.035em; }}
.k-badge {{ display: inline-flex; align-items: center; gap: .4rem; color: #fff; font-weight: 700;
  font-size: 1.15rem; padding: .35rem .9rem; border-radius: 2px; white-space: nowrap; }}
.k-why {{ font-size: 1rem; line-height: 1.6; margin-top: .9rem; }}
.k-foot {{ font-size: .82rem; color: {MUTED}; margin-top: .45rem; }}

/* 가격 신호계: 하락 | 보합 | 상승 눈금 위 현재 3개월 변동 */
.k-gauge {{ margin-top: 1.2rem; }}
.k-gauge .k-track {{ position: relative; height: 10px; display: grid; grid-template-columns: 3fr 1fr 3fr; gap: 2px; }}
.k-gauge .k-track span:nth-child(1) {{ background: linear-gradient(90deg, {FALL}, #A9C1E3); }}
.k-gauge .k-track span:nth-child(2) {{ background: #D7DCE2; }}
.k-gauge .k-track span:nth-child(3) {{ background: linear-gradient(90deg, #EBB2AC, {RISE}); }}
.k-gauge .k-mark {{ position: absolute; top: -7px; width: 3px; height: 24px; background: {INK}; transform: translateX(-1px); }}
.k-gauge .k-mark b {{ position: absolute; top: -1.45rem; left: 50%; transform: translateX(-50%);
  font-size: .85rem; font-weight: 700; white-space: nowrap; color: {INK}; }}
.k-gauge .k-ticks {{ display: grid; grid-template-columns: 3fr 1fr 3fr; font-size: .76rem; color: {MUTED}; margin-top: .35rem; }}
.k-gauge .k-ticks span:nth-child(2) {{ text-align: center; }}
.k-gauge .k-ticks span:nth-child(3) {{ text-align: right; }}
.k-gauge.k-hasmark {{ padding-top: 1.3rem; }}

/* 핵심 숫자: 흰 지면 위 한 줄 표 */
.k-figs {{ display: grid; grid-template-columns: repeat(6, 1fr); background: {PAPER};
  border: 1px solid {RULE}; border-top: 0; margin: 0 0 1.4rem; }}
.k-fig {{ padding: .85rem 1rem .8rem; border-left: 1px solid {RULE}; }}
.k-fig:first-child {{ border-left: 0; }}
.k-fig .k-lab {{ font-size: .82rem; color: {MUTED}; font-weight: 500; }}
.k-fig .k-num {{ font-size: 1.6rem; font-weight: 700; line-height: 1.3; letter-spacing: -0.02em; }}
.k-fig .k-sub {{ font-size: .74rem; color: {MUTED}; line-height: 1.35; }}
.k-up {{ color: {RISE}; }} .k-down {{ color: {FALL}; }}
.k-figs.k-solo {{ border-top: 1px solid {RULE}; }}
@media (max-width: 900px) {{ .k-figs {{ grid-template-columns: repeat(3, 1fr); }}
  .k-fig:nth-child(4) {{ border-left: 0; }} .k-fig:nth-child(n+4) {{ border-top: 1px solid {RULE}; }} }}
@media (max-width: 520px) {{ .k-figs {{ grid-template-columns: repeat(2, 1fr); }}
  .k-fig {{ border-left: 0 !important; border-top: 1px solid {RULE}; }} .k-fig:nth-child(-n+2) {{ border-top: 0; }}
  .k-fig:nth-child(even) {{ border-left: 1px solid {RULE} !important; }} .k-fig .k-num {{ font-size: 1.35rem; }} }}

/* 지역 목록: 시세표 칸 */
.k-plates {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(10.5rem, 1fr)); gap: 6px; margin: .4rem 0 1.4rem; }}
.k-plate {{ display: flex; align-items: stretch; background: {PAPER}; border: 1px solid {RULE};
  color: {INK} !important; text-decoration: none !important; }}
a.k-plate:hover {{ border-color: {NAVY}; }}
.k-plate .k-bar {{ width: 4px; flex: none; }}
.k-plate .k-txt {{ padding: .45rem .7rem .5rem; line-height: 1.3; min-width: 0; flex: 1;
  display: flex; justify-content: space-between; align-items: baseline; gap: .5rem; }}
.k-plate .k-nm {{ font-weight: 600; font-size: .95rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.k-plate .k-val {{ font-size: .85rem; font-weight: 600; white-space: nowrap; }}
.k-legend {{ font-size: .85rem; color: {MUTED}; margin-bottom: .2rem; }}

/* 브리핑 소제목 */
.stApp .k-sec {{ font-size: 1.05rem; font-weight: 700; color: {NAVY}; margin: 1.4rem 0 .3rem;
  padding-left: .6rem; border-left: 3px solid {NAVY}; line-height: 1.3; }}

/* 지표·탭·표·입력 */
[data-testid="stMetric"] {{ background: {PAPER}; border: 1px solid {RULE}; padding: .6rem .8rem; }}
[data-testid="stMetricLabel"] p {{ font-size: .82rem; color: {MUTED}; }}
[data-testid="stMetricValue"] {{ font-size: 1.6rem; font-weight: 700; color: {INK}; }}
.stTabs [data-baseweb="tab-list"] {{ gap: 1.6rem; border-bottom: 1px solid {RULE}; }}
.stTabs [data-baseweb="tab"] {{ padding: .5rem 0; }}
.stTabs [data-baseweb="tab"] p {{ font-size: .98rem; color: {MUTED}; font-weight: 500; }}
.stTabs [aria-selected="true"] p {{ color: {NAVY}; font-weight: 700; }}
.stTabs [data-baseweb="tab-highlight"] {{ background: {NAVY}; height: 2px; }}
[data-testid="stDataFrame"] {{ border: 1px solid {RULE}; background: {PAPER}; }}
[data-testid="stPlotlyChart"] {{ background: {PAPER}; border: 1px solid {RULE}; padding: .4rem; }}
.stMultiSelect [data-baseweb="tag"] {{ background: {NAVY}; }}
[data-baseweb="select"] > div, .stTextInput input, .stNumberInput input {{ background: {PAPER}; border-radius: 2px; }}
*:focus-visible {{ outline: 2px solid {NAVY} !important; outline-offset: 2px; }}
@media (max-width: 640px) {{ .k-hero {{ padding: 1.1rem 1rem 1rem; }} .k-footer {{ padding: 1.4rem 1rem; }} }}
@media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; animation: none !important; }} }}
</style>
"""


def apply():
    st.markdown(CSS, unsafe_allow_html=True)
    t = go.layout.Template()
    t.layout = go.Layout(
        font=dict(family="IBM Plex Sans KR, sans-serif", color=INK, size=13),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        colorway=[NAVY, RISE, FALL, "#C08A1E", "#5E7F3A", "#7A5C8E", "#3E8C8C", FLAT, "#2F2F2F"],
        xaxis=dict(gridcolor="#E3E8EE", linecolor=RULE, zeroline=False),
        yaxis=dict(gridcolor="#E3E8EE", linecolor=RULE, zeroline=False),
        hoverlabel=dict(font=dict(family="IBM Plex Sans KR, sans-serif")),
    )
    pio.templates["kapt"] = t
    pio.templates.default = "plotly_white+kapt"


ARROW = {"상승": "▲", "하락": "▼", "보합": "■", "판단 보류": "·"}


def _sign_cls(s: str) -> str:
    s = str(s).strip()
    if s.startswith("+"):
        return "k-up"
    if s.startswith(("-", "−")):
        return "k-down"
    return ""


def gauge(value, span: float = 4.0) -> str:
    """하락(−1% 이하) | 보합 | 상승(+1% 이상) 눈금 위에 3개월 변동 위치를 표시한다.
    눈금: 왼쪽 끝 −span%, 보합 칸 −1~+1%, 오른쪽 끝 +span%."""
    mark = ""
    if value is not None and value == value:
        v = max(min(float(value), span), -span)
        # 3fr | 1fr | 3fr 구간에 맞춰 위치 환산
        if v <= -1:
            pos = (v + span) / (span - 1) * 3 / 7
        elif v >= 1:
            pos = 4 / 7 + (v - 1) / (span - 1) * 3 / 7
        else:
            pos = 3 / 7 + (v + 1) / 2 / 7
        mark = (f"<div class='k-mark' style='left:{pos * 100:.2f}%'><b>최근 3개월 "
                f"{value:+.1f}%</b></div>")
    return (f"<div class='k-gauge{' k-hasmark' if mark else ''}'><div class='k-track'><span></span><span></span>"
            f"<span></span>{mark}</div><div class='k-ticks'><span>하락 (−{span:.0f}% 이하)</span>"
            f"<span>보합</span><span>상승 (+{span:.0f}% 이상)</span></div></div>")


def hero(name: str, parent: str | None, verdict: str, why: str, foot: str, value=None):
    color = VERDICT.get(verdict, FLAT)
    parent_html = f"<div class='k-parent'>{parent}</div>" if parent else ""
    st.markdown(
        f"<div class='k-hero'><div class='k-top'><div>{parent_html}<div class='k-name'>{name}</div></div>"
        f"<span class='k-badge' style='background:{color}'>{ARROW.get(verdict, '')} {verdict}</span></div>"
        f"{gauge(value) if verdict != '판단 보류' else ''}"
        f"<div class='k-why'>{why}</div><div class='k-foot'>{foot}</div></div>", unsafe_allow_html=True)


def plates(items, legend: str = ""):
    """items: [(이름, 판정, 1년 변동 문자열, 링크 또는 None)]"""
    def one(n, v, c, href=None):
        inner = (f"<div class='k-bar' style='background:{VERDICT.get(v, FLAT)}'></div>"
                 f"<div class='k-txt' title='{n}: {v}{(', 1년 ' + c) if c else ''}'><span class='k-nm'>{n}</span>"
                 f"<span class='k-val {_sign_cls(c)}'>{c or v}</span></div>")
        return (f"<a class='k-plate' href='{href}' target='_self'>{inner}</a>" if href
                else f"<div class='k-plate'>{inner}</div>")
    html = "".join(one(*it) for it in items)
    leg = f"<div class='k-legend'>{legend}</div>" if legend else ""
    st.markdown(f"{leg}<div class='k-plates'>{html}</div>", unsafe_allow_html=True)


def figures(items, solo: bool = False):
    """items: [(라벨, 값, 보조 설명)] → 한 줄 숫자표(좁은 화면 3·2열). solo=True면 윗선을 그린다."""
    html = "".join(f"<div class='k-fig'><div class='k-lab'>{a}</div><div class='k-num {_sign_cls(b)}'>{b}</div>"
                   f"<div class='k-sub'>{c}</div></div>" for a, b, c in items)
    st.markdown(f"<div class='k-figs{' k-solo' if solo else ''}'>{html}</div>", unsafe_allow_html=True)


def section(title: str):
    st.markdown(f"<div class='k-sec'>{title}</div>", unsafe_allow_html=True)


def home_hero(headline: str, lead: str, value=None):
    g = f"<div style='max-width:44rem'>{gauge(value)}</div>" if value is not None else ""
    st.markdown(f"<div class='k-home'><div class='k-head'>{headline}</div><div class='k-lead'>{lead}</div>{g}</div>",
                unsafe_allow_html=True)


def block(title: str, sub: str = ""):
    sub_html = f"<div class='k-block-sub'>{sub}</div>" if sub else ""
    st.markdown(f"<div class='k-block'><div class='k-block-title'>{title}</div>{sub_html}</div>",
                unsafe_allow_html=True)
