"""지역 브리핑 문장 생성 (stats.parquet 한 행 + 하위 지역 행 → 마크다운)."""
import json

import numpy as np
import pandas as pd

import analysis as A

ARROW = {"상승": "▲", "하락": "▼", "보합": "■", "판단 보류": "·"}
COLOR = {"상승": "#d6336c", "하락": "#1c7ed6", "보합": "#868e96", "판단 보류": "#adb5bd"}


def val(v):
    if v is None:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    if isinstance(v, str) and v in ("NaN", "null", "None"):
        return None
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def row_dict(row) -> dict:
    return {k: val(v) for k, v in (row.items() if hasattr(row, "items") else row)}


def p(v, d=1, sign=True):
    return A._pct(v, d, sign)


def market_section(s: dict, child_word: str) -> list[str]:
    out = []
    j = s.get("판정", "판단 보류")
    if j == "판단 보류":
        return ["거래가 적어 방향을 판단하기 어렵습니다."]
    ch3, ch6, ch1 = s.get("3개월"), s.get("6개월"), s.get("1년")
    tone = {"상승": "상승 흐름입니다", "하락": "하락 흐름입니다",
            "보합": "큰 상승도 큰 하락도 없는 보합 흐름입니다"}[j]
    out.append(f"같은 단지·같은 면적 기준 가격지수는 최근 3개월 **{p(ch3)}**, 6개월 **{p(ch6)}**, "
               f"1년 **{p(ch1)}**입니다. {tone}.")
    v = s.get("거래량증감")
    if v is not None:
        if v <= -10:
            mood = "가격은 버티는데 거래가 줄어드는, 관망세에 가까운 신호입니다." if (ch3 or 0) > -1 \
                else "가격과 거래가 함께 약해지는 신호입니다."
        elif v >= 10:
            mood = "거래가 살아나는 신호입니다." if (ch3 or 0) >= -1 else "가격을 낮춘 매물이 소화되는 신호일 수 있습니다."
        else:
            mood = "거래량은 평년 수준입니다."
        out.append(f"거래량(신고 지연을 감안해 기준월 직전 3개월)은 전년 동기 대비 **{p(v, 0)}**입니다. {mood}")
    br = s.get("상승비율")
    if br is not None:
        out.append(f"1년 전과 비교 가능한 단지 {int(s.get('비교단지수') or 0):,}곳 중 **{br:.0f}%**가 올랐습니다.")
    m_now, m_ago = s.get("중위평당가"), s.get("중위평당가_1년전")
    if m_now and m_ago and ch1 is not None:
        raw = (m_now / m_ago - 1) * 100
        if abs(raw - ch1) >= 3:
            out.append(f"중위 평당가만 보면 {m_ago:,.0f}만원 → {m_now:,.0f}만원({p(raw)})이지만, 같은 단지끼리는 "
                       f"{p(ch1)}입니다. 차이는 어떤 단지가 많이 팔렸는지에 따른 **구성 효과**입니다.")
    return out


def children_section(children: pd.DataFrame, name_col="이름") -> tuple[pd.DataFrame, list[str]]:
    """하위 지역 강세/보합/약세 표와 해설."""
    c = children.dropna(subset=["1년"]).copy()
    if c.empty:
        return c, []
    c["구분"] = c["1년"].map(A.strength)
    order = {"강세": 0, "보합": 1, "약세": 2}
    c = c.sort_values(["구분", "1년"], key=lambda s: s.map(order) if s.name == "구분" else -s)

    def feat(r):
        f = []
        if r.get("상승비율") is not None and not pd.isna(r.get("상승비율")):
            f.append(f"상승 단지 {r['상승비율']:.0f}%")
        if r.get("신고가비율") is not None and not pd.isna(r.get("신고가비율")) and r["신고가비율"] >= 8:
            f.append(f"신고가 {r['신고가비율']:.1f}%")
        v = r.get("거래량증감")
        if v is not None and not pd.isna(v) and abs(v) >= 25:
            f.append(f"거래량 {v:+.0f}%")
        if r.get("중위평당가") and not pd.isna(r.get("중위평당가")):
            f.append(f"평당 {r['중위평당가']:,.0f}만원")
        return ", ".join(f)

    c["특징"] = c.apply(feat, axis=1)
    notes = []
    top = c.iloc[0]
    if top["구분"] == "강세":
        notes.append(f"가장 강한 곳은 **{top[name_col]}**({p(top['1년'])})입니다.")
    weak = c[c["구분"] == "약세"]
    if len(weak):
        notes.append(f"약세 지역은 {', '.join(weak[name_col].head(5))} 등 {len(weak)}곳입니다.")
    vol = c.dropna(subset=["거래량증감"])
    if len(vol):
        lo = vol.nsmallest(1, "거래량증감").iloc[0]
        if lo["거래량증감"] <= -25:
            notes.append(f"거래 위축이 가장 큰 곳은 {lo[name_col]}(거래량 {lo['거래량증감']:+.0f}%)입니다.")
    return c, notes


def age_section(s: dict) -> list[str]:
    out = []
    ap = s.get("연식_평당가") or {}
    if "5년 이내" in ap and ("21~30년" in ap or "30년 초과" in ap):
        old_k = "21~30년" if "21~30년" in ap else "30년 초과"
        ratio = ap["5년 이내"] / ap[old_k]
        out.append(f"평당가는 준공 5년 이내가 {ap['5년 이내']:,}만원, {old_k}이 {ap[old_k]:,}만원으로 "
                   f"약 **{ratio:.1f}배** 차이입니다.")
    a1 = s.get("연식_1년") or {}
    if "5년 이내" in a1 and ("30년 초과" in a1 or "21~30년" in a1):
        old_k = "30년 초과" if "30년 초과" in a1 else "21~30년"
        gap = a1["5년 이내"][0] - a1[old_k][0]
        tail = ("상승이 신축에 집중되고 있습니다." if gap >= 3 else
                "구축이 오히려 강합니다." if gap <= -3 else "연식에 따른 차이는 크지 않습니다.")
        out.append(f"1년 변동도 5년 이내 신축 **{p(a1['5년 이내'][0])}**, {old_k} 구축 "
                   f"**{p(a1[old_k][0])}**입니다. {tail}")
    return out


def area_section(s: dict) -> list[str]:
    a = s.get("면적_1년") or {}
    if len(a) < 2:
        return []
    best = max(a.items(), key=lambda kv: kv[1][0])
    parts = ", ".join(f"{k} {p(v[0])}" for k, v in a.items())
    return [f"면적별 1년 변동: {parts}.", f"가장 강한 면적대는 **{best[0]}**입니다."]


def buyer_section(s: dict) -> list[str]:
    out = []
    b = s.get("가격대비중") or {}
    if b:
        u5 = b.get("3억 이하", 0) + b.get("3~5억", 0)
        out.append(f"최근 12개월 거래의 **{u5:.0f}%가 5억 이하**입니다(3억 이하 {b.get('3억 이하', 0):.0f}%, "
                   f"3~5억 {b.get('3~5억', 0):.0f}%). 10억 초과는 {b.get('10억 초과', 0):.1f}%입니다.")
    if s.get("고점대비") is not None:
        gap = 100 - s["고점대비"]
        out.append(f"최근 가격은 수집기간 최고가의 중위 **{s['고점대비']:.0f}%** 수준입니다"
                   + (f"(고점보다 약 {gap:.0f}% 아래)." if gap >= 1 else "(고점 수준)."))
    if s.get("해제율") is not None:
        out.append(f"해제율 {s['해제율']:.1f}%, 직거래 비율 {p(s.get('직거래율'), 1, False)}. "
                   "개별 신고가는 해제 여부를 함께 확인하세요.")
    t = s.get("최고가")
    if t:
        out.append(f"최근 12개월 최고가는 {t['지역']} **{t['단지명']}**(전용 {t['전용면적']}㎡) "
                   f"{t['거래금액_억']:.1f}억입니다({t['계약일자']}).")
    return out
