"""시장 분석 함수 모음 (Streamlit과 분리해 단독 테스트 가능).

가격 비교 원칙
- 단지·평형이 섞이면 '비싼 단지가 많이 팔렸다'는 구성 변화가 가격 변화처럼 보인다.
  그래서 변동률은 **같은 단지 · 같은 전용면적(㎡ 반올림)** 끼리만 비교한다.
- 한 달 거래는 적으므로 3개월 창의 중위가를 쓴다.
    최근     = 기준월 포함 직전 3개월
    6개월 전 = 그보다 6개월 앞선 3개월
    1년 전   = 그보다 12개월 앞선 3개월
- 거래량은 신고 기한(계약 후 30일) 때문에 기준월이 덜 잡히므로, 기준월 직전 3개월로 비교한다.
"""
import numpy as np
import pandas as pd

AGE_BINS = [-1, 5, 10, 20, 30, 200]
AGE_LABELS = ["5년 이내", "6~10년", "11~20년", "21~30년", "30년 초과"]
PRICE_BINS = [0, 3, 5, 7, 10, 1_000]
PRICE_LABELS = ["3억 이하", "3~5억", "5~7억", "7~10억", "10억 초과"]
INFO_COLS = ["단지키", "단지명", "시도코드", "시군구코드", "시군구", "법정동", "면적타입", "건축년도"]

SIGNAL_UP, SIGNAL_DOWN = 1.0, -1.0  # 3개월 지수 변동 판정 기준(%)


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """분석용 파생 컬럼 추가 (common.enrich 이후)."""
    df = df.copy()
    df["면적타입"] = df["전용면적"].round().astype(int)
    df["평형키"] = df["단지키"] + "#" + df["면적타입"].astype(str)
    df["연식"] = (df["계약일자"].dt.year - df["건축년도"].astype("float")).clip(lower=0)
    df["연식구간"] = pd.cut(df["연식"], AGE_BINS, labels=AGE_LABELS)
    df["가격대"] = pd.cut(df["거래금액_억"], PRICE_BINS, labels=PRICE_LABELS, right=True)
    return df


def analysis_base(df: pd.DataFrame, excl_direct=True) -> pd.DataFrame:
    """해제 거래 제외, (선택) 직거래 제외."""
    d = df[~df["해제"]]
    if excl_direct:
        d = d[d["거래유형"] != "직거래"]
    return d


def ref_month(df: pd.DataFrame) -> pd.Timestamp:
    """기준월: 마지막 달이 월초 며칠분뿐이면 직전 달."""
    last = df["계약일자"].max()
    m = last.to_period("M").to_timestamp()
    return m - pd.DateOffset(months=1) if last.day < 25 else m


def window(df, ref: pd.Timestamp, back: int, span: int = 3):
    """ref(월초)에서 back개월 이전을 끝으로 하는 span개월 창."""
    end = ref - pd.DateOffset(months=back)
    start = end - pd.DateOffset(months=span - 1)
    return df[(df["계약월"] >= start) & (df["계약월"] <= end)]


def window_label(ref, back, span=3):
    end = ref - pd.DateOffset(months=back)
    start = end - pd.DateOffset(months=span - 1)
    return f"{start:%y.%m}–{end:%y.%m}"


# ───────────────────────── 신고가 ─────────────────────────
def mark_new_highs(df: pd.DataFrame) -> pd.DataFrame:
    """같은 단지·면적에서 이전 모든 거래의 최고가를 넘어선 거래 = 신고가.
    수집 기간 안에서의 최고가이며, 이전 거래가 2건 이상 있을 때만 판단한다."""
    d = df.sort_values("계약일자").copy()
    prev = d.groupby("평형키")["거래금액"].shift()
    d["직전최고가"] = prev.groupby(d["평형키"]).cummax()
    d["이전거래수"] = d.groupby("평형키").cumcount()
    d["신고가"] = (d["이전거래수"] >= 2) & (d["거래금액"] > d["직전최고가"])
    return d


# ───────────────────────── 동일 단지·면적 변동률 ─────────────────────────
def price_changes(df: pd.DataFrame, ref: pd.Timestamp, min_n: int = 1) -> pd.DataFrame:
    """평형키별 최근/6개월 전/1년 전 3개월 창 중위 거래가와 변동률."""
    def med(w, name):
        return w.groupby("평형키").agg(**{f"{name}": ("거래금액_억", "median"),
                                         f"{name}_건수": ("거래금액_억", "size")})

    now = med(window(df, ref, 0), "최근")
    m6 = med(window(df, ref, 6), "6개월전")
    m12 = med(window(df, ref, 12), "1년전")
    t = now.join(m6, how="left").join(m12, how="left")
    t = t[t["최근_건수"] >= min_n]
    for c in ["6개월전", "1년전"]:
        ok = t[f"{c}_건수"] >= min_n
        t[c] = t[c].where(ok)
        t[f"{c}대비"] = (t["최근"] / t[c] - 1) * 100
    cols = [c for c in INFO_COLS if c in df.columns]
    info = df.sort_values("계약일자").groupby("평형키")[cols].last()
    return t.join(info).reset_index()


# ───────────────────────── 동일단지 가격지수 ─────────────────────────
def price_index(df: pd.DataFrame, min_month_n: int = 20, iters: int = 25) -> pd.DataFrame:
    """월별 동일단지 가격지수 (첫 달 = 100). 반환: index=월, 컬럼 지수·거래수.

    log(거래가) = 평형키 고정효과 + 월 효과 로 두고 교대 평균으로 추정한다.
    같은 단지·면적의 시점 간 가격 차이만 지수에 반영되므로
    '비싼 단지가 많이 팔린 달'에 지수가 튀지 않는다.
    """
    d = df[["평형키", "계약월", "거래금액"]]
    cnt = d.groupby("계약월").size()
    ok_months = cnt[cnt >= min_month_n].index
    d = d[d["계약월"].isin(ok_months)]
    d = d[d.groupby("평형키")["평형키"].transform("size") >= 2]
    if d["계약월"].nunique() < 2:
        return pd.DataFrame(columns=["지수", "거래수"])
    lp = np.log(d["거래금액"].to_numpy(dtype=float))
    mcode, months = pd.factorize(d["계약월"], sort=True)
    kcode, _ = pd.factorize(d["평형키"])
    m_eff = np.zeros(len(months))
    nk = np.bincount(kcode)
    nm = np.bincount(mcode, minlength=len(months))
    for _ in range(iters):
        fe = np.bincount(kcode, lp - m_eff[mcode]) / nk
        m_eff = np.bincount(mcode, lp - fe[kcode], minlength=len(months)) / np.maximum(nm, 1)
    idx = np.exp(m_eff - m_eff[0]) * 100
    return pd.DataFrame({"지수": idx, "거래수": cnt.reindex(months).to_numpy()},
                        index=pd.DatetimeIndex(months, name="계약월"))


def index_change(idx: pd.Series, ref: pd.Timestamp, back: int) -> float:
    """최근 3개월 평균 지수 vs back개월 앞선 3개월 평균 (%)."""
    if len(idx) == 0 or not isinstance(idx.index, pd.DatetimeIndex):
        return np.nan
    def avg(b):
        end = ref - pd.DateOffset(months=b)
        s = idx[(idx.index <= end) & (idx.index > end - pd.DateOffset(months=3))]
        return s.mean() if len(s) else np.nan
    a, b = avg(0), avg(back)
    return (a / b - 1) * 100 if pd.notna(a) and pd.notna(b) else np.nan


def verdict(ch3: float) -> str:
    if pd.isna(ch3):
        return "판단 보류"
    return "상승" if ch3 >= SIGNAL_UP else "하락" if ch3 <= SIGNAL_DOWN else "보합"


# ───────────────────────── 지역 통계 (브리핑 재료) ─────────────────────────
def _median_by(pc, col, by, order=None, min_count=10):
    g = pc.groupby(by, observed=True)[col].agg(["median", "count"])
    g = g[g["count"] >= min_count]
    if order is not None:
        g = g.reindex([o for o in order if o in g.index])
    return {str(k): (round(float(v["median"]), 1), int(v["count"])) for k, v in g.iterrows()}


def region_stats(base, raw, pc, idx, ref) -> dict:
    """한 지역의 브리핑 수치. base=분석 대상 거래, raw=해제 포함 원거래,
    pc=price_changes(base), idx=price_index(base), ref=기준월."""
    s = {}
    si = idx["지수"] if len(idx) else pd.Series(dtype=float)
    s["3개월"] = index_change(si, ref, 3)
    s["6개월"] = index_change(si, ref, 6)
    s["1년"] = index_change(si, ref, 12)
    s["판정"] = verdict(s["3개월"])

    now = window(base, ref, 0)
    s["거래수_3개월"] = int(len(now))
    v_now, v_ago = len(window(base, ref, 1)), len(window(base, ref, 13))
    s["거래량증감"] = (v_now / v_ago - 1) * 100 if v_ago >= 10 else np.nan
    s["중위거래가"] = float(now["거래금액_억"].median()) if len(now) else np.nan
    s["중위평당가"] = float(now["평당가"].median()) if len(now) else np.nan
    ago = window(base, ref, 12)
    s["중위평당가_1년전"] = float(ago["평당가"].median()) if len(ago) else np.nan

    p12 = pc["1년전대비"].dropna()
    s["비교단지수"] = int(len(p12))
    s["상승비율"] = float((p12 > 0).mean() * 100) if len(p12) >= 10 else np.nan
    s["동일단지_1년"] = float(p12.median()) if len(p12) >= 10 else np.nan
    s["신고가비율"] = float(now["신고가"].mean() * 100) if len(now) and "신고가" in now else np.nan

    w_raw = window(raw, ref, 0)
    s["해제율"] = float(w_raw["해제"].mean() * 100) if len(w_raw) else np.nan
    w_nc = w_raw[~w_raw["해제"]]
    s["직거래율"] = float((w_nc["거래유형"] == "직거래").mean() * 100) if len(w_nc) else np.nan

    y = window(base, ref, 0, span=12)
    y_ago = window(base, ref, 12, span=12)
    s["거래수_12개월"] = int(len(y))
    age = y.groupby("연식구간", observed=True)["평당가"].agg(["median", "size"])
    s["연식_평당가"] = {k: round(float(r["median"])) for k, r in age.iterrows() if r["size"] >= 10}
    s["연식_1년"] = _median_by(pc.dropna(subset=["1년전대비"]).assign(
        연식구간=pd.cut(ref.year - pc["건축년도"].astype(float), AGE_BINS, labels=AGE_LABELS)),
        "1년전대비", "연식구간", AGE_LABELS)
    pa = pc.dropna(subset=["1년전대비"]).assign(
        평형대=pd.cut(pc["면적타입"], [0, 40, 60, 85, 102, 135, 10_000],
                   labels=["40㎡ 미만", "40~60㎡", "60~85㎡", "85~102㎡", "102~135㎡", "135㎡ 이상"],
                   right=False))
    s["면적_1년"] = _median_by(pa, "1년전대비", "평형대",
                             ["40㎡ 미만", "40~60㎡", "60~85㎡", "85~102㎡", "102~135㎡", "135㎡ 이상"])
    band = y["가격대"].value_counts(normalize=True).reindex(PRICE_LABELS).fillna(0) * 100
    s["가격대비중"] = {k: round(float(v), 1) for k, v in band.items()}
    hi_now = (y["거래금액_억"] > 10).mean() * 100 if len(y) else np.nan
    hi_ago = (y_ago["거래금액_억"] > 10).mean() * 100 if len(y_ago) else np.nan
    s["고가비중_변화"] = hi_now - hi_ago if pd.notna(hi_now) and pd.notna(hi_ago) else np.nan

    # 전고점 대비 (수집기간 최고가 대비 최근 3개월 중위, 거래 2건 이상인 평형)
    if len(now):
        peak = base.groupby("평형키")["거래금액_억"].max()
        cur = now.groupby("평형키")["거래금액_억"].agg(["median", "size"])
        cur = cur[cur["size"] >= 2]
        s["고점대비"] = float((cur["median"] / peak.reindex(cur.index)).median() * 100) \
            if len(cur) else np.nan
    else:
        s["고점대비"] = np.nan

    if len(y):
        t = y.loc[y["거래금액"].idxmax()]
        s["최고가"] = {"단지명": t["단지명"], "지역": f"{t['시군구']} {t['법정동']}",
                     "전용면적": round(float(t["전용면적"]), 1), "거래금액_억": float(t["거래금액_억"]),
                     "계약일자": f"{t['계약일자']:%Y.%m}"}
    # 상승 주도 단지: 양쪽 창 2건 이상, 상승률 상위 5
    lead = pc[(pc["최근_건수"] >= 2) & (pc["1년전_건수"] >= 2)].dropna(subset=["1년전대비"])
    s["상승주도"] = [{"단지": f"{r.단지명} {r.면적타입}㎡", "지역": f"{r.시군구} {r.법정동}",
                    "변동": round(float(r["1년전대비"]), 1), "최근": round(float(r.최근), 2)}
                   for _, r in lead.nlargest(5, "1년전대비").iterrows()]
    # 거래 많은 대장 단지(최근 12개월) 5곳과 그 1년 변동
    top = y.groupby("단지키").agg(단지명=("단지명", "last"), 시군구=("시군구", "last"),
                                 거래수=("거래금액", "size")).nlargest(5, "거래수")
    ch = pc.dropna(subset=["1년전대비"]).groupby("단지키")["1년전대비"].median()
    s["거래상위"] = [{"단지": r.단지명, "지역": r.시군구, "거래수": int(r.거래수),
                    "변동": (round(float(ch[k]), 1) if k in ch.index else None)}
                   for k, r in top.iterrows()]
    return {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in s.items()}


def child_table(base, pc, ref, by: str) -> pd.DataFrame:
    """하위 지역별 비교표 (by: 시도코드 / 시군구코드 / 법정동)."""
    now = window(base, ref, 0)
    v_now = window(base, ref, 1).groupby(by).size()
    v_ago = window(base, ref, 13).groupby(by).size()
    p = pc.dropna(subset=["1년전대비"]).groupby(by)["1년전대비"]
    t = pd.DataFrame({
        "거래수_3개월": now.groupby(by).size(),
        "중위거래가": now.groupby(by)["거래금액_억"].median(),
        "중위평당가": now.groupby(by)["평당가"].median(),
        "동일단지_1년": p.median(),
        "비교단지수": p.size(),
        "상승비율": p.apply(lambda s: (s > 0).mean() * 100),
        "신고가비율": now.groupby(by)["신고가"].mean() * 100 if "신고가" in now else np.nan,
        "거래량증감": (v_now / v_ago.where(v_ago >= 10) - 1) * 100,
    })
    t.loc[t["비교단지수"].fillna(0) < 5, ["동일단지_1년", "상승비율"]] = np.nan
    return t.reset_index().rename(columns={"index": by})


# ───────────────────────── 브리핑 문장 ─────────────────────────
def _pct(v, d=1, sign=True):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "-"
    return f"{v:+.{d}f}%" if sign else f"{v:.{d}f}%"


def strength(ch1y):
    if ch1y is None or (isinstance(ch1y, float) and np.isnan(ch1y)):
        return None
    return "강세" if ch1y >= 2 else "약세" if ch1y <= -0.3 else "보합"


def drivers(s: dict, parent: dict | None = None) -> list[str]:
    """실거래 데이터로 본 상승·하락 주도 요인 (문장 목록)."""
    out = []
    ch = s.get("1년")
    up = ch is not None and ch > 0
    age = s.get("연식_1년") or {}
    new, old = age.get("5년 이내"), age.get("30년 초과") or age.get("21~30년")
    if new and old:
        gap = new[0] - old[0]
        if gap >= 3:
            out.append(f"**신축 주도**: 5년 이내 신축 {_pct(new[0])} vs 구축 {_pct(old[0])}. "
                       "새 아파트 선호가 지역 시세를 끌어올리는 구조입니다.")
        elif gap <= -3:
            out.append(f"**구축 반등**: 구축 {_pct(old[0])}가 신축 {_pct(new[0])}보다 강합니다. "
                       "재건축 기대나 '저가 갭 메우기' 매수일 가능성이 있습니다.")
    area = s.get("면적_1년") or {}
    if area:
        best = max(area.items(), key=lambda kv: kv[1][0])
        worst = min(area.items(), key=lambda kv: kv[1][0])
        if best[1][0] - worst[1][0] >= 3:
            out.append(f"**면적 쏠림**: {best[0]} {_pct(best[1][0])}로 가장 강하고 "
                       f"{worst[0]} {_pct(worst[1][0])}로 가장 약합니다.")
    br = s.get("상승비율")
    if br is not None:
        if up and br >= 60:
            out.append(f"**광범위한 상승**: 비교 가능한 단지의 {br:.0f}%가 올랐습니다. "
                       "일부 대장 단지만이 아니라 지역 전반이 오르는 흐름입니다.")
        elif up and br < 50:
            out.append(f"**소수 단지 주도**: 오른 단지는 {br:.0f}%뿐입니다. "
                       "평균 상승은 일부 단지가 끌어올린 결과일 수 있습니다.")
        elif not up and br < 45:
            out.append(f"**하락 확산**: 내린 단지가 {100 - br:.0f}%로 더 많습니다.")
    v = s.get("거래량증감")
    if v is not None and ch is not None:
        if up and v >= 10:
            out.append(f"**거래 동반 상승**: 거래량도 {_pct(v, 0)} 늘었습니다. 실수요·투자 수요가 붙은 상승입니다.")
        elif up and v <= -10:
            out.append(f"**거래 없는 상승**: 거래량은 {_pct(v, 0)}입니다. 매물이 적어 호가가 버티는 "
                       "형태일 수 있어 추세 지속은 거래량 회복을 확인할 필요가 있습니다.")
        elif not up and v <= -10:
            out.append(f"**관망세**: 가격 약세와 함께 거래량 {_pct(v, 0)}. 매수 대기 수요가 많습니다.")
    nh = s.get("신고가비율")
    pnh = (parent or {}).get("신고가비율")
    if nh is not None and pnh is not None and nh >= pnh * 1.5 and nh >= 5:
        out.append(f"**신고가 확산**: 최근 3개월 거래의 {nh:.1f}%가 신고가로, "
                   f"상위 지역 평균({pnh:.1f}%)보다 높습니다.")
    hc = s.get("고가비중_변화")
    if hc is not None and abs(hc) >= 2:
        out.append(f"**고가 거래 비중 {'확대' if hc > 0 else '축소'}**: 10억 초과 거래 비중이 1년 전보다 "
                   f"{hc:+.1f}%p. {'고가 단지로 매수세가 옮겨가고' if hc > 0 else '고가 단지 매수세가 줄고'} 있습니다.")
    m_now, m_ago, same = s.get("중위평당가"), s.get("중위평당가_1년전"), s.get("1년")
    if m_now and m_ago and same is not None:
        raw_ch = (m_now / m_ago - 1) * 100
        if abs(raw_ch - same) >= 3:
            out.append(f"**구성 효과 주의**: 중위 평당가만 보면 {_pct(raw_ch)}이지만 같은 단지끼리는 "
                       f"{_pct(same)}입니다. 어떤 단지가 많이 팔렸는지에 따라 평균이 움직인 것입니다.")
    return out


# ───────────────────────── 상세 탭용 ─────────────────────────
def top_trades(df: pd.DataFrame, n=10, one_per_complex=True) -> pd.DataFrame:
    if one_per_complex:  # 단지별 최고가 1건만
        df = df.sort_values("거래금액", ascending=False).drop_duplicates("단지키")
    return df.nlargest(n, "거래금액")


def top_complexes(df: pd.DataFrame, n=10, min_n=3) -> pd.DataFrame:
    """기간 내 중위 평당가 상위 단지 (거래 min_n건 이상)."""
    g = (df.sort_values("계약일자").groupby("단지키")
         .agg(단지명=("단지명", "last"), 시군구=("시군구", "last"), 법정동=("법정동", "last"),
              건축년도=("건축년도", "last"), 거래수=("거래금액", "size"),
              중위평당가=("평당가", "median"), 중위거래가=("거래금액_억", "median"),
              최고거래가=("거래금액_억", "max"), 최근거래일=("계약일자", "max")))
    g["중위평당가"] = g["중위평당가"].round()
    return g[g["거래수"] >= min_n].nlargest(n, "중위평당가").reset_index()


def peak_recovery(df: pd.DataFrame, ref: pd.Timestamp, min_recent=1) -> pd.DataFrame:
    """평형키별 수집기간 최고가 대비 최근 3개월 중위가."""
    d = df.sort_values("계약일자")
    idx = d.groupby("평형키")["거래금액"].idxmax()
    peak = d.loc[idx, ["평형키", "거래금액_억", "계약일자"]].rename(
        columns={"거래금액_억": "최고가", "계약일자": "최고가일"}).set_index("평형키")
    now = window(d, ref, 0).groupby("평형키").agg(최근중위=("거래금액_억", "median"),
                                                  최근건수=("거래금액_억", "size"))
    now = now[now["최근건수"] >= min_recent]
    cols = [c for c in INFO_COLS if c in d.columns]
    info = d.groupby("평형키")[cols].last()
    t = now.join(peak).join(info)
    t["고점대비(%)"] = t["최근중위"] / t["최고가"] * 100
    return t.reset_index()


def budget_search(df: pd.DataFrame, ref: pd.Timestamp, lo: float, hi: float,
                  months: int = 6) -> pd.DataFrame:
    """최근 months개월 중위 거래가가 예산(억) 안에 드는 단지·면적."""
    w = window(df, ref, 0, span=months)
    g = (w.sort_values("계약일자").groupby("평형키")
         .agg(단지키=("단지키", "last"), 단지명=("단지명", "last"), 시군구=("시군구", "last"),
              법정동=("법정동", "last"), 면적타입=("면적타입", "last"),
              건축년도=("건축년도", "last"), 중위거래가=("거래금액_억", "median"),
              최근거래가=("거래금액_억", "last"), 최근거래일=("계약일자", "last"),
              거래수=("거래금액_억", "size"), 평당가=("평당가", "median")))
    g["평당가"] = g["평당가"].round()
    g = g[(g["중위거래가"] >= lo) & (g["중위거래가"] <= hi)]
    prev = window(df, ref, 12, span=months).groupby("평형키")["거래금액_억"].median()
    g["1년전대비"] = (g["중위거래가"] / prev.reindex(g.index) - 1) * 100
    return g.reset_index()


# ───────────────────────── 외부 요인 (KOSIS) ─────────────────────────
def ext_stats(code: str, unsold, mig, comp, ref) -> dict:
    """지역 코드의 미분양·인구 순이동·아파트 준공 요약. 표가 없으면 빈 dict."""
    s = {}
    if unsold is not None:
        u = unsold[unsold["코드"] == code].set_index("월")["미분양"].sort_index()
        if len(u):
            last = u.index.max()
            s["미분양_기준월"] = f"{last:%Y.%m}"
            s["미분양"] = float(u.iloc[-1])
            ago = last - pd.DateOffset(months=12)
            s["미분양_1년전"] = float(u[ago]) if ago in u.index else None
            ago6 = last - pd.DateOffset(months=6)
            s["미분양_6개월전"] = float(u[ago6]) if ago6 in u.index else None
            s["미분양_시단위"] = bool(unsold[unsold["코드"] == code]["시단위"].any())
    if mig is not None:
        m = mig[mig["코드"] == code].set_index("월").sort_index()
        if len(m) >= 12:
            last = m.index.max()
            w = m[m.index > last - pd.DateOffset(months=12)]
            p = m[(m.index <= last - pd.DateOffset(months=12)) & (m.index > last - pd.DateOffset(months=24))]
            s["인구_기준월"] = f"{last:%Y.%m}"
            s["순이동_12개월"] = float(w["순이동"].sum())
            s["전입_12개월"] = float(w["전입"].sum())
            s["순이동_전년12개월"] = float(p["순이동"].sum()) if len(p) >= 12 else None
            s["인구_시단위"] = bool(m["시단위"].any())
    if comp is not None:
        c = comp[comp["코드"] == code].set_index("월")["아파트준공"].sort_index()
        if len(c) >= 12:
            last = c.index.max()
            s["준공_기준월"] = f"{last:%Y.%m}"
            s["준공_12개월"] = float(c[c.index > last - pd.DateOffset(months=12)].sum())
            prev = c[(c.index <= last - pd.DateOffset(months=12)) & (c.index > last - pd.DateOffset(months=24))]
            s["준공_전년12개월"] = float(prev.sum()) if len(prev) >= 12 else None
    return s


def external_factors(s: dict, scope_sido: str | None = None) -> list[str]:
    """공공통계로 본 수요·공급 요인 문장."""
    out = []
    u, u1 = s.get("미분양"), s.get("미분양_1년전")
    if u is not None:
        tail = " (시 전체 값)" if s.get("미분양_시단위") else ""
        if u1:
            ch = (u / u1 - 1) * 100
            if u >= 50 and ch >= 20:
                out.append(f"**미분양 증가**: {s['미분양_기준월']} {u:,.0f}호로 1년 전({u1:,.0f}호)보다 "
                           f"{ch:+.0f}%{tail}. 팔리지 않은 새 집이 쌓이면 주변 시세에 부담이 됩니다.")
            elif u1 >= 50 and ch <= -20:
                out.append(f"**미분양 소진**: {s['미분양_기준월']} {u:,.0f}호로 1년 전({u1:,.0f}호)보다 "
                           f"{ch:+.0f}%{tail}. 신규 공급이 소화되고 있어 가격을 받치는 요인입니다.")
            else:
                out.append(f"미분양 {u:,.0f}호({s['미분양_기준월']}, 1년 전 {u1:,.0f}호){tail}.")
        else:
            out.append(f"미분양 {u:,.0f}호({s['미분양_기준월']}){tail}.")
    n, n0, inflow = s.get("순이동_12개월"), s.get("순이동_전년12개월"), s.get("전입_12개월")
    if n is not None and inflow:
        rate = n / inflow * 100
        tail = " (시 전체 값)" if s.get("인구_시단위") else ""
        if rate >= 3:
            out.append(f"**인구 순유입**: 최근 12개월 {n:+,.0f}명{tail}. 전입이 전출보다 많아 실수요를 받칩니다.")
        elif rate <= -3:
            out.append(f"**인구 순유출**: 최근 12개월 {n:+,.0f}명{tail}. 실수요 기반이 약해지는 요인입니다.")
        else:
            out.append(f"인구 이동은 균형에 가깝습니다(최근 12개월 순이동 {n:+,.0f}명{tail}).")
    c, c0 = s.get("준공_12개월"), s.get("준공_전년12개월")
    if c is not None and c0:
        ch = (c / c0 - 1) * 100
        where = f"{scope_sido} 전체 " if scope_sido else ""
        if ch >= 30:
            out.append(f"**입주 물량 증가**: {where}아파트 준공 최근 12개월 {c:,.0f}호(전년 대비 {ch:+.0f}%). "
                       "새 아파트 입주가 몰리면 매물·전세 공급이 늘어 가격에 부담입니다.")
        elif ch <= -30:
            out.append(f"**입주 물량 감소**: {where}아파트 준공 최근 12개월 {c:,.0f}호(전년 대비 {ch:+.0f}%). "
                       "공급 공백은 가격을 받치는 요인입니다.")
        else:
            out.append(f"{where}아파트 준공은 최근 12개월 {c:,.0f}호로 전년과 비슷합니다({ch:+.0f}%).")
    return out
