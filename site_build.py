"""정적 사이트 생성 (광고 게재용, GitHub Pages).

data/out 집계 결과로 전국·시도·시군구 페이지를 HTML로 만든다. 매일 데이터 갱신 뒤 실행.
    python site_build.py [--out site] [--base-url https://k-apts.kr]

광고 설정은 ads.py 참고 (환경변수). 대화형 분석은 Streamlit 앱으로 연결한다.
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
from common import OUT_DIR, enrich
from regions import SIDO_SHORT, regions

BASE = Path(__file__).resolve().parent
APP_URL = "https://k-apts.streamlit.app"
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


def gauge(value, span=4.0) -> str:
    mark = ""
    if value is not None and value == value:
        v = max(min(float(value), span), -span)
        if v <= -1:
            pos = (v + span) / (span - 1) * 3 / 7
        elif v >= 1:
            pos = 4 / 7 + (v - 1) / (span - 1) * 3 / 7
        else:
            pos = 3 / 7 + (v + 1) / 2 / 7
        mark = f'<div class="mark" style="left:{pos * 100:.2f}%"><b>최근 3개월 {value:+.1f}%</b></div>'
    return (f'<div class="gauge"><div class="track"><span></span><span></span><span></span>{mark}</div>'
            f'<div class="ticks"><span>하락</span><span>보합</span><span>상승</span></div></div>')


def figures(s) -> str:
    items = [("3개월", pct(s.get("3개월")), "가격지수 변동"), ("6개월", pct(s.get("6개월")), "가격지수 변동"),
             ("1년", pct(s.get("1년")), "가격지수 변동"),
             ("오른 단지", pct(s.get("상승비율"), 0, False) if s.get("상승비율") is not None else "-",
              "1년 전보다 오른 단지 비율"),
             ("거래량", pct(s.get("거래량증감"), 0), "전년 같은 3개월 대비"),
             ("신고가", pct(s.get("신고가비율"), 1, False) if s.get("신고가비율") is not None else "-",
              "최근 3개월 거래 중")]
    return '<div class="figs">' + "".join(
        f'<div class="fig"><div class="lab">{a}</div><div class="num {sign_cls(b)}">{b}</div>'
        f'<div class="sub">{c}</div></div>' for a, b, c in items) + "</div>"


def badge(j):
    return f'<span class="badge v-{VKEY.get(j, "hold")}">{ARROW.get(j, "")} {j}</span>'


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


# ───────────────────────── 페이지 틀 ─────────────────────────
class Site:
    def __init__(self, out: Path, base_url: str):
        self.out, self.base_url = out, base_url.rstrip("/")
        self.pages = []
        g = lambda k: ads.conf(k)  # noqa: E731
        self.head_ads = ads.adsense_head(g("ADSENSE_CLIENT"))
        self.adfit = ads.adfit(g("ADFIT_UNIT_PC"), g("ADFIT_UNIT_MO"))
        self.coupang = ads.coupang_block(g("COUPANG_ID"), g("COUPANG_TRACKING"), g("COUPANG_TEMPLATE"))
        self.ads_txt = ads.ads_txt(g("ADSENSE_CLIENT"))

    def write(self, path: str, title: str, desc: str, body: str, crumbs=None):
        depth = path.count("/")
        up = "../" * depth
        crumb = ""
        if crumbs:
            crumb = '<nav class="crumb">' + " › ".join(
                f'<a href="{up}{h}">{e(t)}</a>' if h else e(t) for t, h in crumbs) + "</nav>"
        doc = f"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title><meta name="description" content="{e(desc)}">
<link rel="canonical" href="{self.base_url}/{path}">
<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}">
<meta property="og:type" content="website"><meta property="og:url" content="{self.base_url}/{path}">
<link rel="icon" href="{up}mark.svg" type="image/svg+xml">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
<link rel="stylesheet" href="{up}style.css">
{self.head_ads}
</head><body>
<header class="top"><div class="wrap">
<a class="logo" href="{up}index.html">{MARK}<span>k-apts</span></a>
<form class="search" role="search" onsubmit="return kGo(this)"><input name="q" list="k-regions" autocomplete="off"
 placeholder="지역 검색 (예: 대구 수성구)" aria-label="지역 검색"><button>검색</button></form>
</div><nav class="menu wrap"><a href="{up}index.html">전국</a><a href="{up}r/11.html">서울</a><a href="{up}r/41.html">경기</a>
<a href="{up}r/27.html">대구</a><a href="{up}r/26.html">부산</a><a href="{up}about.html">기준 안내</a>
<a class="app" href="{APP_URL}">자세히 분석하기</a></nav></header>
<main class="wrap">{crumb}{body}</main>
<footer class="foot"><div class="wrap">
<div class="brand">{MARK}<span>k-apts</span></div>
<p>국토교통부 아파트 매매 실거래가를 매일 새벽 받아 전국 시군구의 가격 흐름을 계산합니다.</p>
<p>실거래가: 국토교통부(공공데이터포털). 미분양·주택 준공: 국토교통부, 인구 이동: 국가데이터처(KOSIS).
행정구역 경계: 통계청 SGIS, vuski/admdongkor(CC BY 4.0).</p>
<p>이 사이트는 참고 자료이며 투자 판단의 근거로 정확성을 보증하지 않습니다.</p>
<p class="links"><a href="{up}about.html">기준 안내</a><a href="{up}privacy.html">개인정보처리방침</a>
<a href="{REPO_URL}/issues">문의</a><a href="{APP_URL}">대화형 분석 앱</a></p>
</div></footer>
<datalist id="k-regions"></datalist><script>window.K_UP="{up}"</script><script src="{up}regions.js" defer></script>
</body></html>"""
        f = self.out / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(doc, encoding="utf-8")
        self.pages.append(path)

    def ad(self):
        return self.adfit

    def finish(self):
        urls = "".join(f"<url><loc>{self.base_url}/{p}</loc></url>" for p in self.pages)
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
    t = pd.read_parquet(OUT_DIR / f"trades_{sido}.parquet")
    t = A.prepare(enrich(t))
    t = t[~t["해제"] & (t["거래유형"] != "직거래")]
    last = pd.Timestamp(t["계약일자"].max())
    recent = t[t["계약일자"] > last - pd.Timedelta(days=30)]
    pc = A.price_changes(t[t["계약월"] <= ref], ref)
    pc = pc[(pc["최근_건수"] >= 2) & (pc["1년전_건수"] >= 2)].dropna(subset=["1년전대비"]).copy()
    pc["구간"] = pd.cut(pc["1년전대비"], [10, 20, 30, np.inf], right=False,
                      labels=["10~20%", "20~30%", "30% 이상"]).astype(str)
    return recent, pc, last


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
    out = [f'<h2>1년 전보다 많이 오른 아파트</h2><p class="sub">{e(label)}에서 1년 전과 비교할 수 있는 '
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
            f'{badge(j)}</div><p class="why">{why}</p>{gauge(s.get("3개월")) if j != "판단 보류" else ""}'
            f'{figures(s)}</section>')


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
    stamp = f"{ref:%Y년 %m월} 기준 · 갱신 {meta['생성'][:10]}"
    nat = row("00")

    sido_rows, sido_first = [], {}
    for sido in SIDO_SHORT:
        if not (OUT_DIR / f"trades_{sido}.parquet").exists():
            continue
        recent, pc, last = load_sido(sido, ref)
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
            body = [hero(rg["sggnm"], short, ss), f'<p class="stamp">{stamp}</p>', site.ad()]
            if len(r_sgg):
                tp = top10(r_sgg)
                firsts.append((code, rg["sggnm"], tp.iloc[0], len(r_sgg)))
                body.append(f"<h2>최근 한 달 실거래가 순위</h2><p class='sub'>계약일 "
                            f"{last - pd.Timedelta(days=29):%Y.%m.%d}~{last:%Y.%m.%d}, 거래 {len(r_sgg):,}건 중 "
                            "단지별 가장 비싼 거래 10곳입니다(직거래 제외).</p>" + rank_table(tp))
            body.append(band_section(p_sgg, name, True))
            body.append(f"<h2>{e(rg['sggnm'])} 시장 브리핑</h2><div class='card brief'>"
                        + briefing_html(ss, row(sido), "법정동", short) + "</div>")
            body.append(sibling_section(short, code, sib))
            body.append(site.coupang)
            body.append(f'<p class="more"><a class="btn" href="{APP_URL}/region?r={code}">'
                        f"{e(name)} 단지별 거래 흐름 자세히 보기</a></p>")
            site.write(f"r/{code}.html", f"{name} 아파트 실거래가 순위와 시세 흐름 | k-apts",
                       f"{name} 아파트 최근 실거래가 순위 10, 1년 전보다 10·20·30% 오른 아파트, 가격 흐름 판정.",
                       "".join(body), [("전국", "index.html"), (short, f"r/{sido}.html"), (rg["sggnm"], None)])
        # ── 시도 페이지
        firsts.sort(key=lambda x: -x[2]["거래금액"])
        body = [hero(short, None, s), f'<p class="stamp">{stamp}</p>', site.ad()]
        if firsts:
            rows = [(f'<a href="{c}.html">{e(n)}</a>', e(t.단지명), f"<b>{eok(t.거래금액_억)}</b>",
                     f"{t.전용면적:.0f}㎡", f"{t.계약일자:%m.%d}", f"{k:,}건") for c, n, t, k in firsts]
            body.append("<h2>최근 한 달 시군구별 1위 거래</h2><p class='sub'>시군구 이름을 누르면 10위까지 볼 수 있습니다."
                        "</p>" + table(["시군구", "단지", "거래가", "전용", "계약일", "한 달 거래"], rows))
            sido_first[sido] = firsts[0]
        sg = stats[(stats["단계"] == "시군구") & (stats["상위"] == sido)].copy()
        sg["nm"] = sg["코드"].map(sname)
        sg = sg.sort_values("3개월", ascending=False, na_position="last")
        body.append("<h2>시군구별 가격 흐름</h2><p class='sub'>같은 단지끼리 비교한 가격지수입니다. 최근 3개월 "
                    "상승 폭이 큰 순서이고, 누르면 그 지역 순위와 브리핑을 볼 수 있습니다.</p><div class='rgrid'>"
                    + "".join(region_card(f"{r['코드']}.html", r["nm"], B.row_dict(r)) for _, r in sg.iterrows())
                    + "</div>")
        body.append(band_section(pc, short, False))
        body.append(f"<h2>{e(short)} 시장 브리핑</h2><div class='card brief'>" + briefing_html(s, nat, "시군구", None)
                    + "</div>")
        body.append(site.coupang)
        site.write(f"r/{sido}.html", f"{short} 아파트 실거래가 순위와 시군구별 시세 | k-apts",
                   f"{short} 시군구별 아파트 실거래가 1위, 많이 오른 아파트, 가격 흐름 판정.",
                   "".join(body), [("전국", "index.html"), (short, None)])
        sido_rows.append((sido, short, s))
        print(f"{short}: 시군구 {len(subs)}쪽", flush=True)

    # ── 첫 화면
    j = nat.get("판정", "판단 보류")
    word = {"상승": "오르는 중", "하락": "내리는 중", "보합": "숨 고르는 중"}.get(j, "판단 보류")
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
    body = [f'<section class="home"><span class="pill">국토교통부 실거래가 기반 · 매일 새벽 갱신</span>'
            f'<h1>전국 아파트값,<br>지금은 <em>{word}</em></h1><p class="lead">{ref:%Y년 %m월} 기준, '
            f"같은 단지끼리 비교한 가격지수는 최근 3개월 {pct(nat.get('3개월'))}, 1년 {pct(nat.get('1년'))}입니다. "
            "지역마다 오르는지 내리는지, 어디서 얼마에 거래됐는지 정리해 드립니다.</p>"
            '<div class="cta"><a class="btn" href="r/27.html">대구 실거래가 순위 보기</a>'
            '<a class="btn ghost" href="about.html">숫자 읽는 법</a></div>'
            f'<div class="stats"><div><b>{meta["거래수"] / 1e4:,.0f}만 건</b><span>분석한 실거래</span></div>'
            f'<div><b>{n_sgg}곳</b><span>전국 시군구</span></div><div><b>매일</b><span>새벽 자동 갱신</span></div>'
            "</div></section>",
            f'<section class="card signal"><div class="signal-head"><h2>전국 시장 신호</h2>{badge(j)}</div>'
            f"{gauge(nat.get('3개월'))}{figures(nat)}</section>", site.ad()]

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
               "전국 시군구 아파트 실거래가 순위, 1년 전보다 많이 오른 아파트, 지역별 상승·하락 판정을 매일 갱신합니다.",
               "".join(body))

    site.write("about.html", "기준 안내 | k-apts", "k-apts가 숫자를 만드는 방법과 데이터 출처.", ABOUT,
               [("전국", "index.html"), ("기준 안내", None)])
    site.write("privacy.html", "개인정보처리방침 | k-apts", "k-apts 개인정보처리방침.", PRIVACY,
               [("전국", "index.html"), ("개인정보처리방침", None)])
    site.write("404.html", "페이지를 찾을 수 없습니다 | k-apts", "", '<h1>페이지를 찾을 수 없습니다</h1>'
               '<p><a href="index.html">전국 첫 화면으로 가기</a></p>')
    site.pages.remove("404.html")
    site.finish()
    print(f"완료: {len(site.pages)}쪽 → {out}")


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
<h2>이 자료로 알 수 없는 것</h2>
<ul><li>호가·매물 수, 전세가, 동·층·향별 선호, 급매 여부</li><li>미래 가격. 이 자료는 과거 계약 기록입니다.</li></ul>
<h2>데이터 출처</h2>
<ul><li>실거래가: 국토교통부 아파트 매매 실거래가 상세 자료(공공데이터포털)</li>
<li>미분양·아파트 준공: 국토교통부, 인구 이동: 국가데이터처 (KOSIS 공유서비스)</li>
<li>행정구역 경계: 통계청 SGIS, vuski/admdongkor 가공 (CC BY 4.0)</li></ul>"""

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
