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
    return f'<span class="badge" style="background:{VCOLOR.get(j, "#8C96A3")}">{ARROW.get(j, "")} {j}</span>'


def chip(j):
    return f'<span class="chip" style="background:{VCOLOR.get(j, "#8C96A3")}"></span>'


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
<link rel="icon" href="{up}logo-mark.png">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{up}style.css">
{self.head_ads}
</head><body>
<header class="top"><div class="wrap">
<a class="logo" href="{up}index.html"><img src="{up}logo.png" alt="k-apts" height="30"></a>
<nav class="menu"><a href="{up}index.html">전국</a><a href="{up}r/27.html">대구</a><a href="{up}r/11.html">서울</a>
<a href="{up}about.html">기준 안내</a><a class="app" href="{APP_URL}">자세히 분석</a></nav>
</div></header>
<main class="wrap">{crumb}{body}</main>
<footer class="foot"><div class="wrap">
<div class="brand">k-apts</div>
<p>국토교통부 아파트 매매 실거래가를 매일 새벽 받아 전국 시군구의 가격 흐름을 계산합니다.</p>
<p>실거래가: 국토교통부(공공데이터포털). 미분양·주택 준공: 국토교통부, 인구 이동: 국가데이터처(KOSIS).
행정구역 경계: 통계청 SGIS, vuski/admdongkor(CC BY 4.0).</p>
<p>이 사이트는 참고 자료이며 투자 판단의 근거로 정확성을 보증하지 않습니다.</p>
<p><a href="{up}about.html">기준 안내</a> · <a href="{up}privacy.html">개인정보처리방침</a> ·
<a href="{REPO_URL}/issues">문의</a> · <a href="{APP_URL}">대화형 분석 앱</a></p>
</div></footer>
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
           f'{len(pc):,}곳(같은 단지·같은 면적, 두 시점 각 2건 이상) 중 10~20% 오른 곳 <b>{n10}곳</b>, '
           f'20~30% <b>{n20}곳</b>, 30% 이상 <b>{n30}곳</b>입니다. 최근 3개월과 1년 전 같은 3개월의 '
           "중위 거래가를 비교했습니다.</p>"]
    if not sgg_level and pc["시군구"].nunique() > 1:
        ct = pd.crosstab(pc["시군구"], pc["구간"]).reindex(columns=["30% 이상", "20~30%", "10~20%"], fill_value=0)
        ct["비교"] = pc.groupby("시군구").size()
        ct = ct.sort_values(["30% 이상", "20~30%", "10~20%"], ascending=False)
        out.append(table(["시군구", "30% 이상", "20~30%", "10~20%", "비교한 곳"],
                         [(e(i), *map(int, r.values)) for i, r in ct.iterrows()]))
    for b, n in [("30% 이상", None), ("20~30%", None), ("10~20%", 30)]:
        sub = pc[pc["구간"] == b]
        if len(sub):
            more = f" (상위 {n}곳)" if n and len(sub) > n else ""
            out.append(f"<h3>{b} 오른 곳{more}</h3>" + riser_table(sub, n))
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
    p = f'<div class="parent">{e(parent_name)}</div>' if parent_name else ""
    return (f'<section class="hero"><div class="hero-top"><div>{p}<h1>{e(name)} 아파트 실거래가</h1></div>'
            f'{badge(j)}</div>{gauge(s.get("3개월")) if j != "판단 보류" else ""}<p class="why">{why}</p></section>'
            + figures(s))


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

    meta = json.loads((OUT_DIR / "meta.json").read_text(encoding="utf-8"))
    ref = pd.Timestamp(meta["기준월"] + "-01")
    stats = pd.read_parquet(OUT_DIR / "stats.parquet")
    S = stats.set_index("코드")
    row = lambda c: B.row_dict(S.loc[c]) if c in S.index else {"판정": "판단 보류"}  # noqa: E731
    reg = regions()
    sname = reg.set_index("sggcd")["sggnm"]
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
            body.append(f"<h2>{e(rg['sggnm'])} 시장 브리핑</h2>" + briefing_html(ss, row(sido), "법정동", short))
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
        body.append("<h2>시군구별 가격 흐름</h2><p class='sub'>같은 단지끼리 비교한 가격지수입니다.</p>" + table(
            ["시군구", "판정", "3개월", "1년", "오른 단지"],
            [(f'<a href="{r["코드"]}.html">{e(r["nm"])}</a>', chip(r["판정"]) + e(str(r["판정"])),
              f'<span class="{sign_cls(pct(r["3개월"]))}">{pct(r["3개월"])}</span>',
              f'<span class="{sign_cls(pct(r["1년"]))}">{pct(r["1년"])}</span>',
              pct(r["상승비율"], 0, False) if pd.notna(r["상승비율"]) else "-") for _, r in sg.iterrows()]))
        body.append(band_section(pc, short, False))
        body.append(f"<h2>{e(short)} 시장 브리핑</h2>" + briefing_html(s, nat, "시군구", None))
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

    body = [f'<section class="home"><h1>전국 아파트값, 지금은 {word}</h1><p class="lead">{ref:%Y년 %m월} 기준, '
            f"같은 단지끼리 비교한 가격지수는 최근 3개월 {pct(nat.get('3개월'))}, 1년 {pct(nat.get('1년'))}입니다. "
            f"전국 {meta['거래수']:,}건의 실거래로 지역마다 오르는지 내리는지를 매일 계산합니다.</p>"
            f"{gauge(nat.get('3개월'))}</section>", figures(nat), site.ad()]
    body.append("<h2>시도별 가격 흐름과 최근 한 달 1위 거래</h2>" + table(
        ["시도", "판정", "3개월", "1년", "한 달 최고가"],
        [(f'<a href="r/{c}.html">{e(n)}</a>', chip(s.get("판정")) + e(str(s.get("판정"))),
          f'<span class="{sign_cls(pct(s.get("3개월")))}">{pct(s.get("3개월"))}</span>',
          f'<span class="{sign_cls(pct(s.get("1년")))}">{pct(s.get("1년"))}</span>',
          (f"{e(sido_first[c][1])} {e(sido_first[c][2].단지명)} <b>{eok(sido_first[c][2].거래금액_억)}</b>"
           if c in sido_first else "-")) for c, n, s in sido_rows]))
    body.append('<div class="two"><div><h2>지금 오르는 곳</h2><p class="sub">최근 3개월 가격지수가 1% 넘게 오른 '
                f'시군구 중 상승 폭이 큰 순서입니다.</p>{plates(up)}</div><div><h2>지금 내리는 곳</h2>'
                f'<p class="sub">1% 넘게 내린 시군구 중 하락 폭이 큰 순서입니다.</p>{plates(dn)}</div></div>')
    body.append("<h2>이번 달 전국 시장 이야기</h2>" + briefing_html(nat, None, "시도", None))
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
