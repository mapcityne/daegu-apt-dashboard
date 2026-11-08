"""전국 아파트 매매 실거래가 수집 (국토교통부 실거래가 상세 API 직접 호출).

사용법
    python collect.py                      # 최근 36개월, 전국 256개 시군구
    python collect.py --months 12 --sido 27   # 대구만 12개월
    python collect.py --budget-minutes 300 --workers 4

설계
- 시군구×월 1건 = 결과 파일 1개(data/raw/<코드>/<YYYYMM>.parquet). 중단돼도 다음 실행에서 이어받는다.
- 최근 달부터 거꾸로 받는다. 한도·시간에 걸려 중간에 멈춰도 전국의 최근 시세가 먼저 채워진다.
- 신고 지연·해제 반영: 최근 2개월은 20시간마다, 3~6개월 전은 7일마다 다시 받는다.
- 서버 오류(HTTP 4xx/5xx, 오류 코드)는 '거래 0건'으로 처리하지 않고 실패로 남긴다.
  (PublicDataReader는 HTTP 오류를 빈 결과로 돌려줘 데이터가 조용히 빠질 수 있어 쓰지 않는다)
- 일일 호출 한도 초과·키 오류는 즉시 중단하고, 그때까지 받은 것은 보존한다.
- 2026.7 행정구역 개편: 과거 거래가 새 코드로도 조회되는지 먼저 확인(probe)하고,
  안 되면 개편 전 코드로도 받는다. 새 시군구 재배정은 build.py에서 한다.
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import pandas as pd
import requests

from common import RAW_DIR, load_env
from regions import PROBE, regions

URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
FIELDS = ["sggCd", "umdNm", "aptNm", "jibun", "excluUseAr", "dealYear", "dealMonth", "dealDay",
          "dealAmount", "floor", "buildYear", "aptSeq", "cdealType", "dealingGbn"]
ROWS = 1000                 # 페이지당 건수
TIMEOUT = (10, 40)          # 연결, 응답 (초)
RETRIES = 3
MAX_CONSECUTIVE_FAILS = 25
FATAL_CODES = {"20", "22", "30", "31", "32"}  # 접근거부·한도초과·미등록키·기간만료·미등록IP
REFRESH = [(2, 20 * 3600), (6, 7 * 86400)]   # (최근 n개월 이내, 재수집 주기 초)
PROBE_FILE = RAW_DIR / "_probe.json"
STATUS_FILE = RAW_DIR / "_status.json"


class FatalApiError(RuntimeError):
    """한도 초과·키 오류처럼 재시도로 풀리지 않는 오류."""


def log(*args):
    msg = " ".join(str(a) for a in args)
    print(re.sub(r"(serviceKey=)[^&\s)'\"]+", r"\1***", msg), flush=True)


def call(session, key, code, ym, page):
    r = session.get(URL, params={"serviceKey": key, "LAWD_CD": code, "DEAL_YMD": ym,
                                 "pageNo": page, "numOfRows": ROWS}, timeout=TIMEOUT)
    text = r.text
    if r.status_code != 200:
        if "LIMITED" in text.upper():
            raise FatalApiError(f"HTTP {r.status_code} 호출 한도 초과")
        raise RuntimeError(f"HTTP {r.status_code}")
    root = ET.fromstring(text)
    reason = root.findtext(".//returnReasonCode")
    if reason:  # OpenAPI_ServiceResponse 오류 봉투
        msg = root.findtext(".//returnAuthMsg") or root.findtext(".//errMsg") or ""
        err = f"API 오류 {reason} {msg}"
        if reason in FATAL_CODES:
            raise FatalApiError(err)
        raise RuntimeError(err)
    code_ = root.findtext(".//resultCode")
    if code_ not in ("000", "00"):
        raise RuntimeError(f"resultCode {code_} {root.findtext('.//resultMsg')}")
    total = int(root.findtext(".//totalCount") or 0)
    items = [{f: (it.findtext(f) or "").strip() for f in FIELDS} for it in root.iter("item")]
    return items, total


def fetch(session, key, code, ym):
    """시군구×월 전체 페이지. 건수 검증 포함."""
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            items, total = call(session, key, code, ym, 1)
            page = 1
            while len(items) < total:
                page += 1
                more, _ = call(session, key, code, ym, page)
                if not more:
                    break
                items += more
            if len(items) != total:
                raise RuntimeError(f"건수 불일치 {len(items)}/{total}")
            return pd.DataFrame(items, columns=FIELDS)
        except FatalApiError:
            raise
        except Exception as e:
            last = e
            if attempt < RETRIES:
                time.sleep(3 * attempt)
    raise RuntimeError(f"{type(last).__name__}: {last}")


def path_of(code, ym):
    return RAW_DIR / code / f"{ym}.parquet"


def needs_fetch(code, ym, months_back):
    p = path_of(code, ym)
    if not p.exists():
        return True
    age = time.time() - p.stat().st_mtime
    for within, every in REFRESH:
        if months_back < within:
            return age > every
    return False


def save(df, code, ym):
    p = path_of(code, ym)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, p)


def run_probe(session, key, start_ym):
    """과거 거래가 새 코드로 조회되는지 그룹별 확인 → 필요한 개편 전 코드 목록."""
    state = json.loads(PROBE_FILE.read_text()) if PROBE_FILE.exists() else {}
    legacy = []
    for group, (new_code, old_codes) in PROBE.items():
        if group not in state:
            try:
                n = len(fetch(session, key, new_code, start_ym))
                state[group] = {"new_code_has_history": n > 0, "checked": str(date.today())}
                log(f"[확인] {group}: 새 코드 {new_code}로 {start_ym} 거래 {n}건 → "
                    + ("과거 거래도 새 코드로 조회됨" if n else "개편 전 코드로도 수집"))
            except FatalApiError:
                raise
            except Exception as e:
                log(f"[확인] {group}: 확인 실패({e}) → 안전하게 개편 전 코드로도 수집")
                legacy += old_codes
                continue
        if not state[group]["new_code_has_history"]:
            legacy += old_codes
    PROBE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROBE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    return legacy


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser()
    p.add_argument("--months", type=int, default=36)
    p.add_argument("--end", help="마지막 달 YYYYMM (기본: 이번 달)")
    p.add_argument("--sido", nargs="*", help="시도코드 2자리만 수집 (예: 27 11)")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--budget-minutes", type=float, default=300)
    args = p.parse_args()

    load_env()
    key = os.environ.get("DATA_GO_KR_KEY")
    if not key:
        sys.exit("DATA_GO_KR_KEY가 없습니다. .env 파일이나 환경변수로 넣으세요.")

    end = pd.Period(args.end, "M") if args.end else pd.Period(date.today(), "M")
    months = [(end - i).strftime("%Y%m") for i in range(args.months)]  # 최근 달부터
    reg = regions()
    codes = reg["sggcd"].tolist()
    if args.sido:
        codes = [c for c in codes if c[:2] in args.sido]

    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=args.workers * 2,
                                            pool_maxsize=args.workers * 2)
    session.mount("https://", adapter)

    t0 = time.monotonic()
    deadline = t0 + args.budget_minutes * 60
    try:
        legacy = run_probe(session, key, months[-1])
    except FatalApiError as e:
        sys.exit(f"[중단] API 키/한도 문제: {e}")
    if args.sido:
        legacy = [c for c in legacy if c[:2] in args.sido
                  or (c[:2] in ("29", "46") and "12" in args.sido)]

    tasks = []
    for i, ym in enumerate(months):
        for code in codes + legacy:
            if needs_fetch(code, ym, i):
                tasks.append((code, ym))
    total_cells = len(months) * (len(codes) + len(legacy))
    log(f"기간 {months[-1]}~{months[0]} · 시군구 {len(codes)}개 + 개편 전 코드 {len(legacy)}개 · "
        f"전체 {total_cells:,}칸 중 이번에 받을 {len(tasks):,}칸 · 동시 {args.workers} · "
        f"예산 {args.budget_minutes:.0f}분")

    stop = threading.Event()
    reason = []
    lock = threading.Lock()
    stats = {"ok": 0, "fail": 0, "rows": 0, "consec": 0}
    failures = []

    def work(code, ym):
        if stop.is_set() or time.monotonic() > deadline:
            return "skip"
        try:
            df = fetch(session, key, code, ym)
        except FatalApiError as e:
            if not stop.is_set():
                reason.append(f"API 키/한도 문제: {e}")
                stop.set()
            return "fatal"
        except Exception as e:
            with lock:
                stats["fail"] += 1
                stats["consec"] += 1
                failures.append(f"{code} {ym}: {e}")
                if stats["consec"] >= MAX_CONSECUTIVE_FAILS and not stop.is_set():
                    reason.append(f"{stats['consec']}건 연속 실패")
                    stop.set()
            return "fail"
        save(df, code, ym)
        with lock:
            stats["ok"] += 1
            stats["rows"] += len(df)
            stats["consec"] = 0
        return "ok"

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(work, c, m) for c, m in tasks]
        for f in as_completed(futs):
            done += 1
            if done % 200 == 0 or done == len(tasks):
                el = time.monotonic() - t0
                log(f"  {done:,}/{len(tasks):,} · 성공 {stats['ok']:,} · 실패 {stats['fail']} · "
                    f"거래 {stats['rows']:,}건 · {el / 60:.1f}분 경과")
            if time.monotonic() > deadline and not stop.is_set():
                reason.append(f"시간 예산 {args.budget_minutes:.0f}분 소진")
                stop.set()

    have = sum(path_of(c, m).exists() for m in months for c in codes + legacy)
    status = {"updated": pd.Timestamp.now(tz="Asia/Seoul").isoformat(timespec="minutes"),
              "months": [months[-1], months[0]], "cells_total": total_cells, "cells_done": have,
              "complete": have == total_cells, "stopped": reason[0] if reason else None,
              "failed_this_run": len(failures)}
    STATUS_FILE.write_text(json.dumps(status, ensure_ascii=False, indent=1))
    log(f"완료: 이번 실행 성공 {stats['ok']:,} · 실패 {stats['fail']} · 거래 {stats['rows']:,}건 · "
        f"누적 {have:,}/{total_cells:,}칸 ({have / total_cells:.0%})")
    if reason:
        log(f"! 중단 사유: {reason[0]} — 남은 칸은 다음 실행에서 이어받습니다.")
    if failures:
        log("! 실패 예시: " + "; ".join(failures[:10]))
        if os.environ.get("GITHUB_ACTIONS") == "true":
            log(f"::warning::수집 실패 {len(failures)}칸 — 다음 실행에서 재시도")


if __name__ == "__main__":
    main()
