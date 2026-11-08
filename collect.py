"""1단계: 대구 9개 구·군 아파트 매매 실거래가 수집 → CSV.

사용법
    python collect.py                      # 최근 36개월
    python collect.py --months 12          # 최근 12개월
    python collect.py --start 202301 --end 202610
    python collect.py --budget-minutes 35  # 수집 전체 시간 예산(기본 35분)

- 구·군×월 단위로 data/raw/에 캐시하므로 중간에 끊겨도 다시 실행하면 이어서 받는다.
- 최근 2개월은 신고 지연(계약 후 30일 이내 신고)·해제 반영을 위해 매번 새로 받는다.
- 호출 수: 9개 구·군 × 36개월 = 324회 (개발계정 일일 한도 내).

안정성 (2026-11 Actions 60분 무응답 타임아웃 대응)
- PublicDataReader 1.1.1.post2 는 `requests.get(url, params=..., verify=False)` 를 timeout 없이
  호출해 응답이 멈추면 무한 대기한다. 이 스크립트는 requests.Session.request 를 감싸 기본
  timeout(연결 10초/응답 30초)을 넣고, 응답이 조금씩 흘러오는 경우까지 막기 위해 요청 1건에
  signal 기반 하드 데드라인(75초)을 함께 건다.
- 구·군×월 1건이 실패하면 3회 재시도 후 실패 목록에 남기고 다음 구간으로 넘어간다.
  전체를 중단하지 않고, 캐시에 남은 부분 수집분으로 CSV를 만든다.
- --budget-minutes 를 넘기면 남은 구간은 다음 실행으로 미루고 그때까지 모은 데이터를 저장한다.
- 진행 로그는 line buffering 으로 즉시 Actions 로그에 남는다.
"""
import argparse
import os
import re
import signal
import sys
import threading
import time
from contextlib import contextmanager
from datetime import date

import pandas as pd
import requests
from PublicDataReader import TransactionPrice

from common import (DAEGU_SIGUNGU, RAW_DIR, TRADES_CSV, clean_trades, load_env)

REFRESH_RECENT = 2      # 매번 다시 받을 최근 개월 수
HTTP_TIMEOUT = (10, 30)  # requests 기본값 (연결, 응답) 초
HARD_TIMEOUT = 75        # 요청 1건의 절대 상한(초)
RETRIES = 3              # 구·군×월 1건당 시도 횟수
DEFAULT_BUDGET_MIN = 35  # 수집 전체 시간 예산(분)
MIN_LEFT = 30            # 남은 예산이 이보다 적으면 새 요청을 시작하지 않는다(초)
MAX_CONSECUTIVE_FAILS = 10  # 연쇄 실패는 키/한도 문제로 보고 중단
# 키·한도 문제는 재시도해도 같으므로 즉시 중단한다 (응답 메시지 기준)
FATAL_HINTS = ("SERVICE_KEY", "SERVICE KEY", "등록되지 않은", "LIMITED",
               "ACCESS DENIED", "AUTHENTICATION FAILED")

KEEP_COLUMNS = ["법정동시군구코드", "구군", "법정동", "단지명", "지번", "단지일련번호", "단지키",
                "계약일자", "계약월", "거래금액", "거래금액_억", "전용면적", "평당가", "평형대",
                "층", "건축년도", "거래유형", "해제여부", "해제"]


def _mask(text) -> str:
    """공개 저장소 Actions 로그에 키가 남지 않도록 URL의 serviceKey 값을 가린다.
    (Decoding 키는 URL 인코딩되어 찍히므로 GitHub의 시크릿 자동 마스킹이 안 걸린다)"""
    return re.sub(r"(serviceKey=)[^&\s)'\"]+", r"\1***", str(text))


def log(*args):
    """Actions 로그에 즉시 보이도록 flush 해서 출력한다."""
    print(*(_mask(a) for a in args), flush=True)


def setup_stdout():
    """파이프로 넘어갈 때 블록 버퍼링되는 것을 막는다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True, write_through=True)
        except (AttributeError, ValueError):
            pass


class FatalApiError(RuntimeError):
    """키 오류·일일 한도 초과처럼 재시도로 풀리지 않는 오류."""


@contextmanager
def hard_deadline(seconds, label="요청"):
    """signal 기반 하드 타임아웃. requests timeout 이 놓치는 경우까지 끊는다."""
    usable = (seconds and seconds > 0 and hasattr(signal, "SIGALRM")
              and threading.current_thread() is threading.main_thread())
    if not usable:
        yield
        return

    def _on_timeout(signum, frame):
        raise TimeoutError(f"{label} {seconds:.0f}초 응답 없음")

    old = signal.signal(signal.SIGALRM, _on_timeout)
    signal.setitimer(signal.ITIMER_REAL, max(1.0, float(seconds)))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def install_http_defaults(timeout=HTTP_TIMEOUT):
    """timeout 없이 나가는 requests 요청(PublicDataReader 내부)에 기본 timeout 을 넣는다."""
    original = requests.sessions.Session.request
    if getattr(original, "_has_default_timeout", False):
        return

    def request_with_timeout(self, method, url, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = timeout
        return original(self, method, url, **kwargs)

    request_with_timeout._has_default_timeout = True
    requests.sessions.Session.request = request_with_timeout


def _is_fatal(msg: str) -> bool:
    up = msg.upper()
    return any(h in up for h in FATAL_HINTS)


def month_list(start: str, end: str) -> list[str]:
    return list(pd.period_range(pd.Period(start, "M"), pd.Period(end, "M"), freq="M")
                .strftime("%Y%m"))


def fetch_month(api, code, ym, time_left=None, retries=RETRIES):
    """구·군×월 1건 수집. 실패 시 재시도, 최종 실패는 RuntimeError, 키/한도 문제는 FatalApiError."""
    last = None
    for attempt in range(1, retries + 1):
        limit = HARD_TIMEOUT
        if time_left is not None:
            limit = min(limit, time_left())
        try:
            with hard_deadline(limit, f"{code} {ym}"):
                return api.get_data("아파트", "매매", code, year_month=ym)
        except Exception as e:  # 네트워크 오류, 타임아웃, 일시적 서버 오류 등
            last = e
            msg = f"{type(e).__name__}: {e}"
            if _is_fatal(msg):
                raise FatalApiError(msg) from e
            if attempt < retries:
                wait = 2 * attempt
                log(f"  재시도 {attempt}/{retries - 1} ({code} {ym}) {wait}초 후: {msg}")
                time.sleep(wait)
    raise RuntimeError(f"{type(last).__name__}: {last}")


def annotate_warning(text: str):
    """Actions 화면에 경고로 표시한다(로컬 실행에서는 일반 로그)."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        log(f"::warning::{text}")


def main():
    setup_stdout()
    p = argparse.ArgumentParser()
    p.add_argument("--months", type=int, default=36)
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--budget-minutes", type=float, default=DEFAULT_BUDGET_MIN,
                   help="수집 전체 시간 예산(분). 넘기면 남은 구간은 다음 실행으로 넘긴다")
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
    install_http_defaults()
    api = TransactionPrice(key)
    total = len(months) * len(DAEGU_SIGUNGU)

    budget = max(60.0, args.budget_minutes * 60)
    t_start = time.monotonic()
    def time_left():
        return budget - (time.monotonic() - t_start)

    log(f"수집 기간 {start}~{end}, {len(DAEGU_SIGUNGU)}개 구·군, 총 {total}건 요청 "
        f"(최근 {REFRESH_RECENT}개월은 캐시 무시, 예산 {args.budget_minutes:.0f}분, "
        f"요청 타임아웃 {HTTP_TIMEOUT[0]}/{HTTP_TIMEOUT[1]}초·{RETRIES}회)")

    n = 0
    fetched = skipped = 0
    failures = []
    consecutive = 0
    stopped = None

    for code, name in DAEGU_SIGUNGU.items():
        if stopped:
            break
        for ym in months:
            n += 1
            path = RAW_DIR / f"{code}_{ym}.csv"
            if path.exists() and ym not in recent:
                skipped += 1
                if skipped % 20 == 1:
                    log(f"[{n}/{total}] {name} {ym}: 캐시 재사용 (누적 {skipped}건)")
                continue
            if time_left() < MIN_LEFT:
                stopped = f"시간 예산 {args.budget_minutes:.0f}분 소진"
                break
            t0 = time.monotonic()
            try:
                df = fetch_month(api, code, ym, time_left)
            except FatalApiError as e:
                stopped = f"API 키/한도 문제: {e}"
                break
            except Exception as e:
                consecutive += 1
                failures.append((name, ym, f"{type(e).__name__}: {e}"))
                log(f"[{n}/{total}] {name} {ym}: 실패 ({consecutive}건 연속) — "
                    f"{type(e).__name__}: {e}")
                if consecutive >= MAX_CONSECUTIVE_FAILS:
                    stopped = f"{consecutive}건 연속 실패"
                    break
                continue
            consecutive = 0
            if df is None:
                df = pd.DataFrame()
            # 빈 결과도 0건 파일로 남겨 다음 실행에서 같은 요청을 반복하지 않는다
            df.to_csv(path, index=False, encoding="utf-8-sig")
            fetched += 1
            log(f"[{n}/{total}] {name} {ym}: {len(df)}건 ({time.monotonic() - t0:.1f}s)")

    # 기간 내 캐시 파일만 합친다 (실패한 구간은 빠진 채로 진행)
    frames = []
    for code in DAEGU_SIGUNGU:
        for ym in months:
            path = RAW_DIR / f"{code}_{ym}.csv"
            if not path.exists() or path.stat().st_size == 0:
                continue
            try:
                d = pd.read_csv(path, dtype=str)
            except Exception as e:
                log(f"  캐시 읽기 실패 {path.name}: {e}")
                continue
            if len(d):
                frames.append(d)

    log(f"이번 실행: 신규 {fetched}건, 캐시 {skipped}건, 실패 {len(failures)}건, "
        f"경과 {time.monotonic() - t_start:.0f}초")
    if stopped:
        log(f"! 수집 중단: {stopped} — 남은 구간은 다음 실행에서 이어받습니다.")
    if failures:
        shown = ", ".join(f"{a} {b}" for a, b, _ in failures[:20])
        log(f"! 실패 목록({len(failures)}건, 최대 20건 표시): {shown}")
        annotate_warning(f"실거래가 수집 실패 {len(failures)}건 — 부분 수집분으로 계속합니다")

    if not frames:
        sys.exit("수집된 거래가 없습니다.")

    raw = pd.concat(frames, ignore_index=True)
    for c in ["계약년도", "계약월", "계약일", "거래금액", "층", "건축년도"]:
        raw[c] = pd.to_numeric(raw[c].str.replace(",", ""), errors="coerce")
    df = clean_trades(raw)
    df = df[[c for c in KEEP_COLUMNS if c in df.columns]]  # 저장소 용량 절감
    df.to_csv(TRADES_CSV, index=False, encoding="utf-8-sig")
    log(f"\n저장: {TRADES_CSV}  ({len(df):,}건, 해제 {df['해제'].sum():,}건 포함, "
        f"단지 {df['단지키'].nunique():,}개)")


if __name__ == "__main__":
    main()
