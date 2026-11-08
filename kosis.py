"""KOSIS 외부 요인 수집: 미분양(시군구), 인구 순이동(시군구), 아파트 준공(시도).

사용법:  KOSIS_KEY=... python kosis.py [--months 27]
결과:    data/out/ext_unsold.parquet, ext_migration.parquet, ext_completion.parquet
키는 로그에 남기지 않는다. 실패해도 실거래 집계에는 영향이 없도록 따로 실행한다.
"""
import argparse
import os
import re
import sys
import time

import pandas as pd
import requests

from common import OUT_DIR, load_env
from regions import LEGACY_RENAME, regions

URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
SIDO_BY_NAME = {"서울": "11", "부산": "26", "대구": "27", "인천": "28", "광주": "12", "전남": "12",
                "전남광주": "12", "대전": "30", "울산": "31", "세종": "36", "경기": "41", "강원": "51",
                "충북": "43", "충남": "44", "전북": "52", "경북": "47", "경남": "48", "제주": "50"}


def log(*a):
    print(re.sub(r"(apiKey=)[^&\s]+", r"\1***", " ".join(str(x) for x in a)), flush=True)


def fetch(key, org, tbl, itm, months, **objs):
    params = {"method": "getList", "apiKey": key, "orgId": org, "tblId": tbl, "itmId": itm,
              "prdSe": "M", "newEstPrdCnt": months, "format": "json", "jsonVD": "Y", **objs}
    for attempt in range(3):
        try:
            r = requests.get(URL, params=params, timeout=90)
            data = r.json()
            if isinstance(data, dict):
                raise RuntimeError(f"KOSIS 오류 {data.get('err')} {data.get('errMsg')}")
            df = pd.DataFrame(data)
            df["DT"] = pd.to_numeric(df["DT"], errors="coerce")
            df["월"] = pd.to_datetime(df["PRD_DE"], format="%Y%m")
            return df
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                raise
            log(f"  재시도 {attempt + 1}: {e}")
            time.sleep(8)  # 분당 호출 제한 대비


def norm(s):
    return re.sub(r"\s+", "", str(s))


def name_index():
    """(시도코드, 시군구명) → [현행 시군구코드]. '수원시'처럼 시 이름이면 하위 구 전체."""
    reg = regions()
    idx = {}
    for r in reg.itertuples():
        idx.setdefault((r.sidocd, norm(r.sggnm)), []).append(r.sggcd)
        m = re.match(r"^(.+?시)(.+구)$", norm(r.sggnm))  # 수원시장안구 → 수원시
        if m:
            idx.setdefault((r.sidocd, m.group(1)), []).append(r.sggcd)
    return idx


def code_children():
    """KOSIS 시 단위 코드(예 41110 수원시) → 현행 구 코드들. 개편 전 코드는 이름 그대로 이어지는 것만."""
    cur = set(regions()["sggcd"])
    def resolve(c):
        if c in cur:
            return [c]
        if c in LEGACY_RENAME:
            return [LEGACY_RENAME[c]]
        kids = [x for x in cur if x[:4] == c[:4] and x != c] if c.endswith("0") else []
        return kids
    return resolve


def unsold(key, months):
    # 한 번에 4만 칸 제한 → 시도(구분 19개)별로 나눠 요청
    parts = []
    for i in range(1, 20):
        try:
            parts.append(fetch(key, "116", "DT_MLTM_2082", "13103871087T1", months,
                               objL1=f"13102871087A.{i:04d}", objL2="ALL"))
        except RuntimeError as e:
            if "30" not in str(e):  # 30 = 데이터 없음(예: 통합 전/후 시도) → 건너뜀
                raise
        time.sleep(1.5)
    df = pd.concat(parts, ignore_index=True)
    idx = name_index()
    rows, sido_rows, miss = [], [], set()
    for r in df.itertuples():
        sname, nm = norm(r.C1_NM), norm(r.C2_NM)
        if nm in ("계", "소계", "총계"):
            sido_rows.append((sname, r.월, r.DT))
            continue
        sido = SIDO_BY_NAME.get(sname)
        codes = idx.get((sido, nm)) if sido else None
        if not codes:
            miss.add(f"{r.C1_NM} {r.C2_NM}")
            continue
        for c in codes:
            rows.append((c, r.월, r.DT, len(codes) > 1))
    sgg = (pd.DataFrame(rows, columns=["코드", "월", "미분양", "시단위"])
           .groupby(["코드", "월"], as_index=False).agg(미분양=("미분양", "max"), 시단위=("시단위", "max")))
    if miss:
        log(f"  미분양 지역명 미대응 {len(miss)}개(개편 전 이름 등): {sorted(miss)[:15]}")
    return pd.concat([sgg, sido_totals(sido_rows, "미분양")], ignore_index=True)


def sido_totals(rows, col):
    """시도 합계. 전남광주가 없는 달(통합 전)은 광주+전남을 합친다."""
    t = pd.DataFrame(rows, columns=["이름", "월", col])
    out = []
    for (nm, m), v in t.groupby(["이름", "월"])[col].max().items():
        code = "00" if nm in ("전국", "총계") else SIDO_BY_NAME.get(nm)
        if code and nm not in ("광주", "전남", "29", "46"):
            out.append((code, m, v))
    have12 = {m for c, m, _ in out if c == "12"}
    old = t[t["이름"].isin(["광주", "전남", "29", "46"]) & ~t["월"].isin(have12)]
    for m, v in old.groupby("월")[col].sum().items():
        out.append(("12", m, v))
    return pd.DataFrame(out, columns=["코드", "월", col]).assign(시단위=False)


def migration(key, months):
    df = fetch(key, "101", "DT_1B26001_A01", "T10+T20+T25+", months, objL1="ALL")
    df["C1"] = df["C1"].astype(str)
    resolve = code_children()
    names = {"T10": "전입", "T20": "전출", "T25": "순이동"}
    parts = []
    for itm, col in names.items():
        d = df[df["ITM_ID"] == itm]
        rows, sido_rows = [], []
        for r in d.itertuples():
            if len(r.C1) <= 2:
                sido_rows.append(("전국" if r.C1 == "00" else r.C1, r.월, r.DT))
                continue
            targets = resolve(r.C1)
            for t in targets:
                rows.append((t, r.월, r.DT, len(targets) > 1))
        sgg = (pd.DataFrame(rows, columns=["코드", "월", col, "시단위"])
               .groupby(["코드", "월"], as_index=False).agg(**{col: (col, "max"), "시단위": ("시단위", "max")}))
        sd = pd.DataFrame(sido_rows, columns=["이름", "월", col])
        sd["이름"] = sd["이름"].map(lambda c: c if c in ("전국", "29", "46") else
                                  {v: k for k, v in SIDO_BY_NAME.items() if k not in ("광주", "전남")}.get(c, c))
        parts.append(pd.concat([sgg, sido_totals(list(sd.itertuples(index=False)), col)], ignore_index=True)
                     .set_index(["코드", "월"]))
    out = parts[0].join(parts[1].drop(columns="시단위"), how="outer").join(
        parts[2].drop(columns="시단위"), how="outer").reset_index()
    out["시단위"] = out["시단위"].fillna(False).astype(bool)
    return out


def completion(key, months):
    df = fetch(key, "116", "DT_MLTM_5373", "13103766973T1", months, objL1="ALL",
               objL2="13102766973B.0006", objL3="13102766973C.0007", objL4="13102766973D.0008")
    rows = [(norm(r.C1_NM), r.월, r.DT) for r in df.itertuples()]
    return sido_totals(rows, "아파트준공").drop(columns="시단위")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--months", type=int, default=27)
    args = p.parse_args()
    load_env()
    key = os.environ.get("KOSIS_KEY")
    if not key:
        sys.exit("KOSIS_KEY가 없습니다.")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    for name, fn in [("unsold", unsold), ("migration", migration), ("completion", completion)]:
        try:
            df = fn(key, args.months)
            df.to_parquet(OUT_DIR / f"ext_{name}.parquet", index=False)
            log(f"[{name}] {len(df):,}행 · 지역 {df['코드'].nunique()}개 · "
                f"{df['월'].min():%Y.%m}~{df['월'].max():%Y.%m}")
            log(df.sort_values(["코드", "월"]).groupby("코드").tail(1).head(6).to_string(index=False))
            ok += 1
        except Exception as e:  # noqa: BLE001
            log(f"[{name}] 실패: {e}")
        time.sleep(3)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
