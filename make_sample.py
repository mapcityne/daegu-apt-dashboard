"""검증용 가상 데이터 생성 (실제 거래 아님).

API 키 없이 대시보드 화면을 확인할 때만 쓴다. 결과는 sample/ 폴더에 저장되며,
app.py는 data/ 에 실제 데이터가 없을 때만 이 샘플을 읽고 화면 상단에 경고를 표시한다.
"""
import numpy as np
import pandas as pd

from common import DAEGU_SIGUNGU, SAMPLE_DIR, clean_trades

rng = np.random.default_rng(7)

# 구·군별 대략적 중심 좌표와 가상 기준 평당가(만원)
CENTER = {
    "중구": (35.866, 128.594, 1900), "동구": (35.886, 128.636, 1300),
    "서구": (35.872, 128.559, 1050), "남구": (35.846, 128.597, 1250),
    "북구": (35.886, 128.583, 1250), "수성구": (35.858, 128.631, 2300),
    "달서구": (35.830, 128.533, 1350), "달성군": (35.775, 128.431, 1000),
    "군위군": (36.243, 128.573, 600),
}
DONGS = ["가동", "나동", "다동", "라동", "마동"]


def main():
    rows, coords = [], []
    months = pd.period_range("2023-11", "2026-10", freq="M")
    code_of = {v: k for k, v in DAEGU_SIGUNGU.items()}
    for gu, (lat, lon, base) in CENTER.items():
        n_cx = 6 if gu == "군위군" else 40
        for j in range(n_cx):
            name = f"샘플{gu}{j + 1:02d}단지"
            dong = gu[:-1] + rng.choice(DONGS)
            jibun = f"{rng.integers(1, 1500)}"
            premium = rng.normal(1.0, 0.18)
            build = int(rng.integers(1990, 2024))
            areas = rng.choice([59.9, 74.8, 84.9, 101.9, 114.7, 134.8], size=3, replace=False)
            clat, clon = lat + rng.normal(0, 0.018), lon + rng.normal(0, 0.022)
            coords.append({"단지키": f"{code_of[gu]}-S{j:03d}", "단지명": name, "구군": gu,
                           "lat": clat, "lon": clon, "방식": "샘플"})
            for t, m in enumerate(months):
                # 가상 시세 흐름: 완만한 하락 후 2025년부터 반등
                trend = 1 - 0.004 * min(t, 16) + 0.006 * max(0, t - 16)
                for _ in range(rng.poisson(0.9 if gu != "군위군" else 0.3)):
                    a = float(rng.choice(areas))
                    price = base * premium * trend * (a / 3.305785) * rng.normal(1, 0.06)
                    rows.append({
                        "법정동시군구코드": code_of[gu], "법정동": dong, "단지명": name,
                        "지번": jibun, "전용면적": a, "계약년도": m.year, "계약월": m.month,
                        "계약일": int(rng.integers(1, 29)), "거래금액": int(round(price, -2)),
                        "층": int(rng.integers(1, 25)), "건축년도": build,
                        "단지일련번호": f"{code_of[gu]}-S{j:03d}",
                        "해제여부": "O" if rng.random() < 0.04 else "",
                        "거래유형": "직거래" if rng.random() < 0.08 else "중개거래",
                    })
    SAMPLE_DIR.mkdir(exist_ok=True)
    df = clean_trades(pd.DataFrame(rows))
    df.to_csv(SAMPLE_DIR / "daegu_apt_trades.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(coords).to_csv(SAMPLE_DIR / "complex_coords.csv", index=False, encoding="utf-8-sig")
    print(f"샘플 {len(df):,}건, 단지 {len(coords)}개 생성")


if __name__ == "__main__":
    main()
