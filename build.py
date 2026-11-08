"""수집 캐시(data/raw) → 앱용 결과물(data/out).

만드는 것
- trades_<시도코드>.parquet : 시도별 거래 (지역 상세 화면용)
- stats.parquet             : 전국·시도·시군구별 방향 판정과 브리핑 수치
- index.parquet             : 지역별 월간 동일단지 가격지수
- dong.parquet              : 시군구별 법정동 비교표
- nat_risers / nat_top_trades / nat_new_highs.parquet : 전국 화면용 목록
- meta.json                 : 기준월, 데이터 기간, 수집 진행 상태
"""
import json
import sys
import time

import numpy as np
import pandas as pd

import analysis as A
from common import OUT_DIR, RAW_DIR, clean_raw, enrich, finalize
from regions import LEGACY_RENAME, LEGACY_SPLIT, SIDO_SHORT, SPLIT_FALLBACK_DONG, regions

RENAME = {"sggCd": "법정동시군구코드", "umdNm": "법정동", "aptNm": "단지명", "jibun": "지번",
          "excluUseAr": "전용면적", "dealYear": "계약년도", "dealMonth": "계약월", "dealDay": "계약일",
          "dealAmount": "거래금액", "floor": "층", "buildYear": "건축년도", "aptSeq": "단지일련번호",
          "cdealType": "해제여부", "dealingGbn": "거래유형"}
MIN_MONTH_N = {"전국": 100, "시도": 30, "시군구": 10}


def log(*a):
    print(*a, flush=True)


def load_raw() -> pd.DataFrame:
    frames = []
    for d in sorted(p for p in RAW_DIR.iterdir() if p.is_dir()):
        for f in d.glob("*.parquet"):
            x = pd.read_parquet(f)
            if len(x):
                x["조회코드"] = d.name
                frames.append(x)
    if not frames:
        sys.exit("수집된 거래가 없습니다. 먼저 collect.py를 실행하세요.")
    return pd.concat(frames, ignore_index=True).rename(columns=RENAME)


def assign_region(df: pd.DataFrame) -> pd.DataFrame:
    """조회 코드 → 2026.10 기준 시군구코드. 나뉜 구는 법정동으로 고른다."""
    cur = set(regions()["sggcd"])
    code = df["조회코드"].copy()
    code = code.where(~code.isin(LEGACY_RENAME.keys()), code.map(LEGACY_RENAME))
    for old, cands in LEGACY_SPLIT.items():
        m = code == old
        if not m.any():
            continue
        learned = (df[df["조회코드"].isin(cands)].groupby("법정동")["조회코드"]
                   .agg(lambda s: s.mode().iat[0]))
        fallback = {d: c for c, ds in SPLIT_FALLBACK_DONG.items() if c in cands for d in ds}
        default = cands[0]
        code[m] = df.loc[m, "법정동"].map(lambda d: learned.get(d) or fallback.get(d) or default)
        log(f"  {old} → {cands}: {m.sum():,}건 재배정 (법정동 학습 {len(learned)}개)")
    df["시군구코드"] = code
    df["현행조회"] = df["조회코드"].isin(cur)
    unknown = ~df["시군구코드"].isin(cur)
    if unknown.any():
        log(f"  ! 지역 미확인 {unknown.sum()}건 제외: {df.loc[unknown, '조회코드'].unique()[:10]}")
    return df[~unknown]


def build_trades() -> pd.DataFrame:
    t0 = time.time()
    raw = load_raw()
    log(f"원자료 {len(raw):,}건 읽음 ({time.time() - t0:.0f}초)")
    df = clean_raw(raw)
    df = assign_region(df)
    # 같은 거래가 새 코드·옛 코드로 두 번 잡힌 경우만 옛 코드 쪽을 버린다.
    # (같은 출처 안의 동일 행은 '해제 후 재신고'나 실제 동시 거래이므로 지우지 않는다)
    key = ["시군구코드", "단지일련번호", "단지명", "지번", "법정동", "계약일자", "거래금액",
           "전용면적", "층", "해제여부"]
    cur, old = df[df["현행조회"]], df[~df["현행조회"]]
    if len(old):
        seen = pd.MultiIndex.from_frame(cur[key].astype(str))
        dup = pd.MultiIndex.from_frame(old[key].astype(str)).isin(seen)
        # 해제 여부만 다른 경우(옛 코드 자료가 해제 반영 전)도 같은 거래로 본다
        key2 = key[:-1]
        seen2 = pd.MultiIndex.from_frame(cur[key2].astype(str))
        dup |= pd.MultiIndex.from_frame(old[key2].astype(str)).isin(seen2)
        log(f"  옛 코드 조회분 {len(old):,}건 중 새 코드와 겹치는 {dup.sum():,}건 제거")
        df = pd.concat([cur, old[~dup]], ignore_index=True)
    reg = regions().set_index("sggcd")
    df["시도코드"] = df["시군구코드"].str[:2]
    df["시군구"] = df["시군구코드"].map(reg["sggnm"])
    return finalize(df)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    trades = build_trades()
    for sido, g in trades.groupby("시도코드"):
        g.to_parquet(OUT_DIR / f"trades_{sido}.parquet", index=False, compression="zstd")
    log(f"시도별 거래 저장: {len(trades):,}건, {trades['시도코드'].nunique()}개 시도")

    df = A.prepare(enrich(trades))
    base_all = A.analysis_base(df)
    ref = A.ref_month(base_all)
    raw = df[df["계약월"] <= ref]
    base = A.mark_new_highs(base_all[base_all["계약월"] <= ref])
    pc = A.price_changes(base, ref)
    log(f"기준월 {ref:%Y.%m} · 분석 대상 {len(base):,}건 · 비교 가능 평형 {pc['1년전대비'].notna().sum():,}개")

    reg = regions()
    units = [("00", "전국", "전국", "")]
    units += [(c, "시도", SIDO_SHORT[c], "00") for c in sorted(reg["sidocd"].unique())]
    units += [(r.sggcd, "시군구", r.지역명, r.sidocd) for r in reg.itertuples()]

    by_sido = {k: v for k, v in base.groupby("시도코드")}
    by_sgg = {k: v for k, v in base.groupby("시군구코드")}
    raw_sido = {k: v for k, v in raw.groupby("시도코드")}
    raw_sgg = {k: v for k, v in raw.groupby("시군구코드")}
    pc_sido = {k: v for k, v in pc.groupby("시도코드")}
    pc_sgg = {k: v for k, v in pc.groupby("시군구코드")}
    empty = base.iloc[0:0]

    def ext(name):
        f = OUT_DIR / f"ext_{name}.parquet"
        return pd.read_parquet(f) if f.exists() else None
    unsold, mig, comp = ext("unsold"), ext("migration"), ext("completion")
    log(f"외부 요인: 미분양 {'O' if unsold is not None else 'X'} · 인구이동 {'O' if mig is not None else 'X'} · "
        f"준공 {'O' if comp is not None else 'X'}")

    stats_rows, index_rows = [], []
    for code, level, name, parent in units:
        if level == "전국":
            b, r, p = base, raw, pc
        elif level == "시도":
            b, r, p = by_sido.get(code, empty), raw_sido.get(code, raw.iloc[0:0]), \
                pc_sido.get(code, pc.iloc[0:0])
        else:
            b, r, p = by_sgg.get(code, empty), raw_sgg.get(code, raw.iloc[0:0]), \
                pc_sgg.get(code, pc.iloc[0:0])
        idx = A.price_index(b, min_month_n=MIN_MONTH_N[level]) if len(b) else \
            pd.DataFrame(columns=["지수", "거래수"])
        st = A.region_stats(b, r, p, idx, ref) if len(b) else {"판정": "판단 보류"}
        ex = A.ext_stats(code, unsold, mig, None, ref)
        # 준공은 시도 단위 통계 → 시군구는 소속 시도 값
        ex.update(A.ext_stats(code if level != "시군구" else parent, None, None, comp, ref))
        stats_rows.append({"코드": code, "단계": level, "이름": name, "상위": parent, **st, **ex})
        if len(idx):
            index_rows.append(idx.reset_index().assign(코드=code))
    log(f"지역 통계 {len(stats_rows)}개 ({time.time() - t0:.0f}초)")

    stats = pd.DataFrame(stats_rows)
    for c in stats.columns:  # 중첩 값은 JSON 문자열로
        if stats[c].map(lambda v: isinstance(v, (dict, list))).any():
            stats[c] = stats[c].map(lambda v: json.dumps(v, ensure_ascii=False)
                                      if isinstance(v, (dict, list)) else None)
    stats.to_parquet(OUT_DIR / "stats.parquet", index=False)
    pd.concat(index_rows).to_parquet(OUT_DIR / "index.parquet", index=False)

    dong = [A.child_table(g, pc_sgg.get(k, pc.iloc[0:0]), ref, "법정동").assign(시군구코드=k)
            for k, g in by_sgg.items()]
    pd.concat(dong).to_parquet(OUT_DIR / "dong.parquet", index=False)

    # 전국 화면용 목록
    cols = ["단지명", "시도코드", "시군구코드", "시군구", "법정동", "면적타입", "건축년도",
            "최근", "최근_건수", "6개월전", "6개월전_건수", "6개월전대비", "1년전", "1년전_건수", "1년전대비"]
    pc.dropna(subset=["1년전대비"])[cols].to_parquet(OUT_DIR / "nat_risers.parquet", index=False)
    y = A.window(base, ref, 0, span=12)
    tops = (y.sort_values("거래금액", ascending=False).drop_duplicates("단지키").head(300)
            [["단지명", "시도코드", "시군구", "법정동", "전용면적", "층", "거래금액_억", "평당가", "계약일자"]])
    tops.to_parquet(OUT_DIR / "nat_top_trades.parquet", index=False)
    nh = A.window(base, ref, 0)
    nh = nh[nh["신고가"]][["단지명", "시도코드", "시군구", "법정동", "면적타입", "층",
                         "직전최고가", "거래금액", "계약일자"]]
    nh.to_parquet(OUT_DIR / "nat_new_highs.parquet", index=False)

    status = {}
    sf = RAW_DIR / "_status.json"
    if sf.exists():
        status = json.loads(sf.read_text())
    meta = {"기준월": f"{ref:%Y-%m}", "데이터_시작": f"{trades['계약일자'].min():%Y-%m-%d}",
            "데이터_끝": f"{trades['계약일자'].max():%Y-%m-%d}", "거래수": int(len(trades)),
            "생성": pd.Timestamp.now(tz="Asia/Seoul").isoformat(timespec="minutes"),
            "수집": status}
    (OUT_DIR / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    log(f"완료 ({time.time() - t0:.0f}초): {json.dumps(meta, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
