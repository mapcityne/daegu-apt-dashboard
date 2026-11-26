"""정적 사이트 생성 (광고 게재용, GitHub Pages).

data/out 집계 결과로 전국·시도·시군구 페이지를 HTML로 만든다. 매일 데이터 갱신 뒤 실행.
    python site_build.py [--out site] [--base-url https://k-apts.kr]

광고 설정은 ads.py 참고 (환경변수).
"""
import argparse
import html
import json
import os
import re
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

import ads
import analysis as A
import briefing as B
import site_stats as ST
from common import OUT_DIR, enrich
from regions import SIDO_SHORT, regions

BASE = Path(__file__).resolve().parent
MARK = ('<svg class="mark" viewBox="0 0 32 32" width="32" height="32" aria-hidden="true">'
        '<rect width="32" height="32" rx="9" fill="#3B6FD8"/><rect x="7" y="17" width="5" height="8" rx="1" fill="#fff"/>'
        '<rect x="13.5" y="12" width="5" height="13" rx="1" fill="#fff"/><rect x="20" y="7" width="5" height="18" rx="1" '
        'fill="#fff"/><rect x="20" y="7" width="5" height="3.5" rx="1" fill="#FF8A7A"/></svg>')
REPO_URL = "https://github.com/mapcityne/daegu-apt-dashboard"
VCOLOR = {"상승": "#C8372D", "하락": "#2160B8", "보합": "#8C96A3", "판단 보류": "#B8C0CA"}
ARROW = {"상승": "▲", "하락": "▼", "보합": "■", "판단 보류": "·"}
BOLD = re.compile(r"\*\*(.+?)\*\*")
e = html.escape


# ───────────────────────── 서식 ─────────────────────────
def pct(v, d=1, sign=True):
    return B.p(v, d, sign)


def sign_cls(s):
    s = str(s)
    return "up" if s.startswith("+") else "down" if s.startswith(("-", "−")) else ""


def eok(v):
    return f"{v:,.2f}억" if pd.notna(v) else "-"


def md(line: str) -> str:
    return BOLD.sub(r"<b>\1</b>", e(line))


def ul(lines) -> str:
    return "<ul>" + "".join(f"<li>{md(x)}</li>" for x in lines) + "</ul>" if lines else ""


def table(head, rows, cls="") -> str:
    th = "".join(f"<th>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tbl"><table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


SIG_NOTE = ("가격지수는 같은 단지·같은 면적끼리 비교한 변동률입니다. 오른 단지: 1년 전보다 오른 단지 비율 · "
            "거래량: 전년 같은 3개월 대비 · 신고가 비중: 최근 3개월 거래 중")


def _sig_cells(st) -> str:
    vals = [pct(st.get("3개월")), pct(st.get("6개월")), pct(st.get("1년")),
            pct(st.get("상승비율"), 0, False) if st.get("상승비율") is not None else "-",
            pct(st.get("거래량증감"), 0),
            pct(st.get("신고가비율"), 1, False) if st.get("신고가비율") is not None else "-"]
    j = st.get("판정", "판단 보류")
    return ("".join(f'<td class="{sign_cls(v)}">{v}</td>' for v in vals)
            + f'<td><span class="v-{VKEY.get(j, "hold")} vt">{e(j)}</span></td>')


def sigtable(rows, more=()) -> str:
    """시세표: 지역별 핵심 지표를 한 줄씩. more는 '더 보기'를 눌러야 보이는 줄."""
    def tr(label, st, cls=""):
        return f'<tr class="{cls}"><th scope="row">{label}</th>{_sig_cells(st)}</tr>'
    body = "".join(tr(lb, st) for lb, st in rows) + "".join(tr(lb, st, "sig-more") for lb, st in more)
    btn = ('<button type="button" class="sig-toggle" onclick="var t=this.parentNode;t.classList.toggle(\'open\');'
           'this.textContent=t.classList.contains(\'open\')?\'접기\':\'시도 전체 보기\'">시도 전체 보기</button>'
           if more else "")
    return ('<div class="sigwrap"><div class="tbl"><table class="sigtbl"><thead>'
            '<tr><th rowspan="2">지역</th><th colspan="3">가격지수 변동</th><th colspan="3">시장 분위기</th>'
            '<th rowspan="2">판정</th></tr>'
            '<tr><th>3개월</th><th>6개월</th><th>1년</th><th>오른 단지</th><th>거래량</th><th>신고가 비중</th></tr>'
            f'</thead><tbody>{body}</tbody></table></div>{btn}<p class="sig-note">{SIG_NOTE}</p></div>')


def badge(j):
    return f'<b class="badge v-{VKEY.get(j, "hold")}">{j}</b>'


def chip(j):
    return f'<span class="vpill v-{VKEY.get(j, "hold")}">{ARROW.get(j, "")} {e(str(j))}</span>'


VKEY = {"상승": "up", "하락": "down", "보합": "flat", "판단 보류": "hold"}


def region_card(href, name, s, extra=""):
    j = s.get("판정") if isinstance(s.get("판정"), str) else "판단 보류"
    c3, c1 = pct(s.get("3개월")), pct(s.get("1년"))
    return (f'<a class="rcard" href="{href}"><div class="rc-top"><b>{e(name)}</b>{chip(j)}</div>'
            f'<div class="rc-nums"><div><small>3개월</small><strong class="{sign_cls(c3)}">{c3}</strong></div>'
            f'<div><small>1년</small><strong class="{sign_cls(c1)}">{c1}</strong></div></div>{extra}</a>')


def sibling_section(short, code, sib):
    """같은 시도의 다른 시군구로 가는 내부 링크(최근 3개월 가격지수 변동과 함께)."""
    sib = sib[sib["코드"] != code]
    if sib.empty:
        return ""
    items = []
    for _, r in sib.iterrows():
        j = r["판정"] if isinstance(r["판정"], str) and r["판정"] in VCOLOR else "판단 보류"
        c3 = pct(r["3개월"]) if pd.notna(r["3개월"]) else "-"
        items.append(f'<a class="plate" href="{r["코드"]}.html"><i style="background:{VCOLOR[j]}"></i>'
                     f'<span>{e(r["nm"])}</span><b class="{sign_cls(c3)}">{c3}</b></a>')
    return (f"<h2>{e(short)} 다른 시군구 시세</h2><p class='sub'>최근 3개월 가격지수 변동입니다. 누르면 그 지역의 "
            "실거래가 순위와 브리핑으로 갑니다.</p><div class='plates sib'>" + "".join(items) + "</div>")


# ───────────────────────── 월별 통계·오늘 공개 ─────────────────────────
def chg_word(v, up="늘었", down="줄었"):
    if v is None or pd.isna(v):
        return None
    if abs(v) < 0.05:
        return "같은 수준이었"
    return f"{abs(v):.1f}% {up if v > 0 else down}"


def summary_month(d):
    ok = d[(d["상태"] != "집계 중") & d["거래량"].notna()]
    return ok.index[-1] if len(ok) else None


def won(v_eok):
    """억 단위 숫자를 기사체 금액으로: 5.69 → 5억6,900만원."""
    man = int(round(v_eok * 10000))
    hi, lo = divmod(man, 10000)
    return (f"{hi}억" if hi else "") + (f"{lo:,}만원" if lo else ("원" if hi else "0원"))


def news_lines(name, d, m, last, gen):
    """기사에 그대로 인용할 수 있는 문장(숫자는 모두 집계값)."""
    r = d.loc[m]
    prev = m - pd.DateOffset(months=1)
    a, b = chg_word(r["거래량_전월"]), chg_word(r["거래량_전년"])
    base = f"{m:%Y년 %-m월} {name} 아파트 매매 거래는 {int(r['거래량']):,}건으로"
    if a and b:
        line = f"{base} 전월({prev:%-m월})보다 {a}고, 1년 전 같은 달보다 {b}다."
    elif a:
        line = f"{base} 전월({prev:%-m월})보다 {a}다."
    elif b:
        line = f"{base} 1년 전 같은 달보다 {b}다."
    else:
        line = f"{base} 집계됐다."
    out = [line]
    if pd.notna(r["중위가"]):
        y = chg_word(r["중위가_전년"], "높았", "낮았")
        out.append(f"직거래를 뺀 거래의 중위 거래가는 {won(r['중위가'])}"
                   + (f"으로 1년 전 같은 달보다 {y}다." if y else "이었다."))
    if pd.notna(r["지수_전월"]):
        out.append(f"같은 단지·같은 면적끼리 비교한 가격지수는 전월 대비 {pct(r['지수_전월'])}"
                   + (f", 1년 전 대비 {pct(r['지수_전년'])}였다." if pd.notna(r["지수_전년"]) else "였다."))
    note = ""
    if r["상태"] == "잠정":
        dl = m + pd.offsets.MonthEnd(0) + pd.Timedelta(days=30)
        note = f", {m:%-m월}분은 신고기한({dl:%-m월 %-d일}) 전 잠정치"
    src = f"자료: 국토교통부 아파트 매매 실거래가({gen:%Y.%m.%d} 집계{note}), k-apts 계산"
    return out, src


def quote_card(m, lines, src, more=""):
    text = " ".join(lines) + f" ({src})"
    return (f'<div class="card quote"><div class="q-head"><b>{m:%Y년 %-m월} 요약 · 기사 인용용</b>'
            f'<button type="button" class="copy" data-copy="{e(text)}">문장 복사</button></div>'
            + "".join(f"<p>{e(x)}</p>" for x in lines) + f'<p class="src">{e(src)}</p>{more}</div>')


def josa(word, a, b):
    """받침 있으면 a, 없으면 b (으로/로는 ㄹ받침도 b)."""
    ch = str(word)[-1:]
    if not ("가" <= ch <= "힣"):
        return b
    jong = (ord(ch) - 0xAC00) % 28
    if jong == 0 or (a == "으로" and jong == 8):
        return b
    return a


def story_items(sg, gen, data_end):
    """기자가 관심 가질 만한 숫자를 자동으로 골라 기사체 문장으로 만든다.
    잡음을 줄이려고 최근 3개월 거래가 100건 이상인 시군구만 본다."""
    src = f"국토교통부 아파트 매매 실거래가({gen:%Y.%m.%d} 집계), k-apts 계산"
    big = sg[sg["거래수_3개월"] >= 100].copy()
    items = []

    def link(r):
        return f'r/{r["코드"]}.html'

    def top(col, asc=False):
        d = big.dropna(subset=[col]).sort_values(col, ascending=asc)
        return d.head(3) if len(d) else None

    t = top("3개월")
    if t is not None:
        r, rest = t.iloc[0], t.iloc[1:]
        tail = ", ".join(f"{x['nm']}({pct(x['3개월'])})" for _, x in rest.iterrows())
        items.append(("가장 많이 오른 곳", f"최근 3개월 아파트값이 가장 많이 오른 곳은 {r['nm']}{josa(r['nm'], '으로', '로')} 같은 단지 기준 "
                      f"{abs(r['3개월']):.1f}% 올랐다." + (f" 이어 {tail} 순이었다." if tail else ""), link(r)))
    t = top("3개월", True)
    if t is not None and t.iloc[0]["3개월"] < 0:
        r = t.iloc[0]
        items.append(("가장 많이 내린 곳", f"같은 기간 가장 많이 내린 곳은 {r['nm']}{josa(r['nm'], '으로', '로')} {abs(r['3개월']):.1f}% 떨어졌다.", link(r)))
    t = top("신고가비율")
    if t is not None:
        r = t.iloc[0]
        items.append(("신고가 비중", f"최근 3개월 거래 중 신고가 비중이 가장 높은 곳은 {r['nm']}{josa(r['nm'], '으로', '로')}, "
                      f"거래 {int(r['거래수_3개월']):,}건 중 {r['신고가비율']:.1f}%가 직전 최고가를 넘었다.", link(r)))
    t = top("거래량증감")
    if t is not None and t.iloc[0]["거래량증감"] > 0:
        r = t.iloc[0]
        items.append(("거래량 급증", f"거래량은 {r['nm']}{josa(r['nm'], '이', '가')} 가장 크게 늘었다. 최근 3개월 {int(r['거래수_3개월']):,}건으로 "
                      f"1년 전 같은 기간보다 {r['거래량증감']:.0f}% 많다.", link(r)))
    t = top("해제율")
    if t is not None:
        r = t.iloc[0]
        items.append(("계약 해제", f"계약 해제 비율이 가장 높은 곳은 {r['nm']}{josa(r['nm'], '으로', '로')} 최근 3개월 거래의 "
                      f"{r['해제율']:.1f}%가 해제 신고됐다.", link(r)))
    t = top("직거래율")
    if t is not None:
        r = t.iloc[0]
        items.append(("직거래 비중", f"중개업소를 거치지 않은 직거래 비중은 {r['nm']}{josa(r['nm'], '이', '가')} {r['직거래율']:.1f}%로 가장 높았다.",
                      link(r)))
    try:
        tt = pd.read_parquet(OUT_DIR / "nat_top_trades.parquet")
        tt = tt[tt["계약일자"] >= data_end - pd.Timedelta(days=30)].sort_values("거래금액_억", ascending=False)
        if len(tt):
            r = tt.iloc[0]
            items.append(("최근 한 달 최고가", f"최근 한 달 전국 최고가 거래는 {SIDO_SHORT.get(str(r['시도코드']), '')} "
                          f"{r['시군구']} {r['법정동']} {r['단지명']} 전용 {r['전용면적']:.0f}㎡{josa('㎡', '으로', '로')} "
                          f"{won(r['거래금액_억'])}에 팔렸다({r['계약일자']:%-m월 %-d일} 계약).", f"r/{r['시도코드']}.html"))
    except Exception:
        pass
    try:
        nh = pd.read_parquet(OUT_DIR / "nat_new_highs.parquet")
        nh = nh[nh["계약일자"] >= data_end - pd.Timedelta(days=30)]
        if len(nh):
            by = nh["시도코드"].astype(str).value_counts()
            lead = ", ".join(f"{SIDO_SHORT.get(c, c)} {n:,}건" for c, n in by.head(3).items())
            items.append(("신고가 거래 건수", f"최근 30일 전국에서 직전 최고가를 넘은 거래는 {len(nh):,}건이었다. "
                          f"지역별로는 {lead} 순으로 많았다.", "today.html"))
    except Exception:
        pass
    if not items:
        return ""
    li = "".join(
        f'<li><div class="st-head"><b>{e(h)}</b><button type="button" class="copy" '
        f'data-copy="{e(txt + " (" + src + ")")}">문장 복사</button></div>'
        f'<p>{e(txt)}</p><a class="st-more" href="{u}">자세히 →</a></li>' for h, txt, u in items)
    return (f'<h2 id="story">이번 주 기사 거리</h2><p class="sub">최근 3개월 거래 100건 이상인 시군구 기준으로 눈에 띄는 '
            f'숫자를 매일 자동으로 골랐습니다. 문장은 그대로 인용할 수 있습니다.</p>'
            f'<ol class="stories">{li}</ol><p class="sig-note">자료: {e(src)}</p>')


def sig_csv(rows) -> str:
    """지역별 시장 신호 표를 CSV로 (엑셀에서 바로 열리게 BOM 포함)."""
    import csv, io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["지역", "가격지수_3개월(%)", "가격지수_6개월(%)", "가격지수_1년(%)", "1년전보다_오른_단지(%)",
                "거래량_전년동기대비(%)", "신고가_비중(%)", "거래수_3개월", "판정"])
    for name, st in rows:
        f = lambda k, d=2: "" if st.get(k) is None or st.get(k) != st.get(k) else round(float(st.get(k)), d)
        w.writerow([name, f("3개월"), f("6개월"), f("1년"), f("상승비율", 1), f("거래량증감", 1), f("신고가비율", 1),
                    (int(st["거래수_3개월"]) if st.get("거래수_3개월") == st.get("거래수_3개월") and st.get("거래수_3개월") is not None else ""), st.get("판정", "")])
    return "\ufeff" + buf.getvalue()


def vol_bar(v, vmax):
    if pd.isna(v) or not vmax:
        return "-"
    return (f'<span class="vbar"><i style="width:{max(v / vmax * 100, 2):.0f}%"></i></span>'
            f"<span>{int(v):,}</span>")


def monthly_section(name, d, last, gen, anchor="monthly"):
    m = summary_month(d)
    if m is None:
        return ""
    lines, src = news_lines(name, d, m, last, gen)
    vmax = d["거래량"].max()
    rows = []
    for mm, r in d.iloc[::-1].iterrows():
        tag = f' <small class="tag">{r["상태"]}</small>' if r["상태"] else ""
        rows.append((f"{mm:%y.%m}{tag}", vol_bar(r["거래량"], vmax),
                     f'<span class="{sign_cls(pct(r["거래량_전월"]))}">{pct(r["거래량_전월"], 0)}</span>',
                     f'<span class="{sign_cls(pct(r["거래량_전년"]))}">{pct(r["거래량_전년"], 0)}</span>',
                     eok(r["중위가"]), f'{r["평당가"]:,.0f}만' if pd.notna(r["평당가"]) else "-",
                     f'<span class="{sign_cls(pct(r["지수_전월"]))}">{pct(r["지수_전월"])}</span>',
                     f'<span class="{sign_cls(pct(r["지수_전년"]))}">{pct(r["지수_전년"])}</span>'))
    return (f'<h2 id="{anchor}">{e(name)} 월별 통계</h2><p class="sub">계약월 기준입니다. 거래량은 해제 거래를 뺀 '
            "전체 매매 신고, 가격은 직거래를 뺀 거래 기준입니다. 실거래는 계약 후 30일 안에 신고하므로 최근 두 달은 "
            "숫자가 더 늘어날 수 있습니다.</p>"
            + quote_card(m, lines, src) + table(["계약월", "거래량", "전월 대비", "전년 동월 대비", "중위 거래가", "중위 평당가",
                     "가격지수 전월 대비", "가격지수 1년"], rows, "monthly"))


def fresh_rows(df, by_sgg, counts=None):
    df = df[~df["해제"]].sort_values(["거래금액", "계약일자"], ascending=[False, False])
    if by_sgg:
        df = df.drop_duplicates("시군구코드")
    rows = []
    for i, r in enumerate(df.head(10 if not by_sgg else 999).itertuples()):
        first = f'<a href="{r.시군구코드}.html">{e(r.시군구)}</a>' if by_sgg == "link" else e(r.시군구)
        lead = [first] if by_sgg else [i + 1]
        tail = [f"{counts.get(r.시군구코드, 0):,}건"] if counts is not None else [e(r.법정동)]
        rows.append((*lead, e(r.단지명), f"<b>{eok(r.거래금액_억)}</b>", f"{r.전용면적:.0f}㎡",
                     f"{int(r.층)}층" if pd.notna(r.층) else "-", f"{r.계약일자:%m.%d}",
                     "직거래" if r.거래유형 == "직거래" else "중개", *tail))
    return rows


FRESH_HEAD_SGG = ["시군구", "단지", "거래가", "전용", "층", "계약일", "유형", "오늘 공개"]
FRESH_HEAD = ["순위", "단지", "거래가", "전용", "층", "계약일", "유형", "법정동"]


def subnav(items):
    """페이지 안 하위 메뉴(섹션 바로가기)."""
    return '<nav class="subnav">' + "".join(f'<a href="#{a}">{e(t)}</a>' for a, t in items if a) + "</nav>"


# ───────────────────────── 페이지 틀 ─────────────────────────
class Site:
    def __init__(self, out: Path, base_url: str):
        self.out, self.base_url = out, base_url.rstrip("/")
        self.pages = []
        g = lambda k: ads.conf(k)  # noqa: E731
        self.ga = ads.ga_head(g("GA_ID"))
        self.head_ads = self.ga + ("\n" if self.ga else "") + ads.adsense_head(g("ADSENSE_CLIENT"))
        self.adfit = ads.adfit(g("ADFIT_UNIT_PC"), g("ADFIT_UNIT_MO"))
        self.coupang = ads.coupang_block(g("COUPANG_ID"), g("COUPANG_TRACKING"), g("COUPANG_TEMPLATE"))
        self.ads_txt = ads.ads_txt(g("ADSENSE_CLIENT"))
        # 검색엔진 소유 확인(네이버 서치어드바이저·빙 웹마스터). 값이 있을 때만 meta를 넣는다.
        self.verify = "".join(f'\n<meta name="{n}" content="{e(v)}">' for n, v in [
            ("naver-site-verification", g("NAVER_SITE_VERIFICATION")), ("msvalidate.01", g("BING_SITE_VERIFICATION"))]
            if re.fullmatch(r"[A-Za-z0-9_-]{8,80}", v))

    def url(self, path):
        return f"{self.base_url}/" if path == "index.html" else f"{self.base_url}/{path}"

    def write(self, path: str, title: str, desc: str, body: str, crumbs=None, index=True):
        depth = path.count("/")
        up = "../" * depth
        url = self.url(path)
        robots = "" if index else '<meta name="robots" content="noindex">\n'
        crumb = ""
        if crumbs:
            crumb = '<nav class="crumb">' + " › ".join(
                f'<a href="{up}{h}">{e(t)}</a>' if h else e(t) for t, h in crumbs) + "</nav>"
        ld = self.jsonld(path, crumbs)
        doc = f"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title><meta name="description" content="{e(desc)}">
{robots}<link rel="canonical" href="{url}">
<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}">
<meta property="og:type" content="website"><meta property="og:url" content="{url}">
<meta property="og:site_name" content="k-apts"><meta property="og:locale" content="ko_KR">
<meta property="og:image" content="{self.base_url}/og.png"><meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630"><meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#0E1A2E">{self.verify}
<link rel="icon" href="{up}mark.svg" type="image/svg+xml"><link rel="apple-touch-icon" href="{up}logo-mark.png">
<link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@600;700&display=swap">
<link rel="stylesheet" href="{up}style.css">
{ld}{self.head_ads}
</head><body>
<header class="top"><div class="shell bar">
<button type="button" class="navbtn" aria-label="메뉴 열기" aria-controls="side" aria-expanded="false"
 onclick="kNav(this)"><span></span><span></span><span></span></button>
<a class="logo" href="{up}index.html">{MARK}<span>k-apts<small>실거래가 리포트</small></span></a>
<form class="search" role="search" onsubmit="return kGo(this)"><input name="q" list="k-regions" autocomplete="off"
 placeholder="지역 검색 (예: 대구 수성구)" aria-label="지역 검색"><button>검색</button></form>
</div></header>
<div class="shell layout">{self.side(path, up)}<div class="scrim" onclick="kNav()"></div>
<main class="content">{crumb}{body}</main></div>
<footer class="foot"><div class="shell">
<div class="brand">{MARK}<span>k-apts</span></div>
<p>국토교통부 아파트 매매 실거래가를 매일 새벽 받아 전국 시군구의 가격 흐름을 계산합니다.</p>
<p>실거래가: 국토교통부(공공데이터포털). 미분양·주택 준공: 국토교통부, 인구 이동: 국가데이터처(KOSIS).
행정구역 경계: 통계청 SGIS, vuski/admdongkor(CC BY 4.0).</p>
<p>이 사이트는 참고 자료이며 투자 판단의 근거로 정확성을 보증하지 않습니다.</p>
<p class="links sido">{"".join(f'<a href="{up}r/{c}.html">{n}</a>' for c, n in SIDO_SHORT.items())}</p>
<p class="links"><a href="{up}about.html">기준 안내</a><a href="{up}privacy.html">개인정보처리방침</a>
<a href="{REPO_URL}/issues">문의</a></p>
</div></footer>
<datalist id="k-regions"></datalist><script>window.K_UP="{up}"</script><script src="{up}regions.js" defer></script>{COPY_JS}
</body></html>"""
        f = self.out / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(doc, encoding="utf-8")
        if index:
            self.pages.append(path)

    def side(self, path, up):
        """왼쪽 트리 메뉴: 시세 보기 / 지역별 시세(시도 ▸ 시군구) / 안내. 지금 보는 페이지와 그 시도를 펼쳐 둔다."""
        cur_sido = path[2:4] if path.startswith("r/") else None

        def link(href, text, cls=""):
            on = " on" if href == path else ""
            cur = ' aria-current="page"' if on else ""
            return f'<a class="{(cls + on).strip()}" href="{up}{href}"{cur}>{e(text)}</a>'

        main_links = [("index.html", "전국 시세"), ("today.html", "오늘 공개 거래"), ("stats.html", "월간 통계")]
        out = ['<aside class="side" id="side"><nav aria-label="사이트 메뉴">',
               '<p class="side-h">시세 보기</p><ul class="tree">'
               + "".join(f"<li>{link(h, t)}</li>" for h, t in main_links) + "</ul>",
               '<p class="side-h">지역별 시세</p><ul class="tree">']
        for sido, short, subs in getattr(self, "tree", []):
            op = " open" if sido == cur_sido else ""
            kids = "".join(f"<li>{link(f'r/{c}.html', nm)}</li>" for c, nm in subs)
            out.append(f'<li><details{op}><summary>{e(short)}</summary><ul>'
                       f'<li>{link(f"r/{sido}.html", f"{short} 전체", "all")}</li>{kids}</ul></details></li>')
        out.append('</ul><p class="side-h">안내</p><ul class="tree">'
                   f'<li>{link("about.html", "기준 안내")}</li><li>{link("privacy.html", "개인정보처리방침")}</li>'
                   '</ul></nav></aside>')
        return "".join(out)

    def jsonld(self, path, crumbs):
        """검색 결과에 경로(전국 › 시도 › 시군구)가 보이도록 BreadcrumbList, 첫 화면엔 WebSite."""
        if path == "index.html":
            org = {"@type": "Organization", "@id": f"{self.base_url}/#org", "name": "k-apts",
                   "url": f"{self.base_url}/", "logo": f"{self.base_url}/logo-mark.png"}
            d = {"@context": "https://schema.org", "@graph": [org, {
                "@type": "WebSite", "name": "k-apts", "alternateName": "k-apts 전국 아파트 실거래가",
                "url": f"{self.base_url}/", "inLanguage": "ko", "publisher": {"@id": f"{self.base_url}/#org"}}]}
        elif crumbs:
            d = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "name": t, "item": self.url(h or path)}
                for i, (t, h) in enumerate(crumbs)]}
        else:
            return ""
        js = json.dumps(d, ensure_ascii=False).replace("</", "<\\/")
        return f'<script type="application/ld+json">{js}</script>\n'

    def ad(self):
        return self.adfit

    def finish(self):
        lm = f"<lastmod>{self.lastmod}</lastmod>" if getattr(self, "lastmod", "") else ""
        urls = "".join(f"<url><loc>{self.url(p)}</loc>{lm}</url>" for p in self.pages)
        (self.out / "sitemap.xml").write_text(
            f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{urls}</urlset>", encoding="utf-8")
        (self.out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {self.base_url}/sitemap.xml\n")
        if self.ads_txt:
            (self.out / "ads.txt").write_text(self.ads_txt)
        domain = os.environ.get("SITE_DOMAIN", "").strip()
        if domain:
            (self.out / "CNAME").write_text(domain + "\n")
        (self.out / ".nojekyll").write_text("")


# ───────────────────────── 계산 ─────────────────────────
def load_sido(sido, ref):
    raw = A.prepare(enrich(pd.read_parquet(OUT_DIR / f"trades_{sido}.parquet")))
    t_all = raw[~raw["해제"]]
    t = t_all[t_all["거래유형"] != "직거래"]
    last = pd.Timestamp(t["계약일자"].max())
    recent = t[t["계약일자"] > last - pd.Timedelta(days=30)]
    q3 = t[t["계약일자"] > last - pd.Timedelta(days=90)]
    pc = A.price_changes(t[t["계약월"] <= ref], ref)
    pc = pc[(pc["최근_건수"] >= 2) & (pc["1년전_건수"] >= 2)].dropna(subset=["1년전대비"]).copy()
    pc["구간"] = pd.cut(pc["1년전대비"], [10, 20, 30, np.inf], right=False,
                      labels=["10~20%", "20~30%", "30% 이상"]).astype(str)
    return recent, pc, last, q3, raw, t_all, t


SIZES = [("84", "전용 84㎡ 안팎(이른바 국민평형)", 80, 86), ("59", "전용 59㎡ 안팎", 55, 62)]


def size_section(q3, name, last):
    """많이 찾는 면적(84㎡·59㎡)의 최근 3개월 단지별 중위 거래가 순위."""
    out = []
    for key, label, lo, hi in SIZES:
        g = q3[(q3["전용면적"] >= lo) & (q3["전용면적"] < hi)]
        if len(g) < 5:
            continue
        agg = (g.groupby("단지키").agg(단지명=("단지명", "first"), 법정동=("법정동", "first"),
                                     준공=("건축년도", "max"), 중위=("거래금액_억", "median"),
                                     최저=("거래금액_억", "min"), 최고=("거래금액_억", "max"),
                                     건수=("거래금액_억", "size"))
               .sort_values(["중위", "건수"], ascending=False).head(10))
        rows = [(i + 1, e(r.단지명), f"<b>{eok(r.중위)}</b>",
                 eok(r.최저) if r.건수 > 1 else "-", eok(r.최고) if r.건수 > 1 else "-", f"{r.건수}건",
                 e(r.법정동), int(r.준공) if pd.notna(r.준공) else "-") for i, r in enumerate(agg.itertuples())]
        out.append(f'<h3 id="s{key}">{label}</h3><p class="sub">거래 {len(g):,}건, 전체 중위 거래가 '
                   f'{eok(g["거래금액_억"].median())}. 단지별 중위 거래가가 높은 10곳입니다.</p>'
                   + table(["순위", "단지", "중위", "최저", "최고", "거래", "법정동", "준공"], rows, "rank"))
    if not out:
        return ""
    return (f"<h2 id='size'>{e(name)} 많이 찾는 면적별 시세</h2><p class='sub'>계약일 "
            f"{last - pd.Timedelta(days=89):%Y.%m.%d}~{last:%Y.%m.%d} 최근 3개월 거래(해제·직거래 제외)로 "
            "같은 면적대끼리 비교했습니다.</p>" + "".join(out))


def top10(g):
    return g.sort_values(["거래금액", "계약일자"], ascending=[False, False]).drop_duplicates("단지키").head(10)


def rank_table(top):
    rows = [(i + 1, e(r.단지명), f'<b>{eok(r.거래금액_억)}</b>', f"{r.전용면적:.0f}㎡",
             f"{int(r.층)}층" if pd.notna(r.층) else "-", f"{r.계약일자:%m.%d}", e(r.법정동))
            for i, r in enumerate(top.itertuples())]
    return table(["순위", "단지", "거래가", "전용", "층", "계약일", "법정동"], rows, "rank")


def riser_table(sub, n=None):
    sub = sub.sort_values("1년전대비", ascending=False)
    if n:
        sub = sub.head(n)
    rows = [(e(f"{r['단지명']} {r['면적타입']}㎡"), f'<b class="up">{r["1년전대비"]:+.1f}%</b>', eok(r["최근"]),
             eok(r["1년전"]), e(f"{r['시군구']} {r['법정동']}"),
             int(r["건축년도"]) if pd.notna(r["건축년도"]) else "-") for _, r in sub.iterrows()]
    return table(["단지", "변동", "최근", "1년 전", "지역", "준공"], rows)


def band_section(pc, label, sgg_level):
    if pc.empty:
        return ""
    cnt = pc["구간"].value_counts()
    n10, n20, n30 = (int(cnt.get(k, 0)) for k in ["10~20%", "20~30%", "30% 이상"])
    out = [f'<h2 id="rise">1년 전보다 많이 오른 아파트</h2><p class="sub">{e(label)}에서 1년 전과 비교할 수 있는 '
           f'{len(pc):,}곳(같은 단지·같은 면적, 두 시점 각 2건 이상)의 최근 3개월과 1년 전 같은 3개월 '
           "중위 거래가를 비교했습니다.</p>"
           f'<div class="bands"><a href="#b30"><small>30% 이상</small><b>{n30}곳</b></a>'
           f'<a href="#b20"><small>20~30%</small><b>{n20}곳</b></a><a href="#b10"><small>10~20%</small><b>{n10}곳</b></a></div>']
    if not sgg_level and pc["시군구"].nunique() > 1:
        ct = pd.crosstab(pc["시군구"], pc["구간"]).reindex(columns=["30% 이상", "20~30%", "10~20%"], fill_value=0)
        ct = ct.reindex(pc["시군구"].unique(), fill_value=0)
        ct["비교"] = pc.groupby("시군구").size()
        ct = ct.fillna(0)
        ct = ct.sort_values(["30% 이상", "20~30%", "10~20%"], ascending=False)
        out.append(table(["시군구", "30% 이상", "20~30%", "10~20%", "비교한 곳"],
                         [(e(i), *map(int, r.values)) for i, r in ct.iterrows()]))
    for b, n in [("30% 이상", None), ("20~30%", None), ("10~20%", 30)]:
        sub = pc[pc["구간"] == b]
        if len(sub):
            more = f" (상위 {n}곳)" if n and len(sub) > n else ""
            anchor = {"30% 이상": "b30", "20~30%": "b20", "10~20%": "b10"}[b]
            out.append(f'<h3 id="{anchor}">{b} 오른 곳{more}</h3>' + riser_table(sub, n))
    return "".join(out)


def briefing_html(s, parent, child_word, sido_short):
    parts = [("시장 전체", B.market_section(s, child_word)), ("왜 오르고 내리나", A.drivers(s, parent)),
             ("수요와 공급 (공공통계)", A.external_factors(s, sido_short)),
             ("신축과 구축", B.age_section(s)), ("면적별", B.area_section(s)),
             ("집을 살 때 볼 숫자", B.buyer_section(s))]
    out = []
    for t, lines in parts:
        if lines:
            out.append(f"<h3>{t}</h3>{ul(lines)}")
    lead = s.get("상승주도") or []
    if lead:
        out.append("<p>상승 폭이 큰 단지: " + e(", ".join(f"{x['단지']}({x['지역']}) {pct(x['변동'])}"
                                                      for x in lead[:5])) + "</p>")
    return "".join(out)


def hero(name, parent_name, s):
    j = s.get("판정", "판단 보류")
    why = ("거래가 적어 방향을 판단하기 어렵습니다." if j == "판단 보류" else
           f"같은 단지·같은 면적 기준 가격지수는 최근 3개월 {pct(s.get('3개월'))}, 1년 {pct(s.get('1년'))}입니다.")
    p = f'<span class="eyebrow">{e(parent_name)}</span>' if parent_name else ""
    return (f'<section class="hero card"><div class="hero-top"><div>{p}<h1>{e(name)} 아파트 실거래가</h1></div>'
            f'</div><p class="why">{why} 최근 3개월 판정은 {badge(j)}입니다.</p>'
            f'{sigtable([(e(name), s)])}</section>')


def page_desc(name, s, top, n, ref, tail, where=""):
    """검색 결과 설명문: 그 지역의 실제 숫자(최고가 거래·가격지수 변동)로 페이지마다 다르게 쓴다."""
    parts = [f"{name} 아파트 실거래가({ref:%Y년 %m월} 기준)."]
    if top is not None:
        w = f"{where} " if where else ""
        parts.append(f"최근 한 달 {n:,}건 중 최고가는 {w}{top.단지명} {top.전용면적:.0f}㎡ {eok(top.거래금액_억)}.")
    j = s.get("판정")
    if isinstance(j, str) and j != "판단 보류" and pd.notna(s.get("3개월")):
        parts.append(f"동일단지 가격지수 3개월 {pct(s.get('3개월'))}, 1년 {pct(s.get('1년'))}({j}).")
    parts.append(tail + ".")
    return " ".join(parts)


# ───────────────────────── 생성 ─────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site")
    ap.add_argument("--base-url", default=os.environ.get("SITE_URL", "https://mapcityne.github.io/daegu-apt-dashboard"))
    a = ap.parse_args()
    out = BASE / a.out
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    shutil.copy(BASE / "site_assets" / "style.css", out / "style.css")
    shutil.copy(BASE / "assets" / "logo.png", out / "logo.png")
    shutil.copy(BASE / "site_assets" / "logo-mark.png", out / "logo-mark.png")
    shutil.copy(BASE / "site_assets" / "og.png", out / "og.png")
    (out / "mark.svg").write_text(MARK.replace('class="mark" ', 'xmlns="http://www.w3.org/2000/svg" '), encoding="utf-8")

    meta = json.loads((OUT_DIR / "meta.json").read_text(encoding="utf-8"))
    ref = pd.Timestamp(meta["기준월"] + "-01")
    stats = pd.read_parquet(OUT_DIR / "stats.parquet")
    S = stats.set_index("코드")
    row = lambda c: B.row_dict(S.loc[c]) if c in S.index else {"판정": "판단 보류"}  # noqa: E731
    reg = regions()
    sname = reg.set_index("sggcd")["sggnm"]
    rmap = {"전국": "index.html"} | {v: f"r/{k}.html" for k, v in SIDO_SHORT.items()} | {
        f"{SIDO_SHORT[r.sidocd]} {r.sggnm}": f"r/{r.sggcd}.html" for r in reg.itertuples()}
    (out / "regions.js").write_text(
        "window.K_REGIONS=" + json.dumps(rmap, ensure_ascii=False) + ";\n" + SEARCH_JS, encoding="utf-8")
    site = Site(out, a.base_url)
    site.tree = [(sd, SIDO_SHORT[sd], [(r.sggcd, r.sggnm) for r in reg[reg["sidocd"] == sd].sort_values("sggcd").itertuples()])
                 for sd in SIDO_SHORT if (OUT_DIR / f"trades_{sd}.parquet").exists()]
    site.lastmod = meta["생성"][:10]
    stamp = f"{ref:%Y년 %m월} 기준 · 갱신 {meta['생성'][:10]}"
    nat = row("00")
    gen = pd.Timestamp(meta["생성"][:10])
    idx_all = pd.read_parquet(OUT_DIR / "index.parquet")
    idx_g = {c: g.set_index("계약월")["지수"] for c, g in idx_all.groupby("코드")}
    fresh = ST.Fresh(meta["생성"][:10])
    fresh_by_sido, sido_month, nat_cnt, nat_px = {}, {}, [], []
    sgg_month = {}

    sido_rows, sido_first = [], {}
    for sido in SIDO_SHORT:
        if not (OUT_DIR / f"trades_{sido}.parquet").exists():
            continue
        recent, pc, last, q3, raw, t_all, t_px = load_sido(sido, ref)
        new = fresh.pick(raw, last)
        new = new[~new["해제"]]
        new_cnt = new.groupby("시군구코드").size().to_dict()
        fresh_by_sido[sido] = (new, new_cnt)
        fresh.recent[sido] = raw[~raw["해제"] & (raw["계약일자"] > last - pd.Timedelta(days=3))]
        cut = last.to_period("M").to_timestamp() - pd.DateOffset(months=26)
        nat_cnt.append(t_all.loc[t_all["계약월"] >= cut, "계약월"].value_counts())
        nat_px.append(t_px.loc[t_px["계약월"] >= cut, ["계약월", "거래금액_억", "평당가"]])
        sido_month[sido] = ST.monthly(t_all, t_px, idx_g.get(sido), last)
        s = row(sido)
        short = SIDO_SHORT[sido]
        subs = reg[reg["sidocd"] == sido].sort_values("sggcd")
        sib = stats[(stats["단계"] == "시군구") & (stats["상위"] == sido)].copy()
        sib["nm"] = sib["코드"].map(sname)
        sib = sib.dropna(subset=["nm"]).sort_values("코드")
        # ── 시군구 페이지
        firsts = []
        for _, rg in subs.iterrows():
            code = rg["sggcd"]
            ss = row(code)
            r_sgg = recent[recent["시군구코드"] == code]
            p_sgg = pc[pc["시군구코드"] == code]
            name = f"{short} {rg['sggnm']}"
            body = [hero(rg["sggnm"], short, ss), f'<p class="stamp">{stamp}</p>', None, site.ad()]
            nav = []
            n_sgg = new[new["시군구코드"] == code]
            if len(n_sgg):
                nav.append(("today", "오늘 공개"))
                body.append(f'<h2 id="today">오늘 새로 공개된 거래</h2><p class="sub">{gen:%Y.%m.%d} 갱신에서 처음 '
                            f"나타난 {rg['sggnm']} 거래 {len(n_sgg):,}건 중 비싼 순서 10건입니다(해제 거래 제외). "
                            f'<a href="../today.html">전국 보기</a></p>'
                            + table(FRESH_HEAD, fresh_rows(n_sgg, False), "rank"))
            if len(r_sgg):
                tp = top10(r_sgg)
                firsts.append((code, rg["sggnm"], tp.iloc[0], len(r_sgg)))
                nav.append(("rank", "실거래 순위"))
                body.append(f"<h2 id='rank'>최근 한 달 실거래가 순위</h2><p class='sub'>계약일 "
                            f"{last - pd.Timedelta(days=29):%Y.%m.%d}~{last:%Y.%m.%d}, 거래 {len(r_sgg):,}건 중 "
                            "단지별 가장 비싼 거래 10곳입니다(직거래 제외).</p>" + rank_table(tp))
            sz = size_section(q3[q3["시군구코드"] == code], name, last)
            body.append(sz)
            nav.append(("size", "면적별 시세") if sz else (None, None))
            dm = ST.monthly(t_all[t_all["시군구코드"] == code], t_px[t_px["시군구코드"] == code], idx_g.get(code), last)
            sgg_month[code] = (short, rg["sggnm"], dm)
            mon = monthly_section(name, dm, last, gen)
            body.append(mon)
            nav.append(("monthly", "월별 통계") if mon else (None, None))
            bs = band_section(p_sgg, name, True)
            body.append(bs)
            nav += [("rise", "많이 오른 곳") if bs else (None, None), ("brief", "시장 브리핑")]
            body[2] = subnav(nav)
            body.append(f"<h2 id='brief'>{e(rg['sggnm'])} 시장 브리핑</h2><div class='card brief'>"
                        + briefing_html(ss, row(sido), "법정동", short) + "</div>")
            body.append(sibling_section(short, code, sib))
            body.append(site.coupang)
            site.write(f"r/{code}.html", f"{name} 아파트 실거래가 순위와 시세 흐름 | k-apts",
                       page_desc(name, ss, tp.iloc[0] if len(r_sgg) else None, len(r_sgg), ref,
                                 ("최근 한 달 실거래가 순위 10, " + ("84㎡·59㎡ 단지별 시세, " if sz else "")
                                  + "1년 전보다 많이 오른 아파트") if len(r_sgg)
                                 else "시장 브리핑과 같은 시도 다른 시군구 시세"),
                       "".join(body), [("전국", "index.html"), (short, f"r/{sido}.html"), (rg["sggnm"], None)])
        # ── 시도 페이지
        firsts.sort(key=lambda x: -x[2]["거래금액"])
        body = [hero(short, None, s), f'<p class="stamp">{stamp}</p>', None, site.ad()]
        nav = []
        if len(new):
            nav.append(("today", "오늘 공개"))
            body.append(f'<h2 id="today">오늘 새로 공개된 거래 · 시군구별 최고</h2><p class="sub">{gen:%Y.%m.%d} '
                        f"갱신에서 처음 나타난 {short} 거래 {len(new):,}건을 시군구마다 가장 비싼 1건씩 모았습니다"
                        f'(해제 거래 제외). <a href="../today.html">전국 보기</a></p>'
                        + table(FRESH_HEAD_SGG, fresh_rows(new, "link", new_cnt)))
        if firsts:
            nav.append(("rank", "시군구별 1위"))
            rows = [(f'<a href="{c}.html">{e(n)}</a>', e(t.단지명), f"<b>{eok(t.거래금액_억)}</b>",
                     f"{t.전용면적:.0f}㎡", f"{t.계약일자:%m.%d}", f"{k:,}건") for c, n, t, k in firsts]
            body.append("<h2 id='rank'>최근 한 달 시군구별 1위 거래</h2><p class='sub'>시군구 이름을 누르면 10위까지 볼 수 있습니다."
                        "</p>" + table(["시군구", "단지", "거래가", "전용", "계약일", "한 달 거래"], rows))
            sido_first[sido] = firsts[0]
        sg = stats[(stats["단계"] == "시군구") & (stats["상위"] == sido)].copy()
        sg["nm"] = sg["코드"].map(sname)
        sg = sg.sort_values("3개월", ascending=False, na_position="last")
        mon = monthly_section(short, sido_month[sido], last, gen)
        body.append(mon)
        if mon:
            nav.append(("monthly", "월별 통계"))
        nav += [("flow", "시군구별 흐름"), ("rise" if len(pc) else None, "많이 오른 곳"), ("brief", "시장 브리핑")]
        body[2] = subnav(nav)
        body.append("<h2 id='flow'>시군구별 가격 흐름</h2><p class='sub'>같은 단지끼리 비교한 가격지수입니다. 최근 3개월 "
                    "상승 폭이 큰 순서이고, 누르면 그 지역 순위와 브리핑을 볼 수 있습니다.</p><div class='rgrid'>"
                    + "".join(region_card(f"{r['코드']}.html", r["nm"], B.row_dict(r)) for _, r in sg.iterrows())
                    + "</div>")
        body.append(band_section(pc, short, False) if len(pc) else '')
        body.append(f"<h2 id='brief'>{e(short)} 시장 브리핑</h2><div class='card brief'>" + briefing_html(s, nat, "시군구", None)
                    + "</div>")
        body.append(site.coupang)
        site.write(f"r/{sido}.html", f"{short} 아파트 실거래가 순위와 시군구별 시세 | k-apts",
                   page_desc(short, s, firsts[0][2] if firsts else None, len(recent), ref,
                             "시군구별 1위 거래와 가격 흐름, 많이 오른 아파트", firsts[0][1] if firsts else ""),
                   "".join(body), [("전국", "index.html"), (short, None)])
        sido_rows.append((sido, short, s))
        print(f"{short}: 시군구 {len(subs)}쪽", flush=True)

    # ── 전국 월별 통계 · 월간 통계 페이지
    last_all = pd.Timestamp(meta["데이터_끝"])
    nc = pd.concat(nat_cnt, axis=1, sort=True).sum(axis=1)
    npx = pd.concat(nat_px)
    nat_m = ST.monthly(pd.DataFrame({"계약월": nc.index.repeat(nc.astype(int).values)}), npx, idx_g.get("00"),
                       last_all)
    sm = summary_month(nat_m)
    build_stats_page(site, nat_m, sido_month, sgg_month, sm, last_all, gen, stamp)
    # ── 오늘 공개된 거래 페이지
    today_counts = {c: int(len(v[0])) for c, v in fresh_by_sido.items()}
    build_today_page(site, fresh, fresh_by_sido, today_counts, gen, last_all)
    fresh.save(today_counts)

    # ── 첫 화면
    j = nat.get("판정", "판단 보류")
    sg = stats[stats["단계"] == "시군구"].copy()
    sg["nm"] = sg["코드"].map(lambda c: f"{SIDO_SHORT.get(c[:2], '')} {sname.get(c, c)}")
    up = sg[sg["판정"] == "상승"].sort_values("3개월", ascending=False).head(12)
    dn = sg[sg["판정"] == "하락"].sort_values("3개월").head(12)

    def plates(df):
        return '<div class="plates">' + "".join(
            f'<a class="plate" href="r/{r["코드"]}.html"><i style="background:{VCOLOR[r["판정"]]}"></i>'
            f'<span>{e(r["nm"])}</span><b class="{sign_cls(pct(r["3개월"]))}">{pct(r["3개월"])}</b></a>'
            for _, r in df.iterrows()) + "</div>"

    n_sgg = int((stats["단계"] == "시군구").sum())
    by_sido = {c: (short, st) for c, short, st in sido_rows}
    first = ["11", "41", "26", "27"]  # 서울·경기·부산·대구를 먼저
    sido_link = lambda c: f'<a href="r/{c}.html">{e(by_sido[c][0])}</a>'
    sig_main = [("전국", nat)] + [(sido_link(c), by_sido[c][1]) for c in first if c in by_sido]
    sig_more = [(sido_link(c), by_sido[c][1]) for c in SIDO_SHORT if c in by_sido and c not in first]
    body = [f'<section class="home"><p class="dateline">{gen:%Y년 %-m월 %-d일} 갱신 · 국토교통부 실거래가 '
            f'{meta["거래수"] / 1e4:,.0f}만 건, 전국 {n_sgg}개 시군구</p>'
            f"<h1>전국 아파트값 3개월 {pct(nat.get('3개월'))}, 1년 {pct(nat.get('1년'))}</h1>"
            f'<p class="lead">{ref:%Y년 %-m월} 기준, 같은 단지끼리 비교한 가격지수입니다. 최근 3개월 흐름은 '
            f'<b>{e(j)}</b>{"로" if j.endswith("류") else "으로"} 판정했습니다.</p>'
            '<p class="home-links"><a href="today.html">오늘 공개된 거래</a><a href="stats.html">월간 통계</a>'
            '<a href="r/27.html">대구 실거래가 순위</a></p></section>',
            f'<section class="signal"><h2>지역별 시장 신호</h2><p class="sub">전국 최근 3개월 판정: {badge(j)}</p>'
            f"{sigtable(sig_main, sig_more)}"
            '<p class="dl"><a href="data/sido_signal.csv" download>표 데이터 내려받기 (CSV)</a></p></section>',
            story_items(sg, gen, pd.Timestamp(meta["데이터_끝"])), site.ad()]
    (site.out / "data").mkdir(parents=True, exist_ok=True)
    (site.out / "data" / "sido_signal.csv").write_text(
        sig_csv([("전국", nat)] + [(by_sido[c][0], by_sido[c][1]) for c in SIDO_SHORT if c in by_sido]),
        encoding="utf-8")
    if sm is not None:
        ln, src = news_lines("전국", nat_m, sm, last_all, gen)
        body.append(f"<h2>{sm:%-m월} 전국 아파트 매매 통계</h2><p class='sub'>시도·시군구별 거래량과 가격지수 비교는 "
                    "월간 통계에서 볼 수 있습니다.</p>"
                    + quote_card(sm, ln, src, '<p class="more-in"><a href="stats.html">월간 통계 전체 보기 →</a></p>'))

    def first_line(c):
        if c not in sido_first:
            return ""
        _, nm, t, _ = sido_first[c]
        return (f'<div class="rc-foot">한 달 최고가 <b>{eok(t.거래금액_억)}</b><br><span>{e(nm)} '
                f'{e(t.단지명)}</span></div>')

    body.append("<h2>시도별 시세</h2><p class='sub'>색은 최근 3개월 판정입니다. 누르면 시군구별 순위로 갑니다.</p>"
                "<div class='rgrid'>" + "".join(region_card(f"r/{c}.html", n, s, first_line(c))
                                               for c, n, s in sido_rows) + "</div>")
    body.append('<div class="two"><div><h2>지금 오르는 곳</h2><p class="sub">최근 3개월 가격지수가 1% 넘게 오른 '
                f'시군구 중 상승 폭이 큰 순서입니다.</p>{plates(up)}</div><div><h2>지금 내리는 곳</h2>'
                f'<p class="sub">1% 넘게 내린 시군구 중 하락 폭이 큰 순서입니다.</p>{plates(dn)}</div></div>')
    body.append("<h2>이번 달 전국 시장 이야기</h2><div class='card brief'>" + briefing_html(nat, None, "시도", None)
                + "</div>")
    body.append(site.coupang)
    site.write("index.html", "k-apts 전국 아파트 실거래가 순위와 시세 흐름",
               f"전국 시군구 아파트 실거래가 순위, 1년 전보다 많이 오른 아파트, 지역별 상승·하락 판정을 매일 갱신합니다. "
               f"{ref:%Y년 %m월} 기준 전국 동일단지 가격지수 3개월 {pct(nat.get('3개월'))}, 1년 {pct(nat.get('1년'))}.",
               "".join(body))

    site.write("about.html", "기준 안내 | k-apts", "k-apts가 숫자를 만드는 방법과 데이터 출처.", ABOUT,
               [("전국", "index.html"), ("기준 안내", None)])
    privacy = PRIVACY.replace("<h2>접속 기록</h2>", GA_NOTE + "<h2>접속 기록</h2>") if site.ga else PRIVACY
    site.write("privacy.html", "개인정보처리방침 | k-apts", "k-apts 개인정보처리방침.", privacy,
               [("전국", "index.html"), ("개인정보처리방침", None)])
    site.write("404.html", "페이지를 찾을 수 없습니다 | k-apts", "", '<h1>페이지를 찾을 수 없습니다</h1>'
               '<p><a href="index.html">전국 첫 화면으로 가기</a></p>', index=False)
    site.finish()
    print(f"완료: {len(site.pages)}쪽 → {out}")


def build_stats_page(site, nat_m, sido_month, sgg_month, m, last, gen, stamp):
    """전국·시도·시군구 월별 통계를 한 페이지에서 비교(기사 근거용)."""
    if m is None:
        return
    prev = m - pd.DateOffset(months=1)
    st = nat_m.loc[m, "상태"]
    tag = " (잠정)" if st == "잠정" else ""

    def cells(d):
        r = d.loc[m] if m in d.index else None
        if r is None or pd.isna(r["거래량"]):
            return ["-"] * 6
        f = lambda v, n=1: f'<span class="{sign_cls(pct(v))}">{pct(v, n)}</span>'  # noqa: E731
        return [f"{int(r['거래량']):,}", f(r["거래량_전월"], 0), f(r["거래량_전년"], 0), eok(r["중위가"]),
                f(r["지수_전월"]), f(r["지수_전년"])]

    head = ["거래량", f"전월({prev:%-m월}) 대비", "전년 동월 대비", "중위 거래가", "가격지수 전월 대비", "가격지수 1년"]
    sido_rows = [(f'<a href="r/{c}.html">{SIDO_SHORT[c]}</a>', *cells(d)) for c, d in sido_month.items()]
    sg = []
    for c, (sh, nm, d) in sgg_month.items():
        if m in d.index and pd.notna(d.loc[m, "거래량"]) and d.loc[m, "거래량"] >= 30:
            sg.append((c, sh, nm, d.loc[m]))
    by_vol = sorted(sg, key=lambda x: -x[3]["거래량"])[:15]
    yoy = [x for x in sg if pd.notna(x[3]["거래량_전년"])]
    up = sorted(yoy, key=lambda x: -x[3]["거래량_전년"])[:10]
    dn = sorted(yoy, key=lambda x: x[3]["거래량_전년"])[:10]
    idx_up = sorted([x for x in sg if pd.notna(x[3]["지수_전월"])], key=lambda x: -x[3]["지수_전월"])[:10]
    idx_dn = sorted([x for x in sg if pd.notna(x[3]["지수_전월"])], key=lambda x: x[3]["지수_전월"])[:10]

    def sgg_tbl(lst, col, n=1):
        rows = [(i + 1, f'<a href="r/{c}.html">{e(sh)} {e(nm)}</a>', f"{int(r['거래량']):,}",
                 f'<b class="{sign_cls(pct(r[col]))}">{pct(r[col], n)}</b>') for i, (c, sh, nm, r) in enumerate(lst)]
        return table(["순위", "시군구", "거래량", "변동"], rows, "rank")

    body = [f'<h1>{m:%Y년 %-m월} 아파트 매매 월간 통계{tag}</h1><p class="stamp">{stamp}</p>',
            subnav([("nat", "전국 월별"), ("sido", "시도 비교"), ("vol", "거래 많은 시군구"), ("yoy", "거래량 증감"),
                    ("idx", "가격지수 변동")]),
            monthly_section("전국", nat_m, last, gen, "nat"),
            f'<h2 id="sido">시도별 {m:%-m월} 비교</h2><p class="sub">시도 이름을 누르면 그 시도의 월별 통계와 '
            "시군구별 순위로 갑니다.</p>" + table(["시도", *head], sido_rows),
            f'<h2 id="vol">{m:%-m월} 거래가 많은 시군구</h2><p class="sub">해제 거래를 뺀 매매 신고 건수 순서입니다.</p>'
            + sgg_tbl(by_vol, "거래량_전년", 0).replace("<th>변동</th>", "<th>전년 동월 대비</th>"),
            f'<h2 id="yoy">1년 전보다 거래가 많이 늘어난·줄어든 시군구</h2><p class="sub">{m:%-m월} 거래 30건 이상인 '
            "시군구만 비교했습니다.</p><div class='two'><div><h3>늘어난 곳</h3>" + sgg_tbl(up, "거래량_전년", 0)
            + "</div><div><h3>줄어든 곳</h3>" + sgg_tbl(dn, "거래량_전년", 0) + "</div></div>",
            f'<h2 id="idx">가격지수가 전월보다 많이 오른·내린 시군구</h2><p class="sub">같은 단지·같은 면적끼리 비교한 '
            "지수의 전월 대비 변동입니다. 한 달 변동은 거래 구성에 따라 흔들릴 수 있어 3개월 흐름과 함께 보세요.</p>"
            "<div class='two'><div><h3>오른 곳</h3>" + sgg_tbl(idx_up, "지수_전월") + "</div><div><h3>내린 곳</h3>"
            + sgg_tbl(idx_dn, "지수_전월") + "</div></div>",
            site.coupang]
    nat = nat_m.loc[m]
    desc = (f"{m:%Y년 %-m월} 전국 아파트 매매 {int(nat['거래량']):,}건(전월 대비 {pct(nat['거래량_전월'], 0)}, "
            f"전년 동월 대비 {pct(nat['거래량_전년'], 0)}). 시도·시군구별 거래량과 중위 거래가, 가격지수 변동을 "
            "기사 인용용 문장과 함께 정리했습니다.")
    site.write("stats.html", f"{m:%Y년 %-m월} 아파트 매매 월간 통계 · 시도·시군구 거래량 | k-apts", desc,
               "".join(body), [("전국", "index.html"), ("월간 통계", None)])


def build_today_page(site, fresh, by_sido, counts, gen, last):
    """이번 갱신에서 처음 공개된 실거래: 시도별 건수와 시군구별 최고가."""
    total = sum(counts.values())
    if fresh.ready:
        log = dict(fresh.log)
        log[fresh.today] = counts
        days = sorted(log)[-14:]
        daily = [(d, sum(log[d].values())) for d in days]
        trend = ""
        if len(daily) >= 2:
            vmax = max(v for _, v in daily) or 1
            trend = ("<h2 id='trend'>날짜별 공개 건수</h2><p class='sub'>매일 새벽 갱신 때 새로 나타난 거래 수입니다"
                     "(주말·공휴일 신고분은 다음 평일에 몰립니다).</p>"
                     + table(["갱신일", "전국 새 거래"], [(d[5:].replace("-", "."), vol_bar(v, vmax))
                                                       for d, v in reversed(daily)]))
            if len(daily) >= 14:
                w1, w0 = sum(v for _, v in daily[-7:]), sum(v for _, v in daily[-14:-7])
                trend = (f"<p class='lead'>최근 7일 공개 {w1:,}건, 그 전 7일 {w0:,}건"
                         f"({pct((w1 / w0 - 1) * 100 if w0 else None, 0)}).</p>" + trend)
        intro = (f"<p class='lead'>{gen:%Y년 %-m월 %-d일} 새벽 갱신에서 전국 <b>{total:,}건</b>의 아파트 매매 실거래가 "
                 f"새로 공개됐습니다({fresh.base_date} 갱신과 비교, 해제 거래 제외).</p>")
        sido_rows = [(f'<a href="#s{c}">{SIDO_SHORT[c]}</a>', f"{n:,}건") for c, n in
                     sorted(counts.items(), key=lambda x: -x[1]) if n]
        parts = [intro, subnav([("bysido", "시도별 건수"), ("trend" if trend else None, "날짜별 추이")]
                               + [(f"s{c}", SIDO_SHORT[c]) for c, n in counts.items() if n]),
                 "<h2 id='bysido'>시도별 새 거래</h2>" + table(["시도", "새로 공개"], sido_rows), trend]
        for c, (new, cnt) in by_sido.items():
            if len(new):
                parts.append(f'<h2 id="s{c}"><a href="r/{c}.html">{SIDO_SHORT[c]}</a> 시군구별 최고 거래 '
                             f'<small class="muted">{len(new):,}건</small></h2>'
                             + table(FRESH_HEAD_SGG, fresh_rows(new, True, cnt)))
        title = f"오늘 공개된 아파트 실거래가 · 시군구별 최고 ({gen:%Y.%m.%d}) | k-apts"
        desc = (f"{gen:%Y년 %-m월 %-d일} 새로 공개된 전국 아파트 매매 실거래 {total:,}건을 시도·시군구별 최고가로 "
                "정리했습니다. 매일 새벽 국토교통부 실거래가 갱신분 기준.")
    else:
        # 비교할 전날 목록이 아직 없을 때(첫 실행): 최근 계약일 기준으로 대신 보여 준다.
        lo = last - pd.Timedelta(days=2)
        parts = [f"<p class='lead'>새로 공개된 거래 집계는 다음 갱신부터 시작합니다. 그동안은 최근 3일"
                 f"({lo:%m.%d}~{last:%m.%d}) 계약된 거래를 시군구별 최고가로 보여 드립니다.</p>",
                 subnav([(f"s{c}", SIDO_SHORT[c]) for c in by_sido])]
        for c, (new, cnt) in by_sido.items():
            rec = fresh.recent.get(c)
            if rec is not None and len(rec):
                rc = rec.groupby("시군구코드").size().to_dict()
                parts.append(f'<h2 id="s{c}"><a href="r/{c}.html">{SIDO_SHORT[c]}</a> 시군구별 최고 거래</h2>'
                             + table(FRESH_HEAD_SGG[:-1] + ["3일 거래"], fresh_rows(rec, True, rc)))
        title = "최근 공개된 아파트 실거래가 · 시군구별 최고 | k-apts"
        desc = "최근 계약된 전국 아파트 매매 실거래를 시도·시군구별 최고가로 정리했습니다. 매일 새벽 갱신."
    body = (f'<h1>오늘 공개된 아파트 실거래가</h1><p class="sub">아파트 매매는 계약 후 30일 안에 신고하고, '
            "신고된 거래는 국토교통부 실거래가 자료로 공개됩니다. 신고일은 공개되지 않아, 매일 새벽 갱신 때 처음 나타난 거래를 '새로 공개된 거래'로 "
            "셉니다.</p>" + "".join(parts) + site.coupang)
    site.write("today.html", title, desc, body, [("전국", "index.html"), ("오늘 공개 거래", None)])


COPY_JS = """<script>function kNav(b){var o=document.body.classList.toggle('nav-open');
var x=document.querySelector('.navbtn');if(x)x.setAttribute('aria-expanded',o?'true':'false');}
document.addEventListener('keydown',function(ev){if(ev.key==='Escape')document.body.classList.remove('nav-open');});document.addEventListener('click',function(ev){var b=ev.target.closest&&ev.target.closest('.copy');
if(!b)return;var t=b.getAttribute('data-copy');var done=function(){b.textContent='복사됨';setTimeout(function(){b.textContent='문장 복사'},1500)};
if(navigator.clipboard){navigator.clipboard.writeText(t).then(done,function(){})}else{var a=document.createElement('textarea');
a.value=t;document.body.appendChild(a);a.select();try{document.execCommand('copy');done()}catch(e){}a.remove()}});</script>"""

SEARCH_JS = """(function(){var d=document.getElementById('k-regions');if(!d)return;
Object.keys(window.K_REGIONS).forEach(function(k){var o=document.createElement('option');o.value=k;d.appendChild(o);});})();
function kGo(f){var q=(f.q.value||'').trim().replace(/\\s+/g,' ');var R=window.K_REGIONS||{};var hit=R[q];
if(!hit){var ks=Object.keys(R);for(var i=0;i<ks.length;i++){if(ks[i].replace(/ /g,'').indexOf(q.replace(/ /g,''))>=0){hit=R[ks[i]];break;}}}
if(hit){location.href=(window.K_UP||'')+hit;}else{f.q.value='';f.q.placeholder='찾는 지역이 없습니다';}return false;}"""


ABOUT = """<h1>기준 안내</h1>
<h2>무엇을 보여 주나요</h2>
<p>국토교통부가 공개하는 아파트 매매 실거래가 신고 자료를 매일 받아, 전국 시도·시군구마다 아파트값이 오르는지
내리는지와 그 이유를 정리합니다. 개인이 운영하는 비영리 성격의 참고용 사이트입니다.</p>
<h2>상승·보합·하락 판정</h2>
<ul><li>같은 단지·같은 전용면적 거래끼리만 비교한 <b>동일단지 가격지수</b>를 씁니다. 비싼 단지가 많이 팔린 달에
평균이 오르는 착시를 걷어 내기 위해서입니다.</li>
<li>최근 3개월 평균 지수가 직전 3개월보다 1% 넘게 오르면 상승, 1% 넘게 내리면 하락, 그 사이면 보합입니다.</li>
<li>거래가 적은 곳은 판단을 보류합니다.</li></ul>
<h2>실거래가 순위</h2>
<ul><li>최근 30일 계약 거래 중 단지별 가장 비싼 1건을 골라 순위를 매깁니다. 해제 거래와 직거래는 뺍니다.</li>
<li>실거래는 계약 후 30일 안에 신고되므로 최근 며칠 거래는 이후에 더 들어올 수 있습니다.</li></ul>
<h2>많이 오른 아파트</h2>
<ul><li>같은 단지·같은 면적의 최근 3개월 중위 거래가를 1년 전 같은 3개월 중위 거래가와 비교합니다.</li>
<li>두 시점 모두 2건 이상 거래된 곳만 셉니다. 거래가 적으면 층·향 차이로 변동률이 크게 나올 수 있습니다.</li></ul>
<h2>월별 통계</h2>
<ul><li>계약월 기준입니다. 거래량은 해제 거래를 뺀 전체 매매 신고 건수(직거래 포함), 중위 거래가·평당가는 직거래를
뺀 거래로 계산합니다.</li>
<li>계약 후 30일 안에 신고하므로 이번 달은 '집계 중', 신고기한이 남은 지난달은 '잠정'으로 표시합니다.</li>
<li>'기사 인용용' 문장은 표의 숫자를 그대로 문장으로 옮긴 것이며, 인용할 때 자료 출처와 집계일을 함께 적어 주세요.</li></ul>
<h2>오늘 공개된 거래</h2>
<ul><li>실거래가 자료에는 신고일이 없어, 매일 새벽 갱신 때 전날 갱신에 없던 거래(최근 2개월 계약분)를 새로 공개된
거래로 셉니다. 해제 거래는 뺍니다.</li></ul>
<h2>이 자료로 알 수 없는 것</h2>
<ul><li>호가·매물 수, 전세가, 동·층·향별 선호, 급매 여부</li><li>미래 가격. 이 자료는 과거 계약 기록입니다.</li></ul>
<h2>데이터 출처</h2>
<ul><li>실거래가: 국토교통부 아파트 매매 실거래가 상세 자료(공공데이터포털)</li>
<li>미분양·아파트 준공: 국토교통부, 인구 이동: 국가데이터처 (KOSIS 공유서비스)</li>
<li>행정구역 경계: 통계청 SGIS, vuski/admdongkor 가공 (CC BY 4.0)</li></ul>"""

GA_NOTE = """<h2>방문 통계</h2>
<ul><li>사이트 개선을 위해 Google 애널리틱스로 방문 수, 본 페이지, 유입 경로, 기기 종류 같은 통계를 수집합니다.
개인을 식별하는 정보는 수집하지 않으며, 운영자는 집계된 통계만 봅니다.</li>
<li>Google 애널리틱스는 쿠키를 사용하며, <a href="https://tools.google.com/dlpage/gaoptout">차단 부가기능</a>이나
브라우저의 쿠키 거부로 수집을 막을 수 있습니다.</li></ul>
"""

PRIVACY = """<h1>개인정보처리방침</h1>
<p>k-apts(이하 '사이트')는 이용자의 개인정보를 직접 수집하지 않습니다. 회원 가입, 로그인, 게시판, 문의 양식이 없습니다.</p>
<h2>쿠키와 광고</h2>
<ul><li>사이트에는 Google AdSense, 카카오 애드핏, 쿠팡 파트너스 광고가 표시될 수 있습니다.</li>
<li>Google을 포함한 제3자 광고 사업자는 쿠키를 사용해 이용자의 이 사이트 및 다른 사이트 방문 기록을 바탕으로
광고를 게재할 수 있습니다.</li>
<li>Google의 광고 쿠키 사용은 <a href="https://adssettings.google.com">광고 설정</a>에서 해제할 수 있고,
자세한 내용은 <a href="https://policies.google.com/technologies/ads">Google 광고 정책</a>에서 볼 수 있습니다.</li>
<li>쿠팡 파트너스 배너를 통해 구매가 이루어지면 사이트 운영자는 일정액의 수수료를 받습니다.</li>
<li>브라우저 설정에서 쿠키 저장을 거부할 수 있습니다.</li></ul>
<h2>접속 기록</h2>
<p>사이트는 GitHub Pages에서 제공되며, 호스팅 사업자가 보안과 운영을 위해 접속 기록(IP 주소 등)을 처리할 수 있습니다.</p>
<h2>문의</h2>
<p>사이트 관련 문의는 <a href="https://github.com/mapcityne/daegu-apt-dashboard/issues">GitHub 이슈</a>로 남겨 주세요.</p>
<p>시행일: 2026년 11월 23일</p>"""

if __name__ == "__main__":
    main()
