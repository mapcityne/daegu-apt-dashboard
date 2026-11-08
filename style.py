"""화면 디자인: 아파트 외벽 슈퍼그래픽.

한국 아파트 외벽의 큰 동 번호·단지 표지판에서 가져온 시각 언어.
- 주인공은 하나: 지역 이름을 외벽 동 번호처럼 크게, 그 아래 판정을 외벽 색 띠로 칠한다.
- 나머지(지표·표·차트)는 조용하게. 카드·그림자·그라데이션 장식 없음.
"""
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

# 색: 외벽·콘크리트·단지 표지 초록 / 상승=벽돌, 하락=유리창, 보합=콘크리트
WALL = "#F2F3EF"
CONCRETE = "#E3E6E0"
INK = "#1E2422"
MUTED = "#5F6660"
GREEN = "#2E5E4E"
BRICK = "#B8423A"
GLASS = "#2C5C9A"
FLAT = "#8C918A"
OCHRE = "#C9A227"

VERDICT = {"상승": BRICK, "하락": GLASS, "보합": FLAT, "판단 보류": "#B9BDB6"}
DIVERGING = [GLASS, "#C9D3DF", WALL, "#E5C9C4", BRICK]
SEQUENTIAL = ["#EEF0EA", "#C9D6CC", "#8FAE9B", "#4F7D68", GREEN]

DISPLAY = "'Black Han Sans', 'Gowun Dodum', sans-serif"
BODY = "'Gowun Dodum', 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Black+Han+Sans&family=Gowun+Dodum&display=swap');

html, body, .stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea,
.stApp button, .stApp div, .stApp td, .stApp th {{
  font-family: {BODY};
}}
/* 아이콘 글꼴은 건드리지 않는다 */
[data-testid="stIconMaterial"], .material-symbols-rounded, [class*="material-symbols"] {{
  font-family: 'Material Symbols Rounded' !important;
}}
.stApp {{ background: {WALL}; color: {INK}; }}
[data-testid="stDecoration"], #MainMenu, footer, [data-testid="stAppDeployButton"] {{ display: none; }}
[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 2.2rem; max-width: 1240px; }}

h1, h2, h3, h4 {{ font-family: {DISPLAY}; font-weight: 400; letter-spacing: -0.01em; color: {INK}; }}
h3 {{ font-size: 1.55rem; margin-top: .4rem; }}
.stMarkdown p, .stMarkdown li {{ line-height: 1.75; max-width: 46em; }}
/* 본문 글꼴엔 굵은 체가 없어 강조는 간판 서체로 */
.stMarkdown b, .stMarkdown strong {{ font-family: {DISPLAY}; font-weight: 400; color: {INK}; letter-spacing: .01em; }}

/* 사이드바: 콘크리트 벽 */
[data-testid="stSidebar"] {{ background: {CONCRETE}; border-right: 1px solid #C9CDC5; }}
[data-testid="stSidebar"] h2 {{ font-size: 1.35rem; margin-bottom: .2rem; }}
[data-testid="stSidebar"] .stCaption, [data-testid="stSidebar"] small {{ color: {MUTED}; }}

/* 지표: 층 표지처럼 굵은 숫자 + 윗선 */
[data-testid="stMetric"] {{ border-top: 2px solid {INK}; padding-top: .45rem; }}
[data-testid="stMetricLabel"] p {{ font-size: .88rem; color: {MUTED}; }}
[data-testid="stMetricValue"] {{ font-family: {DISPLAY}; font-size: 2.1rem; color: {INK}; }}

/* 탭: 단지 안내판 글씨 */
.stTabs [data-baseweb="tab-list"] {{ gap: 1.4rem; border-bottom: 1px solid #C9CDC5; }}
.stTabs [data-baseweb="tab"] {{ padding: .3rem 0; }}
.stTabs [data-baseweb="tab"] p {{ font-size: 1.02rem; color: {MUTED}; }}
.stTabs [aria-selected="true"] p {{ color: {INK}; font-weight: 700; }}
.stTabs [data-baseweb="tab-highlight"] {{ background: {GREEN}; height: 3px; }}

/* 히어로: 외벽 동 번호 + 색 띠 */
.k-hero {{ margin: .2rem 0 1.4rem; }}
.k-hero .k-parent {{ font-size: 1.05rem; color: {MUTED}; margin-bottom: -.4rem; }}
.k-hero .k-name {{
  font-family: {DISPLAY}; font-size: clamp(3.4rem, 9vw, 6.8rem); line-height: 1;
  letter-spacing: -0.03em; color: {INK};
}}
.k-band {{ display: flex; align-items: stretch; margin-top: .9rem; min-height: 4.2rem; }}
.k-band .k-verdict {{
  font-family: {DISPLAY}; color: #fff; font-size: 2.1rem; padding: .55rem 1.3rem;
  display: flex; align-items: center; gap: .6rem; white-space: nowrap;
}}
.k-band .k-why {{
  flex: 1; padding: .6rem 1.1rem; display: flex; align-items: center;
  background: {CONCRETE}; font-size: 1.02rem; line-height: 1.55;
}}
.k-foot {{ font-size: .86rem; color: {MUTED}; margin-top: .5rem; }}

/* 핵심 숫자: 층 표지처럼 윗선 + 굵은 숫자, 휴대폰에선 2열 */
.k-figs {{ display: grid; grid-template-columns: repeat(3, 1fr); column-gap: 1.6rem; row-gap: 1.1rem;
  margin: .2rem 0 1.4rem; }}
.k-fig {{ border-top: 2px solid {INK}; padding-top: .45rem; }}
.k-fig .k-lab {{ font-size: .88rem; color: {MUTED}; }}
.k-fig .k-num {{ font-family: {DISPLAY}; font-size: 2.05rem; line-height: 1.15; }}
.k-fig .k-sub {{ font-size: .8rem; color: {MUTED}; }}
@media (max-width: 640px) {{ .k-figs {{ grid-template-columns: repeat(2, 1fr); column-gap: 1rem; }}
  .k-fig .k-num {{ font-size: 1.7rem; }} }}

/* 하위 지역: 동 표지판 */
.k-plates {{ display: flex; flex-wrap: wrap; gap: .5rem; margin: .4rem 0 1.4rem; }}
.k-plate {{
  display: flex; align-items: stretch; background: #fff; border: 1.5px solid {INK};
  min-width: 7.2rem;
}}
.k-plate .k-bar {{ width: .55rem; }}
.k-plate .k-txt {{ padding: .3rem .6rem .35rem; line-height: 1.15; }}
.k-plate .k-nm {{ font-family: {DISPLAY}; font-size: 1.05rem; }}
.k-plate .k-val {{ font-size: .8rem; color: {MUTED}; }}
.k-legend {{ font-size: .85rem; color: {MUTED}; }}

/* 브리핑 소제목 */
.stApp .k-sec {{ font-family: {DISPLAY}; font-size: 1.2rem; margin: 1.3rem 0 .3rem; }}

/* 표·입력 */
[data-testid="stDataFrame"] {{ border: 1px solid #C9CDC5; }}
.stMultiSelect [data-baseweb="tag"] {{ background: {GREEN}; }}
*:focus-visible {{ outline: 2px solid {GREEN} !important; outline-offset: 2px; }}
@media (max-width: 640px) {{
  .k-band {{ flex-direction: column; }}
  .k-band .k-verdict {{ font-size: 1.7rem; }}
}}
@media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; animation: none !important; }} }}
</style>
"""


def apply():
    st.markdown(CSS, unsafe_allow_html=True)
    t = go.layout.Template()
    t.layout = go.Layout(
        font=dict(family="Gowun Dodum, sans-serif", color=INK, size=13),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        colorway=[GREEN, BRICK, GLASS, OCHRE, "#7A5C8E", "#4A8C8C", "#8C6A4A", FLAT, "#2F2F2F"],
        xaxis=dict(gridcolor="#DADDD6", linecolor="#B9BDB6", zeroline=False),
        yaxis=dict(gridcolor="#DADDD6", linecolor="#B9BDB6", zeroline=False),
        hoverlabel=dict(font=dict(family="Gowun Dodum, sans-serif")),
    )
    pio.templates["kapt"] = t
    pio.templates.default = "plotly_white+kapt"


ARROW = {"상승": "▲", "하락": "▼", "보합": "■", "판단 보류": "·"}


def hero(name: str, parent: str | None, verdict: str, why: str, foot: str):
    color = VERDICT.get(verdict, FLAT)
    parent_html = f"<div class='k-parent'>{parent}</div>" if parent else ""
    st.markdown(
        f"<div class='k-hero'>{parent_html}<div class='k-name'>{name}</div>"
        f"<div class='k-band'><div class='k-verdict' style='background:{color}'>"
        f"{ARROW.get(verdict, '')} {verdict}</div><div class='k-why'>{why}</div></div>"
        f"<div class='k-foot'>{foot}</div></div>", unsafe_allow_html=True)


def plates(items, legend: str):
    """items: [(이름, 판정, 1년 변동 문자열)]"""
    html = "".join(
        f"<div class='k-plate'><div class='k-bar' style='background:{VERDICT.get(v, FLAT)}'></div>"
        f"<div class='k-txt'><div class='k-nm'>{n}</div><div class='k-val'>{ARROW.get(v, '')} {v}"
        f"{(', 1년 ' + c) if c else ''}</div></div></div>" for n, v, c in items)
    st.markdown(f"<div class='k-legend'>{legend}</div><div class='k-plates'>{html}</div>",
                unsafe_allow_html=True)


def figures(items):
    """items: [(라벨, 값, 보조 설명)] → 3열(휴대폰 2열) 숫자판."""
    html = "".join(f"<div class='k-fig'><div class='k-lab'>{a}</div><div class='k-num'>{b}</div>"
                   f"<div class='k-sub'>{c}</div></div>" for a, b, c in items)
    st.markdown(f"<div class='k-figs'>{html}</div>", unsafe_allow_html=True)


def section(title: str):
    st.markdown(f"<div class='k-sec'>{title}</div>", unsafe_allow_html=True)
