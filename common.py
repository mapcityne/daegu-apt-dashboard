"""공통 설정: 경로, 평형 구간, 전처리 함수."""
import os
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("APT_DATA_DIR", BASE_DIR / "data"))
RAW_DIR = DATA_DIR / "raw"          # 시군구코드/YYYYMM.parquet 단위 수집 캐시
OUT_DIR = DATA_DIR / "out"          # 앱이 읽는 결과물 (GitHub Release 'data'에 업로드)
SAMPLE_DIR = BASE_DIR / "sample"    # 화면 확인용 가상 데이터

PYEONG = 3.305785  # 1평(㎡)

# 전용면적 기준 평형대 구간 (㎡)
AREA_BINS = [0, 40, 60, 85, 102, 135, 10_000]
AREA_LABELS = ["40㎡ 미만", "40~60㎡ (소형)", "60~85㎡ (국민평형)",
               "85~102㎡", "102~135㎡", "135㎡ 이상"]

# 결과 파일에 남기는 컬럼 (파생값은 앱에서 계산)
STORE_COLUMNS = ["시도코드", "시군구코드", "시군구", "법정동", "단지명", "지번", "단지일련번호",
                 "단지키", "계약일자", "거래금액", "전용면적", "층", "건축년도", "거래유형", "해제여부"]


def load_env():
    """같은 폴더의 .env 파일(KEY=VALUE)을 환경변수로 읽는다."""
    env = BASE_DIR / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def clean_raw(df: pd.DataFrame) -> pd.DataFrame:
    """PublicDataReader 결과(한글 컬럼)를 저장용으로 정리한다. 시군구코드 재배정 전 단계."""
    df = df.copy()
    df["원코드"] = df["법정동시군구코드"].astype(str).str.zfill(5)
    for c in ["법정동", "단지명", "지번", "해제여부", "거래유형", "단지일련번호"]:
        if c not in df.columns:
            df[c] = ""
        df[c] = df[c].fillna("").astype(str).str.strip().replace({"nan": "", "None": ""})
    for c in ["계약년도", "계약월", "계약일", "거래금액", "층", "건축년도", "전용면적"]:
        df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ""), errors="coerce")
    df["계약일자"] = pd.to_datetime(
        dict(year=df["계약년도"], month=df["계약월"], day=df["계약일"]), errors="coerce")
    return df.dropna(subset=["계약일자", "거래금액", "전용면적"])


def finalize(df: pd.DataFrame) -> pd.DataFrame:
    """시군구코드 확정 후 단지키를 만들고 저장 컬럼만 남긴다."""
    fallback = df["시군구코드"] + "|" + df["법정동"] + "|" + df["지번"] + "|" + df["단지명"]
    seq = df["단지일련번호"]
    df["단지키"] = seq.where(seq.str.len() > 0, fallback)
    df["층"] = df["층"].astype("Int16")
    df["건축년도"] = df["건축년도"].astype("Int16")
    df["거래금액"] = df["거래금액"].astype("int64")
    return df[STORE_COLUMNS].sort_values("계약일자").reset_index(drop=True)


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """저장된 거래에 분석용 파생 컬럼을 붙인다."""
    df = df.copy()
    df["계약일자"] = pd.to_datetime(df["계약일자"])
    df["계약월"] = df["계약일자"].dt.to_period("M").dt.to_timestamp()
    df["거래금액_억"] = df["거래금액"] / 10_000
    df["평당가"] = (df["거래금액"] / (df["전용면적"] / PYEONG)).round()
    df["평형대"] = pd.cut(df["전용면적"], AREA_BINS, labels=AREA_LABELS, right=False)
    df["해제"] = df["해제여부"].eq("O")
    return df
