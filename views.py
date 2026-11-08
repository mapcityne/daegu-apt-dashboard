"""화면 블록. 페이지(app.py)들이 조합해 쓴다."""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import analysis as A
import briefing as B
import mapview as MV
import style as UI
from common import AREA_LABELS
from core import CHART, DATE, EOK, MAN, PCT, YEAR, Ctx, Data, eok, load_geo, md_list, name_col
from regions import SIDO_SHORT


# ───────────────────────── 지역 머리 ─────────────────────────
def region_header(D: Data, ctx: Ctx):
    s, j = ctx.s, ctx.s.get("판정", "판단 보류")
    if j == "판단 보류":
        why = "거래가 적어 방향을 판단하기 어렵습니다."
    else:
        why = f"최근 3개월 가격지수 {B.p(s.get('3개월'))}, 1년 {B.p(s.get('1년'))}."
        if s.get("상승비율") is not None:
            why += f" 1년 전보다 오른 단지가 {s['상승비율']:.0f}%입니다."
    UI.hero(ctx.name, ctx.parent_name, j, why,
            f"{D.ref:%Y년 %m월} 기준. 같은 단지·같은 면적 거래끼리 비교해, 최근 3개월이 직전 3개월보다 "
            "1% 넘게 오르면 상승, 1% 넘게 내리면 하락으로 봅니다.", value=s.get("3개월"))
    figures(s)


def figures(s, solo=False):
    UI.figures([
        ("3개월", B.p(s.get("3개월")), "가격지수 변동"),
        ("6개월", B.p(s.get("6개월")), "가격지수 변동"),
        ("1년", B.p(s.get("1년")), "가격지수 변동"),
        ("오른 단지", B.p(s.get("상승비율"), 0, False) if s.get("상승비율") is not None else "-",
         "1년 전보다 오른 단지 비율"),
        ("거래량", B.p(s.get("거래량증감"), 0), "전년 같은 3개월 대비"),
        ("신고가", B.p(s.get("신고가비율"), 1, False) if s.get("신고가비율") is not None else "-",
         "최근 3개월 거래 중 최고가 경신"),
    ], solo=solo)


def child_plates(ctx: Ctx, link_page="region", legend=True):
    if not len(ctx.children) or ctx.level == "시군구":
        return
    items = []
    for _, r in ctx.children.sort_values("3개월", ascending=False, na_position="last").iterrows():
        jj = r.get("판정") if isinstance(r.get("판정"), str) else "판단 보류"
        ch = r.get("3개월")
        items.append((r["이름"], jj, B.p(ch) if pd.notna(ch) else "", f"/{link_page}?r={r['코드']}"))
    UI.plates(items, f"{ctx.child_word}별 신호. 색은 판정, 숫자는 최근 3개월 가격지수 변동입니다. 누르면 그 지역으로 갑니다."
              if legend else "")


# ───────────────────────── 브리핑 ─────────────────────────
def briefing(D: Data, ctx: Ctx, compact=False):
    s = ctx.s
    UI.section("시장 전체")
    md_list(B.market_section(s, ctx.child_word))
    if len(ctx.children) and not compact:
        ctab, cnotes = B.children_section(ctx.children)
        if len(ctab):
            UI.section(f"{ctx.child_word}별 차이")
            show = ctab[["구분", "이름", "1년", "특징"]].rename(columns={"이름": ctx.child_word, "1년": "1년 변동"})
            st.dataframe(show.head(40), hide_index=True, width="stretch", column_config={
                "1년 변동": st.column_config.NumberColumn(format="%+.1f%%")})
            if cnotes:
                md_list(cnotes)
    UI.section("왜 오르고 내리나")
    note = A.drivers(s, ctx.parent)
    md_list(note) if note else st.markdown("- 뚜렷한 주도 요인이 보이지 않습니다.")
    lead = s.get("상승주도") or []
    if lead and not compact:
        st.markdown("상승 폭이 큰 단지(양쪽 시점 각 2건 이상 거래): " +
                    ", ".join(f"{x['단지']}({x['지역']}) {B.p(x['변동'])}" for x in lead[:5]))
    top = s.get("거래상위") or []
    if top and not compact:
        st.markdown("거래가 가장 많은 대장 단지(최근 12개월): " +
                    ", ".join(f"{x['단지']}({x['지역']}, {x['거래수']}건"
                              + (f", 1년 {B.p(x['변동'])}" if x.get("변동") is not None else "") + ")"
                              for x in top))
    ext = A.external_factors(s, SIDO_SHORT.get(ctx.sido) if ctx.level == "시군구" else None)
    if ext:
        st.markdown("**수요와 공급 (공공통계)**")
        md_list(ext)
    if compact:
        return
    for title, sec in [("신축과 구축", B.age_section(s)), ("면적별", B.area_section(s)),
                       ("집을 살 때 볼 숫자", B.buyer_section(s))]:
        if sec:
            UI.section(title)
            md_list(sec)


# ───────────────────────── 지도 ─────────────────────────
def region_map(D: Data, ctx: Ctx, key="map", height=None):
    if ctx.level == "시군구":
        c = ctx.children.dropna(subset=["1년"]).sort_values("1년")
        if len(c):
            UI.section("법정동별 1년 변동")
            fig = px.bar(c, x="1년", y="이름", orientation="h", color="1년",
                         color_continuous_scale=UI.DIVERGING, color_continuous_midpoint=0,
                         labels={"1년": "1년 변동(%)", "이름": ""})
            fig.update_layout(height=max(300, 22 * len(c)), coloraxis_showscale=False, **CHART)
            st.plotly_chart(fig, width="stretch")
        return
    mopt = {"1년 변동": "1년", "3개월 변동": "3개월", "오른 단지 비율": "상승비율",
            "거래량 증감": "거래량증감", "평당가": "중위평당가"}
    mname = st.radio("색 기준", list(mopt), horizontal=True, key=f"{key}_metric")
    mcol = mopt[mname]
    sg = D.stats[D.stats["단계"] == "시군구"].copy()
    sg["이름"] = sg["코드"].map(D.reg.set_index("sggcd")["지역명"])
    if ctx.level == "시도":
        sg = sg[sg["상위"] == ctx.sido]
    geo = load_geo()
    geo_sub = {"type": "FeatureCollection", "features": [f for f in geo["features"] if f["id"] in set(sg["코드"])]}
    diverging = mcol in ("1년", "3개월", "거래량증감")
    if diverging:
        rng = {"1년": 8, "3개월": 3, "거래량증감": 50}[mcol]
        vmin, vmax, scale = -rng, rng, UI.DIVERGING
    else:
        q = sg[mcol].dropna()
        vmin, vmax = (q.quantile(0.05), q.quantile(0.95)) if len(q) else (0, 1)
        scale = UI.SEQUENTIAL

    def f(v, fmt):
        return fmt.format(v) if pd.notna(v) else "-"

    hover = {r["코드"]: (f"<b>{r['이름']}</b> {B.ARROW.get(r['판정'], '')} {r['판정']}<br>"
                        f"1년 {f(r['1년'], '{:+.1f}%')}, 3개월 {f(r['3개월'], '{:+.1f}%')}<br>"
                        f"오른 단지 {f(r['상승비율'], '{:.0f}%')}, 거래량 {f(r['거래량증감'], '{:+.0f}%')}<br>"
                        f"평당 {f(r['중위평당가'], '{:,.0f}만원')}")
             for _, r in sg.iterrows()}
    fig = MV.choropleth(geo_sub, dict(zip(sg["코드"], sg[mcol])), hover, scale=scale, vmin=vmin, vmax=vmax,
                        title=mname, height=height or (700 if ctx.level == "전국" else 560))
    st.plotly_chart(fig, width="stretch", config={"scrollZoom": True, "displayModeBar": False}, key=key)
    st.caption("회색은 거래가 적어 판단을 보류한 곳입니다. 경계: 통계청 SGIS 행정구역(2026.10), "
               "vuski/admdongkor 가공(CC BY 4.0)")


# ───────────────────────── 가격지수 ─────────────────────────
def index_chart(D: Data, ctx: Ctx):
    UI.section("동일단지 가격지수")
    st.caption("같은 단지·같은 면적의 가격 변화만 모은 지수입니다. 수집 첫 달을 100으로 두고 3개월 평균으로 그립니다.")
    lines = [("전국", "00")]
    if ctx.level != "전국":
        lines.append((SIDO_SHORT[ctx.sido], ctx.sido))
    if ctx.level == "시군구":
        lines.append((ctx.title, ctx.sgg))
    if ctx.level != "시군구":
        pick = st.multiselect(f"함께 볼 {ctx.child_word}", ctx.children["이름"].tolist(), key="idx_pick")
        n2c = dict(zip(ctx.children["이름"], ctx.children["코드"]))
        lines += [(n, n2c[n]) for n in pick]
    rows = []
    for n, c in lines:
        x = D.index[D.index["코드"] == c].sort_values("계약월")
        if len(x):
            rows.append(pd.DataFrame({"계약월": x["계약월"], "지수": x["지수"].rolling(3, min_periods=1).mean(),
                                      "지역": n}))
    if not rows:
        st.info("거래가 적어 지수를 계산하지 못했습니다.")
        return
    fig = px.line(pd.concat(rows), x="계약월", y="지수", color="지역", labels={"계약월": ""})
    fig.add_hline(y=100, line_dash="dot", line_color="#B9BDB6")
    fig.update_layout(height=420, hovermode="x unified", **CHART)
    st.plotly_chart(fig, width="stretch")
    x = D.index[D.index["코드"] == ctx.code].sort_values("계약월")
    if len(x):
        UI.section(f"{ctx.title} 월별 거래량")
        st.caption("마지막 1~2개월은 신고 기한(계약 후 30일) 때문에 적게 잡힙니다.")
        fig2 = px.bar(x, x="계약월", y="거래수", labels={"계약월": "", "거래수": "거래(건)"})
        fig2.update_layout(height=220, **CHART)
        st.plotly_chart(fig2, width="stretch")


# ───────────────────────── 순위 ─────────────────────────
def rankings(D: Data, ctx: Ctx, base):
    basis = st.radio("비교 시점", ["1년 전", "6개월 전"], horizontal=True, key="rank_basis")
    colc = "1년전대비" if basis == "1년 전" else "6개월전대비"
    refc = "1년전" if basis == "1년 전" else "6개월전"
    if ctx.level == "전국":
        pc, tt = D.table("nat_risers.parquet"), D.table("nat_top_trades.parquet").head(10)
    elif base is not None:
        pc, tt = A.price_changes(base, D.ref), A.top_trades(A.window(base, D.ref, 0, span=12))
    else:
        return
    pc = pc[(pc["최근_건수"] >= 2) & (pc[f"{refc}_건수"] >= 2)].dropna(subset=[colc]).copy()
    pc["단지"] = name_col(pc)
    pc["지역"] = pc["시도코드"].map(SIDO_SHORT) + " " + pc["시군구"] + " " + pc["법정동"]
    st.caption(f"최근 {A.window_label(D.ref, 0)} 중위가와 {basis} 중위가를 같은 단지·같은 전용면적끼리 비교합니다. "
               f"양쪽 시점에 각 2건 이상 거래된 {len(pc):,}개가 대상입니다.")
    cfg = {"단지": st.column_config.TextColumn(width="medium"), "최근": EOK, refc: EOK,
           colc: st.column_config.NumberColumn("변동률", format="%+.1f%%"), "건축년도": YEAR}
    cols = ["순위", "단지", "지역", colc, refc, "최근", "건축년도"]
    a, b = st.columns(2)
    for box, ttl, sub in [(a, "많이 오른 단지 20곳", pc[pc[colc] > 0].nlargest(20, colc)),
                          (b, "많이 내린 단지 20곳", pc[pc[colc] < 0].nsmallest(20, colc))]:
        with box:
            UI.section(ttl)
            if sub.empty:
                st.info("이 조건에 맞는 단지가 없습니다.")
                continue
            sub = sub.assign(순위=range(1, len(sub) + 1))
            st.dataframe(sub[cols], hide_index=True, width="stretch", column_config=cfg,
                         height=min(740, 38 + 35 * len(sub)))
    UI.section("최근 12개월 최고가 거래 10건")
    st.caption("한 단지에서 가장 비싼 거래 1건씩만 셉니다.")
    tt = tt.copy()
    tt["지역"] = tt["시도코드"].map(SIDO_SHORT) + " " + tt["시군구"] + " " + tt["법정동"]
    tt.insert(0, "순위", range(1, len(tt) + 1))
    st.dataframe(tt[["순위", "단지명", "지역", "전용면적", "층", "거래금액_억", "평당가", "계약일자"]],
                 hide_index=True, width="stretch", column_config={
                     "거래금액_억": eok("거래가"), "평당가": MAN, "계약일자": DATE,
                     "전용면적": st.column_config.NumberColumn(format="%.1f㎡")})
    if base is not None:
        UI.section("평당가가 높은 단지 10곳")
        st.caption("최근 12개월 거래 3건 이상인 단지만 셉니다.")
        tc = A.top_complexes(A.window(base, D.ref, 0, span=12))
        tc.insert(0, "순위", range(1, len(tc) + 1))
        st.dataframe(tc[["순위", "단지명", "시군구", "법정동", "건축년도", "거래수", "중위평당가", "중위거래가",
                         "최고거래가"]], hide_index=True, width="stretch",
                     column_config={"중위평당가": MAN, "중위거래가": EOK, "최고거래가": EOK, "건축년도": YEAR})


def new_highs(D: Data, ctx: Ctx, base):
    UI.section(f"최근 3개월({A.window_label(D.ref, 0)}) 신고가")
    st.caption("같은 단지·같은 면적에서 수집기간 최고가를 넘어선 거래입니다(이전 거래 2건 이상).")
    if ctx.level == "전국":
        nh = D.table("nat_new_highs.parquet").copy()
    elif base is not None:
        nh = A.window(A.mark_new_highs(base), D.ref, 0)
        nh = nh[nh["신고가"]].copy()
    else:
        return
    if not len(nh):
        st.info("최근 3개월 신고가 거래가 없습니다.")
    else:
        nh["경신폭"] = (nh["거래금액"] / nh["직전최고가"] - 1) * 100
        nh["단지"] = name_col(nh)
        nh["지역"] = nh["시도코드"].map(SIDO_SHORT) + " " + nh["시군구"] + " " + nh["법정동"]
        nh["직전최고가_억"], nh["거래가_억"] = nh["직전최고가"] / 1e4, nh["거래금액"] / 1e4
        cnt = nh.groupby("지역").size().sort_values(ascending=False).head(8)
        st.markdown("신고가가 많은 곳: " + ", ".join(f"{g} {n}건" for g, n in cnt.items()))
        st.dataframe(nh.sort_values("계약일자", ascending=False)
                     [["계약일자", "단지", "지역", "층", "직전최고가_억", "거래가_억", "경신폭"]].head(500),
                     hide_index=True, width="stretch", height=380, column_config={
                         "계약일자": DATE, "직전최고가_억": eok("직전 최고가"), "거래가_억": eok("거래가"),
                         "경신폭": PCT, "단지": st.column_config.TextColumn(width="medium")})
    if base is None:
        return
    UI.section("최고가 대비 지금 가격")
    c1, c2 = st.columns([1, 3])
    lo = c1.slider("최고가 대비 범위(%)", 50, 110, (50, 90))
    c2.caption("수집기간 최고가와 최근 3개월 중위가를 비교합니다. 낮다고 저평가라는 뜻은 아닙니다. "
               "고점이 일시적 과열이었거나 입지·노후도 요인일 수 있습니다.")
    pk = A.peak_recovery(base, D.ref)
    pk = pk[pk["고점대비(%)"].between(*lo)].copy()
    pk["단지"] = name_col(pk)
    st.dataframe(pk.sort_values("고점대비(%)")
                 [["단지", "시군구", "법정동", "건축년도", "최고가", "최고가일", "최근중위", "최근건수", "고점대비(%)"]],
                 hide_index=True, width="stretch", height=420, column_config={
                     "최고가": EOK, "최근중위": eok("최근 중위"), "최고가일": DATE, "건축년도": YEAR,
                     "최근건수": st.column_config.NumberColumn("최근 건수"),
                     "고점대비(%)": st.column_config.ProgressColumn("최고가 대비", format="%.0f%%",
                                                                 min_value=0, max_value=110),
                     "단지": st.column_config.TextColumn(width="medium")})


# ───────────────────────── 예산 맞춤 ─────────────────────────
def budget(D: Data, ctx: Ctx, base):
    c1, c2, c3, c4 = st.columns(4)
    lo_hi = c1.slider("예산 (억원)", 0.5, 30.0, (3.0, 5.0), step=0.25)
    sp = c2.select_slider("시세 기준 기간", [3, 6, 12], value=6, format_func=lambda v: f"최근 {v}개월")
    min_year = c3.number_input("이 해 이후 준공", 1970, 2030, 2000, step=5)
    sort_by = c4.selectbox("정렬", ["1년 상승률 높은 순", "평당가 높은 순", "최근 거래 순", "거래 많은 순", "신축 순"])
    bs = A.budget_search(base, D.ref, lo_hi[0], lo_hi[1], months=sp)
    bs = bs[bs["건축년도"].astype(float).fillna(0) >= min_year]
    key_ = {"평당가 높은 순": "평당가", "1년 상승률 높은 순": "1년전대비", "최근 거래 순": "최근거래일",
            "거래 많은 순": "거래수", "신축 순": "건축년도"}[sort_by]
    bs = bs.sort_values(key_, ascending=False, na_position="last")
    bs["단지"] = name_col(bs)
    st.markdown(f"{lo_hi[0]:.2f}억에서 {lo_hi[1]:.2f}억 사이, {min_year}년 이후 준공한 **{len(bs):,}곳**"
                "(단지·면적 기준)이 있습니다.")
    if not bs.empty and ctx.level == "시도":
        by = bs.groupby("시군구").size().sort_values(ascending=False)
        fig = px.bar(x=by.index, y=by.values, labels={"x": "", "y": "후보 수"})
        fig.update_layout(height=220, **CHART)
        st.plotly_chart(fig, width="stretch")
    st.dataframe(bs[["단지", "시군구", "법정동", "건축년도", "중위거래가", "최근거래가", "최근거래일", "거래수",
                     "평당가", "1년전대비"]], hide_index=True, width="stretch", height=520,
                 column_config={"중위거래가": EOK, "최근거래가": EOK, "최근거래일": DATE, "평당가": MAN,
                                "1년전대비": st.column_config.NumberColumn("1년 변동", format="%+.1f%%"),
                                "건축년도": YEAR, "단지": st.column_config.TextColumn(width="medium")})
    st.caption("1년 변동은 같은 길이 기간의 1년 전 중위가와 비교한 값입니다. 취득세·중개보수 같은 부대비용은 "
               "빠져 있고, 실거래가는 계약 기준이라 호가·급매 여부는 알 수 없습니다.")


# ───────────────────────── 단지 상세 ─────────────────────────
def complex_detail(D: Data, ctx: Ctx, base, VAL):
    unit = "만원/3.3㎡" if VAL == "평당가" else "억원"
    cnt = (base.groupby("단지키").agg(단지명=("단지명", "last"), 시군구=("시군구", "last"),
                                    법정동=("법정동", "last"), n=("단지키", "size"))
           .sort_values("n", ascending=False))
    labels = dict(zip(cnt["단지명"] + ", " + cnt["시군구"] + " " + cnt["법정동"], cnt.index))
    pick = st.selectbox("단지 이름으로 찾기", list(labels), key=f"cx_{ctx.code}")
    cx = base[base["단지키"] == labels[pick]]
    pcx, pkx = A.price_changes(cx, D.ref), A.peak_recovery(cx, D.ref)
    info = cx.iloc[-1]
    UI.figures([
        ("준공", f"{int(info['건축년도'])}년" if pd.notna(info["건축년도"]) else "-", ""),
        ("거래", f"{len(cx)}건", "수집기간 전체"),
        ("1년 변동", B.p(pcx["1년전대비"].median()) if pcx["1년전대비"].notna().any() else "-", "면적별 중위"),
    ])
    c1, c2 = st.columns([1.4, 1])
    with c1:
        fig = px.scatter(cx, x="계약일자", y=VAL, color="평형대", category_orders={"평형대": AREA_LABELS},
                         hover_data={"전용면적": ":.1f", "층": True, "거래금액_억": ":.2f"},
                         labels={VAL: unit, "계약일자": ""})
        g = cx.groupby("계약월")[VAL].median().rolling(3, min_periods=1).mean()
        fig.add_trace(go.Scatter(x=g.index, y=g.values, mode="lines", name="월 중위",
                                 line=dict(color=UI.MUTED, dash="dot")))
        fig.update_layout(height=340, **CHART)
        st.plotly_chart(fig, width="stretch")
    with c2:
        UI.section("면적별 시세")
        t = (pcx[["면적타입", "최근", "6개월전", "1년전", "1년전대비"]]
             .merge(pkx[["면적타입", "최고가", "고점대비(%)"]], on="면적타입", how="outer").sort_values("면적타입"))
        st.dataframe(t, hide_index=True, width="stretch", column_config={
            "면적타입": st.column_config.NumberColumn("전용㎡"), "최근": EOK, "6개월전": EOK, "1년전": EOK,
            "최고가": EOK, "1년전대비": PCT, "고점대비(%)": st.column_config.NumberColumn("최고가 대비", format="%.0f%%")})
        st.caption("최근·6개월 전·1년 전은 각각 3개월 중위 거래가입니다.")
    UI.section("최근 거래")
    st.dataframe(cx.sort_values("계약일자", ascending=False)
                 [["계약일자", "전용면적", "층", "거래금액_억", "평당가", "거래유형"]].head(30),
                 hide_index=True, width="stretch", column_config={
                     "계약일자": DATE, "거래금액_억": eok("거래가"), "평당가": MAN,
                     "전용면적": st.column_config.NumberColumn(format="%.2f㎡")})


# ───────────────────────── 바닥글 ─────────────────────────
def footer(D: Data):
    m = D.meta
    st.markdown(
        f"<div class='k-footer'><div class='k-brand'>k-apts</div>"
        f"<p>{D.ref:%Y년 %m월} 기준, 전국 아파트 매매 실거래 {m['거래수']:,}건을 매일 새벽 새로 받아 분석합니다. "
        f"마지막 갱신 {m['생성'][:16].replace('T', ' ')}.</p>"
        "<p>실거래가: 국토교통부(공공데이터포털). 미분양·주택 준공: 국토교통부, 인구 이동: 국가데이터처(KOSIS). "
        "행정구역 경계: 통계청 SGIS, vuski/admdongkor(CC BY 4.0).</p>"
        "<p>이 사이트는 참고 자료이며 투자 판단의 근거로 정확성을 보증하지 않습니다.</p></div>",
        unsafe_allow_html=True)
