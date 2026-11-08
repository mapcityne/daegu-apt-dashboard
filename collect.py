"""1단계: 대구 9개 구·군 아파트 매매 실거래가 수집 → CSV.

사용법
    python collect.py                 # 최근 36개월
    python collect.py --months 12     # 최근 12개월
    python collect.py --start 202301 --end 202610

- 구·군×월 단위로 data/raw/에 캐시하므로 중간에 끊겨도 다시 실행하면 이어서 받는다.
- 최근 2개월은 신고 지연(계약 후 30일 이내 신고)·해제 반영을 위해 매번 새로 받는다.
- 호출 수: 9개 구·군 × 36개월 = 324회 (개발계정 일일 한도 내).
"""
import argparse
import os
import sys
import time
from datetime import date

import pandas as pd
from PublicDataReader import TransactionPrice

from common import (DAEGU_SIGUNGU, RAW_DIR, TRADES_CSV, clean_trades, load_env)

REFRESH_RECENT = 2  # 매번 다시 받을 최근 개월 수
KEEP_COLUMNS = ["법정동시군구코드", "구군", "법정동", "단지명", "지번", "단지일련번호", "단지키",
                "계약일자", "계약월", "거래금액", "거래금액_억", "전용면적", "평당가", "평형대",
                "층", "건축년도", "거래유형", "해제여부", "해제"]


def month_list(start: str, end: str) -> list[str]:
    return list(pd.period_range(pd.Period(start, "M"), pd.Period(end, "M"), freq="M")
                .strftime("%Y%m"))


def fetch_month(api, code, ym, retries=3):
    for i in range(retries):
        try:
            return api.get_data("아파트", "매매", code, year_month=ym)
        except Exception as e:  # 네트워크 오류, 일시적 서버 오류 등
            msg = str(e)
            if "SERVICE_KEY" in msg or "등록되지 않은" in msg or "LIMITED" in msg:
                sys.exit(f"[중단] API 키/한도 문제: {msg}")
            print(f"  재시도 {i + 1}/{retries} ({code} {ym}): {msg}")
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"{code} {ym} 수집 실패")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--months", type=int, default=36)
    p.add_argument("--start")
    p.add_argument("--end")
    args = p.parse_args()

    load_env()
    key = os.environ.get("DATA_GO_KR_KEY")
    if not key:
        sys.exit("DATA_GO_KR_KEY가 없습니다. .env 파일에 DATA_GO_KR_KEY=발급키 를 넣으세요.")

    today = pd.Period(date.today(), "M")
    end = args.end or today.strftime("%Y%m")
    start = args.start or (today - (args.months - 1)).strftime("%Y%m")
    months = month_list(start, end)
    recent = set(month_list((pd.Period(end, "M") - (REFRESH_RECENT - 1)).strftime("%Y%m"), end))

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    api = TransactionPrice(key)
    total = len(months) * len(DAEGU_SIGUNGU)
    n = 0
    print(f"수집 기간 {start}~{end}, {len(DAEGU_SIGUNGU)}개 구·군, 총 {total}건 요청")

    for code, name in DAEGU_SIGUNGU.items():
        for ym in months:
            n += 1
            path = RAW_DIR / f"{code}_{ym}.csv"
            if path.exists() and ym not in recent:
                continue
            df = fetch_month(api, code, ym)
            df.to_csv(path, index=False, encoding="utf-8-sig")
            print(f"[{n}/{total}] {name} {ym}: {len(df)}건")
            time.sleep(0.2)

    # 기간 내 캐시 파일만 합친다
    frames = []
    for code in DAEGU_SIGUNGU:
        for ym in months:
            path = RAW_DIR / f"{code}_{ym}.csv"
            if path.exists() and path.stat().st_size > 0:
                d = pd.read_csv(path, dtype=str)
                if len(d):
                    frames.append(d)
    if not frames:
        sys.exit("수집된 거래가 없습니다.")

    raw = pd.concat(frames, ignore_index=True)
    for c in ["계약년도", "계약월", "계약일", "거래금액", "층", "건축년도"]:
        raw[c] = pd.to_numeric(raw[c].str.replace(",", ""), errors="coerce")
    df = clean_trades(raw)
    df = df[[c for c in KEEP_COLUMNS if c in df.columns]]  # 저장소 용량 절감
    df.to_csv(TRADES_CSV, index=False, encoding="utf-8-sig")
    print(f"\n저장: {TRADES_CSV}  ({len(df):,}건, 해제 {df['해제'].sum():,}건 포함, "
          f"단지 {df['단지키'].nunique():,}개)")


if __name__ == "__main__":
    main()
