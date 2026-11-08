"""시장 분석 함수 모음 (Streamlit과 분리해 단독 테스트 가능).

가격 비교 원칙
- 단지·평형이 섞이면 '비싼 단지가 많이 팔렸다'는 구성 변화가 가격 변화처럼 보인다.
  그래서 변동률은 **같은 단지 · 같은 전용면적(㎡ 반올림)** 끼리만 비교한다.
- 한 달 거래는 적으므로 3개월 창의 중위가를 쓴다.
    최근     = 기준월 포함 직전 3개월
    6개월 전 = 그보다 6개월 앞선 3개월
    1년 전   = 그보다 12개월 앞선 3개월
"""
import numpy as np
import pandas as pd

AGE_BINS = [-1, 5, 10, 20, 30, 200]
AGE_LABELS = ["5년 이내", "6~10년", "11~20년", "21~30년", "30년 초과"]
PRICE_BINS = [0, 3, 5, 7, 10, 1_000]
PRICE_LABELS = ["3억 이하", "3~5억", "5~7억", "7~10억", "10억 초과"]


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """분석용 파생 컬럼 추가."""
    df = df.copy()
    df["평당가"] = df["평당가"].round()
    df["면적타입"] = df["전용면적"].round().astype(int)
    df["평형키"] = df["단지키"] + "#" + df["면적타입"].astype(str)
    ref_year = df["계약일자"].dt.year
    df["연식"] = (ref_year - df["건축년도"]).clip(lower=0)
    df["연식구간"] = pd.cut(df["연식"], AGE_BINS, labels=AGE_LABELS)
    df["가격대"] = pd.cut(df["거래금액_억"], PRICE_BINS, labels=PRICE_LABELS, right=True)
    return df


def window(df, ref: pd.Timestamp, back: int, span: int = 3):
    """ref(월초)에서 back개월 이전을 끝으로 하는 span개월 창."""
    end = ref - pd.DateOffset(months=back)
    start = end - pd.DateOffset(months=span - 1)
    return df[(df["계약월"] >= start) & (df["계약월"] <= end)]


def window_label(ref, back, span=3):
    end = ref - pd.DateOffset(months=back)
    start = end - pd.DateOffset(months=span - 1)
    return f"{start:%y.%m}–{end:%y.%m}"


# ───────────────────────── 신고가 표시 ─────────────────────────
def mark_new_highs(df: pd.DataFrame) -> pd.DataFrame:
    """같은 단지·면적에서 이전 모든 거래의 최고가를 넘어선 거래 = 신고가.
    수집 기간 안에서의 최고가이며, 이전 거래가 2건 이상 있을 때만 판단한다."""
    d = df.sort_values("계약일자").copy()
    g = d.groupby("평형키")["거래금액"]
    d["직전최고가"] = g.transform(lambda s: s.shift().cummax())
    d["이전거래수"] = g.cumcount()
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
        t[c] = t[c].where(ok)  # 건수 미달 시 가격도 비움
        t[f"{c}대비"] = (t["최근"] / t[c] - 1) * 100
    info = (df.sort_values("계약일자").groupby("평형키")
            [["단지키", "단지명", "구군", "법정동", "면적타입", "건축년도"]].last())
    return t.join(info).reset_index()


# ───────────────────────── 구별 요약 ─────────────────────────
def gu_summary(df: pd.DataFrame, ref: pd.Timestamp, order: list[str]) -> pd.DataFrame:
    """전문가용 구별 시장 지표 한 장."""
    d = mark_new_highs(df)
    now = window(d, ref, 0)
    ago = window(d, ref, 12)
    pc = price_changes(df, ref, min_n=1)

    rows = []
    for gu in order:
        n, a = now[now["구군"] == gu], ago[ago["구군"] == gu]
        p = pc[pc["구군"] == gu]
        p6, p12 = p["6개월전대비"].dropna(), p["1년전대비"].dropna()
        rows.append({
            "구군": gu,
            "최근 거래(3개월)": len(n),
            "거래량 증감(전년동기)": (len(n) / len(a) - 1) * 100 if len(a) else np.nan,
            "중위 거래가(억)": n["거래금액_억"].median(),
            "중위 평당가(만원)": n["평당가"].median(),
            "6개월 변동(%)": p6.median() if len(p6) else np.nan,
            "1년 변동(%)": p12.median() if len(p12) else np.nan,
            "1년 상승 단지 비율(%)": (p12 > 0).mean() * 100 if len(p12) else np.nan,
            "신고가 비율(%)": n["신고가"].mean() * 100 if len(n) else np.nan,
            "직거래 비율(%)": (n["거래유형"] == "직거래").mean() * 100
            if len(n) and "거래유형" in n else np.nan,
        })
    out = pd.DataFrame(rows)
    return out[out["최근 거래(3개월)"] > 0].reset_index(drop=True)


def cancel_rate(df_with_cancel: pd.DataFrame, ref, order) -> pd.Series:
    """해제 비율은 해제 거래를 포함한 원자료에서 계산."""
    w = window(df_with_cancel, ref, 0)
    return w.groupby("구군")["해제"].mean().mul(100).reindex(order)


# ───────────────────────── TOP 10 ─────────────────────────
def top_trades(df: pd.DataFrame, n=10, one_per_complex=True) -> pd.DataFrame:
    if one_per_complex:  # 단지별 최고가 1건만
        df = df.sort_values("거래금액", ascending=False).drop_duplicates("단지키")
    return df.nlargest(n, "거래금액")


def top_complexes(df: pd.DataFrame, n=10, min_n=3) -> pd.DataFrame:
    """기간 내 중위 평당가 상위 단지 (거래 min_n건 이상)."""
    g = (df.sort_values("계약일자").groupby("단지키")
         .agg(단지명=("단지명", "last"), 구군=("구군", "last"), 법정동=("법정동", "last"),
              건축년도=("건축년도", "last"), 거래수=("거래금액", "size"),
              중위평당가=("평당가", "median"), 중위거래가=("거래금액_억", "median"),
              최고거래가=("거래금액_억", "max"), 최근거래일=("계약일자", "max")))
    g["중위평당가"] = g["중위평당가"].round()
    return g[g["거래수"] >= min_n].nlargest(n, "중위평당가").reset_index()


# ───────────────────────── 전고점 대비 ─────────────────────────
def peak_recovery(df: pd.DataFrame, ref: pd.Timestamp, min_recent=1) -> pd.DataFrame:
    """평형키별 수집기간 최고가 대비 최근 3개월 중위가."""
    d = df.sort_values("계약일자")
    idx = d.groupby("평형키")["거래금액"].idxmax()
    peak = d.loc[idx, ["평형키", "거래금액_억", "계약일자"]].rename(
        columns={"거래금액_억": "최고가", "계약일자": "최고가일"}).set_index("평형키")
    now = window(d, ref, 0).groupby("평형키").agg(최근중위=("거래금액_억", "median"),
                                                  최근건수=("거래금액_억", "size"))
    now = now[now["최근건수"] >= min_recent]
    info = d.groupby("평형키")[["단지키", "단지명", "구군", "법정동", "면적타입", "건축년도"]].last()
    t = now.join(peak).join(info)
    t["고점대비(%)"] = t["최근중위"] / t["최고가"] * 100
    return t.reset_index()


# ───────────────────────── 예산으로 찾기 ─────────────────────────
def budget_search(df: pd.DataFrame, ref: pd.Timestamp, lo: float, hi: float,
                  months: int = 6) -> pd.DataFrame:
    """최근 months개월 중위 거래가가 예산(억) 안에 드는 단지·면적."""
    w = window(df, ref, 0, span=months)
    g = (w.sort_values("계약일자").groupby("평형키")
         .agg(단지키=("단지키", "last"), 단지명=("단지명", "last"), 구군=("구군", "last"),
              법정동=("법정동", "last"), 면적타입=("면적타입", "last"),
              건축년도=("건축년도", "last"), 중위거래가=("거래금액_억", "median"),
              최근거래가=("거래금액_억", "last"), 최근거래일=("계약일자", "last"),
              거래수=("거래금액_억", "size"), 평당가=("평당가", "median")))
    g["평당가"] = g["평당가"].round()
    g = g[(g["중위거래가"] >= lo) & (g["중위거래가"] <= hi)]
    # 같은 길이의 1년 전 창과 비교
    prev = window(df, ref, 12, span=months).groupby("평형키")["거래금액_억"].median()
    g["1년전대비"] = (g["중위거래가"] / prev.reindex(g.index) - 1) * 100
    return g.reset_index()
