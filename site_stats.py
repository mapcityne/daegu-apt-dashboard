"""정적 사이트용 통계: 월별 통계(기사 인용용)와 '오늘 새로 공개된 거래'.

오늘 공개 거래는 실거래가 API에 신고일이 없어서, 전날 빌드 때 본 거래 목록(해시)과 비교해
이번 갱신에서 처음 나타난 거래를 '새로 공개된 거래'로 본다. 목록은 SEEN_DIR(기본 data/seen)에
날짜별로 저장하고, GitHub Actions 캐시로 다음 빌드에 넘긴다.
"""
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

KEY_COLS = ["단지키", "계약일자", "거래금액", "전용면적", "층"]
KEEP_SNAPSHOTS = 4
KEEP_LOG_DAYS = 60


# ───────────────────────── 오늘 공개 거래 ─────────────────────────
def trade_hash(t: pd.DataFrame) -> np.ndarray:
    """같은 단지·계약일·금액·면적·층 거래가 여러 건이어도 구분되도록 순번을 붙여 해시한다."""
    k = t[KEY_COLS].copy()
    k["계약일자"] = pd.to_datetime(k["계약일자"]).dt.strftime("%Y%m%d")
    k["층"] = k["층"].fillna(-99).astype(int)
    k["전용면적"] = (k["전용면적"].astype(float) * 100).round().astype(int)
    k["n"] = k.groupby(list(k.columns)).cumcount()
    return pd.util.hash_pandas_object(k, index=False).to_numpy(np.uint64)


class Fresh:
    """전날 스냅샷과 비교해 새로 공개된 거래를 찾는다."""

    def __init__(self, today: str, seen_dir: str | None = None):
        self.dir = Path(seen_dir or os.environ.get("SEEN_DIR", "data/seen"))
        self.dir.mkdir(parents=True, exist_ok=True)
        self.today = today
        prev = sorted(p for p in self.dir.glob("seen_*.npy") if p.stem[5:] < today)
        self.base_date = prev[-1].stem[5:] if prev else None
        self.base = np.load(prev[-1]) if prev else None
        self.current = []
        self.recent = {}
        self.log_path = self.dir / "log.json"
        try:
            self.log = json.loads(self.log_path.read_text(encoding="utf-8"))
        except Exception:
            self.log = {}

    @property
    def ready(self):
        return self.base is not None

    def pick(self, t_raw: pd.DataFrame, last: pd.Timestamp) -> pd.DataFrame:
        """t_raw: 해당 시도 전체 거래(enrich 후). 새로 공개된 거래 행을 돌려준다(해제 포함 여부 그대로)."""
        # 수집기는 최근 2개월(이번 달·지난달)을 매일 다시 받는다. 그보다 오래된 달은 주 1회 재수집이라
        # 정정분이 '새 거래'로 섞이지 않도록 비교 범위를 최근 2개월로 한정한다.
        start = last.to_period("M").to_timestamp() - pd.DateOffset(months=1)
        w = t_raw[t_raw["계약일자"] >= start]
        h = trade_hash(w)
        self.current.append(h)
        if not self.ready:
            return w.iloc[0:0]
        return w[~np.isin(h, self.base)]

    def save(self, counts: dict):
        np.save(self.dir / f"seen_{self.today}.npy", np.unique(np.concatenate(self.current)) if self.current
                else np.array([], np.uint64))
        for p in sorted(self.dir.glob("seen_*.npy"))[:-KEEP_SNAPSHOTS]:
            p.unlink()
        if self.ready:
            self.log[self.today] = counts
        self.log = dict(sorted(self.log.items())[-KEEP_LOG_DAYS:])
        self.log_path.write_text(json.dumps(self.log, ensure_ascii=False), encoding="utf-8")


# ───────────────────────── 월별 통계 ─────────────────────────
def month_status(m: pd.Timestamp, last: pd.Timestamp) -> str:
    """실거래는 계약 후 30일 안에 신고하므로, 기한이 지나지 않은 달은 숫자가 더 늘 수 있다."""
    end = m + pd.offsets.MonthEnd(0)
    if last <= end:
        return "집계 중"
    if last <= end + pd.Timedelta(days=30):
        return "잠정"
    return ""


def monthly(t_all: pd.DataFrame, t_price: pd.DataFrame, idx: pd.Series, last: pd.Timestamp, n=13):
    """월별 거래량(해제 제외, 직거래 포함)·중위 거래가·중위 평당가(직거래 제외)·가격지수."""
    cnt = t_all.groupby("계약월").size()
    med = t_price.groupby("계약월")["거래금액_억"].median()
    ppy = t_price.groupby("계약월")["평당가"].median()
    months = pd.date_range(end=last.to_period("M").to_timestamp(), periods=n + 12, freq="MS")
    d = pd.DataFrame(index=months)
    d["거래량"] = cnt.reindex(months)
    d["중위가"] = med.reindex(months)
    d["평당가"] = ppy.reindex(months)
    d["지수"] = idx.reindex(months) if idx is not None else np.nan
    d["거래량_전월"] = d["거래량"].pct_change(fill_method=None) * 100
    d["거래량_전년"] = (d["거래량"] / d["거래량"].shift(12) - 1) * 100
    d["지수_전월"] = d["지수"].pct_change(fill_method=None) * 100
    d["지수_전년"] = (d["지수"] / d["지수"].shift(12) - 1) * 100
    d["중위가_전년"] = (d["중위가"] / d["중위가"].shift(12) - 1) * 100
    d["상태"] = [month_status(m, last) for m in d.index]
    return d.iloc[-n:]
