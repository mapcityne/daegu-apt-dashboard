"""데이터 불러오기, 지역 선택(페이지 간 유지), 공통 표 서식."""
import io
import json
import os
import re
import time
import urllib.request

import pandas as pd
import streamlit as st

import analysis as A
import briefing as B
from common import AREA_LABELS, OUT_DIR, enrich
from regions import GEOJSON, SIDO_SHORT, regions

# 1순위: data 브랜치(raw CDN), 2순위: GitHub Release
SOURCES = [os.environ.get("DATA_URL", "https://raw.githubusercontent.com/mapcityne/daegu-apt-dashboard/data"),
           "https://github.com/mapcityne/daegu-apt-dashboard/releases/download/data"]
CHART = dict(margin=dict(l=0, r=0, t=10, b=0), legend_title=None)


# ───────────────────────── 데이터 ─────────────────────────
def _read(name: str) -> bytes:
    local = OUT_DIR / name
    if local.exists():
        return local.read_bytes()
    last = None
    for base in SOURCES:
        for _ in range(2):
            try:
                req = urllib.request.Request(f"{base}/{name}", headers={"User-Agent": "k-apts-app"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    return r.read()
            except Exception as e:  # noqa: BLE001
                last = e
                if getattr(e, "code", None) == 404:
                    break
                time.sleep(1.5)
    raise last


@st.cache_data(ttl=600)
def load_meta() -> dict:
    """10분마다 확인. '생성' 시각이 바뀌면 아래 표들도 새로 받는다."""
    return json.loads(_read("meta.json"))


@st.cache_data(ttl=24 * 3600, show_spinner="데이터 불러오는 중…", max_entries=20)
def load_table(name: str, version: str = "") -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(_read(name)))


@st.cache_data(ttl=24 * 3600, show_spinner="지역 거래 불러오는 중…", max_entries=4)
def load_trades(sido: str, version: str = "") -> pd.DataFrame:
    df = pd.read_parquet(io.BytesIO(_read(f"trades_{sido}.parquet")))
    df = A.prepare(enrich(df))
    df["평형대"] = pd.Categorical(df["평형대"], categories=AREA_LABELS, ordered=True)
    return df


@st.cache_data
def load_geo() -> dict:
    return json.loads(GEOJSON.read_text(encoding="utf-8"))


class Data:
    """앱 전체가 함께 쓰는 집계 데이터."""
    def __init__(self):
        self.meta = load_meta()
        self.ver = self.meta.get("생성", "")
        self.stats = load_table("stats.parquet", self.ver)
        self.index = load_table("index.parquet", self.ver)
        self.ref = pd.Timestamp(self.meta["기준월"] + "-01")
        self.reg = regions()
        self.S = self.stats.set_index("코드")

    def row(self, code) -> dict:
        return B.row_dict(self.S.loc[code]) if code in self.S.index else {"판정": "판단 보류"}

    def table(self, name):
        return load_table(name, self.ver)

    def trades(self, sido):
        return load_trades(sido, self.ver)


# ───────────────────────── 지역 선택 (페이지를 옮겨도 유지) ─────────────────────────
SIDO_ORDER = list(SIDO_SHORT)


def _init_region():
    st.session_state.setdefault("sel_sido", "00")
    st.session_state.setdefault("sel_sgg", "all")
    r = st.query_params.get("r")  # 링크로 들어온 지역 (?r=27290)
    if r and st.session_state.get("_qp_used") != r:
        st.session_state["_qp_used"] = r
        if len(r) == 5:
            st.session_state["sel_sido"], st.session_state["sel_sgg"] = r[:2], r
        elif len(r) == 2:
            st.session_state["sel_sido"], st.session_state["sel_sgg"] = r, "all"


def set_region(code: str):
    if code == "00":
        st.session_state["sel_sido"], st.session_state["sel_sgg"] = "00", "all"
    elif len(code) == 2:
        st.session_state["sel_sido"], st.session_state["sel_sgg"] = code, "all"
    else:
        st.session_state["sel_sido"], st.session_state["sel_sgg"] = code[:2], code


def region_picker(page: str, allow_nation=True):
    """페이지 상단의 지역 선택 줄. (sido, sgg) 반환."""
    _init_region()
    reg = regions()
    sidos = (["00"] if allow_nation else []) + SIDO_ORDER
    cur = st.session_state["sel_sido"]
    if cur not in sidos:
        cur = sidos[0]

    def on_sido():
        st.session_state["sel_sido"] = st.session_state[f"{page}_sido"]
        st.session_state["sel_sgg"] = "all"

    def on_sgg():
        st.session_state["sel_sgg"] = st.session_state[f"{page}_sgg"]

    c1, c2, _ = st.columns([1, 1, 2])
    sido = c1.selectbox("시도", sidos, index=sidos.index(cur), key=f"{page}_sido", on_change=on_sido,
                        format_func=lambda c: "전국" if c == "00" else SIDO_SHORT[c])
    sgg = "all"
    if sido != "00":
        subs = reg[reg["sidocd"] == sido].sort_values("sggcd")
        opts = ["all"] + subs["sggcd"].tolist()
        names = dict(zip(subs["sggcd"], subs["sggnm"]))
        cur_sgg = st.session_state["sel_sgg"] if st.session_state["sel_sgg"] in opts else "all"
        sgg = c2.selectbox("시군구", opts, index=opts.index(cur_sgg), key=f"{page}_sgg", on_change=on_sgg,
                           format_func=lambda c: "전체" if c == "all" else names[c])
    st.session_state["sel_sido"], st.session_state["sel_sgg"] = sido, sgg
    return sido, sgg


class Ctx:
    """선택한 지역의 단계·통계·하위 지역."""
    def __init__(self, D: Data, sido: str, sgg: str):
        self.sido, self.sgg = sido, sgg
        self.level = "전국" if sido == "00" else ("시도" if sgg == "all" else "시군구")
        self.code = sido if sgg == "all" else sgg
        self.s = D.row(self.code)
        self.parent = D.row("00") if self.level == "시도" else D.row(sido) if self.level == "시군구" else None
        reg = D.reg.set_index("sggcd")
        if self.level == "시군구":
            self.name, self.parent_name = reg.at[sgg, "sggnm"], SIDO_SHORT[sido]
        else:
            self.name, self.parent_name = ("전국" if sido == "00" else SIDO_SHORT[sido]), None
        self.title = self.name if self.level != "시군구" else f"{self.parent_name} {self.name}"
        st_ = D.stats
        if self.level == "전국":
            self.children, self.child_word = st_[st_["단계"] == "시도"].copy(), "시도"
        elif self.level == "시도":
            c = st_[(st_["단계"] == "시군구") & (st_["상위"] == sido)].copy()
            c["이름"] = c["코드"].map(reg["sggnm"])
            self.children, self.child_word = c, "시군구"
        else:
            dong = D.table("dong.parquet")
            c = dong[dong["시군구코드"] == sgg].rename(columns={"법정동": "이름", "동일단지_1년": "1년"}).copy()
            self.children, self.child_word = c[c["거래수_3개월"].fillna(0) >= 3], "법정동"


def detail_filters(key: str):
    """단지 목록 조건 (평형대·직거래·가격 기준). 접어 둔 상자 안에."""
    with st.expander("단지 목록 조건"):
        c1, c2, c3 = st.columns([2, 1, 1])
        areas = c1.multiselect("평형대 (전용면적)", AREA_LABELS, default=AREA_LABELS, key=f"{key}_areas")
        excl = c2.checkbox("직거래 제외", value=True, key=f"{key}_excl",
                           help="가족 간 증여성 거래 등 시세와 동떨어진 거래가 섞일 수 있음")
        metric = c3.radio("가격 기준", ["평당가", "거래금액"], key=f"{key}_metric", horizontal=True)
    return areas, excl, ("평당가" if metric == "평당가" else "거래금액_억")


def base_trades(D: Data, ctx: Ctx, areas, excl):
    if ctx.level == "전국":
        return None
    try:
        t = D.trades(ctx.sido)
    except Exception as e:  # noqa: BLE001
        st.warning(f"이 지역 거래 파일을 받지 못했습니다({type(e).__name__}). 잠시 뒤 다시 열어 주세요.")
        return None
    d = t[t["평형대"].isin(areas) & ~t["해제"] & (t["계약월"] <= D.ref)]
    if excl:
        d = d[d["거래유형"] != "직거래"]
    if ctx.level == "시군구":
        d = d[d["시군구코드"] == ctx.sgg]
    return d


# ───────────────────────── 표 서식 ─────────────────────────
PCT = st.column_config.NumberColumn(format="%+.1f%%")
EOK = st.column_config.NumberColumn(format="%.2f억")
MAN = st.column_config.NumberColumn("평당가(만원)", format="localized")
DATE = st.column_config.DateColumn(format="YYYY.MM.DD")
YEAR = st.column_config.NumberColumn(format="%d")
BOLD = re.compile(r"\*\*(.+?)\*\*")


def eok(label):
    return st.column_config.NumberColumn(label, format="%.2f억")


def name_col(t):
    return t["단지명"] + " " + t["면적타입"].astype(str) + "㎡"


def md_list(lines):
    """마크다운 **굵게**가 한글 조사 앞에서 깨지는 문제를 피해 <b>로 렌더링."""
    items = [BOLD.sub(r"<b>\1</b>", x).replace("~", "\\~") for x in lines]
    st.markdown("\n".join(f"- {x}" for x in items), unsafe_allow_html=True)
