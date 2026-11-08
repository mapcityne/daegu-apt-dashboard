"""공통 설정: 경로, 대구 구·군 코드, 전처리 함수."""
from pathlib import Path
import os

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"                      # 구·군×월 단위 캐시
TRADES_CSV = DATA_DIR / "daegu_apt_trades.csv"  # 1단계 결과
COORDS_CSV = DATA_DIR / "complex_coords.csv"    # 3단계 좌표 캐시
SAMPLE_DIR = BASE_DIR / "sample"                # 검증용 가상 데이터

# 대구광역시 구·군 (법정동코드 앞 5자리). 2023.7. 군위군 편입으로 9개.
DAEGU_SIGUNGU = {
    "27110": "중구",
    "27140": "동구",
    "27170": "서구",
    "27200": "남구",
    "27230": "북구",
    "27260": "수성구",
    "27290": "달서구",
    "27710": "달성군",
    "27720": "군위군",
}

PYEONG = 3.305785  # 1평(㎡)

# 전용면적 기준 평형대 구간 (㎡)
AREA_BINS = [0, 40, 60, 85, 102, 135, 10_000]
AREA_LABELS = ["40㎡ 미만", "40~60㎡ (소형)", "60~85㎡ (국민평형)",
               "85~102㎡", "102~135㎡", "135㎡ 이상"]


def load_env():
    """같은 폴더의 .env 파일(KEY=VALUE)을 환경변수로 읽는다."""
    env = BASE_DIR / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def clean_trades(df: pd.DataFrame) -> pd.DataFrame:
    """PublicDataReader 결과(한글 컬럼)를 분석용으로 정리한다."""
    df = df.copy()
    df["법정동시군구코드"] = df["법정동시군구코드"].astype(str).str.zfill(5)
    df["구군"] = df["법정동시군구코드"].map(DAEGU_SIGUNGU)
    for c in ["법정동", "단지명", "지번", "해제여부", "거래유형", "단지일련번호"]:
        if c in df.columns:
            df[c] = df[c].fillna("").astype(str).str.strip().replace({"nan": "", "None": ""})

    df["계약일자"] = pd.to_datetime(
        dict(year=df["계약년도"], month=df["계약월"], day=df["계약일"]), errors="coerce")
    df["계약월"] = df["계약일자"].dt.to_period("M").dt.to_timestamp()
    df["거래금액"] = pd.to_numeric(df["거래금액"], errors="coerce")          # 만원
    df["전용면적"] = pd.to_numeric(df["전용면적"], errors="coerce")          # ㎡
    df["거래금액_억"] = df["거래금액"] / 10_000
    df["평당가"] = df["거래금액"] / (df["전용면적"] / PYEONG)                # 만원/전용평
    df["평형대"] = pd.cut(df["전용면적"], AREA_BINS, labels=AREA_LABELS, right=False)
    df["해제"] = df["해제여부"].eq("O")

    # 단지 식별키: 단지일련번호가 있으면 사용, 없으면 구군+법정동+지번+단지명
    fallback = df["구군"] + "|" + df["법정동"] + "|" + df["지번"] + "|" + df["단지명"]
    seq = df["단지일련번호"] if "단지일련번호" in df.columns else ""
    df["단지키"] = seq.where(seq.astype(str).str.len() > 0, fallback)

    df = df.dropna(subset=["계약일자", "거래금액", "전용면적"])
    return df.sort_values("계약일자").reset_index(drop=True)
