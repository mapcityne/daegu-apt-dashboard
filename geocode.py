"""3단계 준비: 단지별 지번 주소 → 위경도 (카카오 로컬 API).

사용법
    python geocode.py

- 1단계 결과(data/daegu_apt_trades.csv)의 단지 목록을 좌표로 바꿔 data/complex_coords.csv에 저장.
- 이미 변환한 단지는 건너뛰므로, 새 단지가 생긴 뒤 다시 실행하면 추가분만 변환한다.
- 1차: 지번 주소 검색, 실패 시 2차: "구군 단지명 아파트" 키워드 검색.
"""
import os
import sys
import time

import pandas as pd
import requests

from common import COORDS_CSV, TRADES_CSV, load_env

ADDR_URL = "https://dapi.kakao.com/v2/local/search/address.json"
KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"


def search(session, url, query):
    r = session.get(url, params={"query": query, "size": 1}, timeout=10)
    if r.status_code == 401:
        sys.exit("카카오 REST API 키가 올바르지 않습니다 (401).")
    r.raise_for_status()
    docs = r.json().get("documents", [])
    if docs:
        return float(docs[0]["y"]), float(docs[0]["x"])
    return None


def main():
    load_env()
    key = os.environ.get("KAKAO_REST_KEY")
    if not key:
        sys.exit("KAKAO_REST_KEY가 없습니다. .env 파일에 KAKAO_REST_KEY=REST API 키 를 넣으세요.")
    if not TRADES_CSV.exists():
        sys.exit("먼저 python collect.py 로 거래 데이터를 수집하세요.")

    trades = pd.read_csv(TRADES_CSV, dtype={"지번": str, "단지일련번호": str})
    complexes = (trades.sort_values("계약일자")
                 .groupby("단지키", as_index=False)
                 .agg(구군=("구군", "last"), 법정동=("법정동", "last"),
                      지번=("지번", "last"), 단지명=("단지명", "last")))

    done = pd.read_csv(COORDS_CSV, dtype=str) if COORDS_CSV.exists() else pd.DataFrame(columns=["단지키"])
    todo = complexes[~complexes["단지키"].isin(done["단지키"])]
    print(f"단지 {len(complexes):,}개 중 신규 {len(todo):,}개 변환")

    s = requests.Session()
    s.headers["Authorization"] = f"KakaoAK {key}"
    rows = []
    for i, r in enumerate(todo.itertuples(index=False), 1):
        jibun = str(r.지번) if pd.notna(r.지번) else ""
        hit = search(s, ADDR_URL, f"대구광역시 {r.구군} {r.법정동} {jibun}".strip()) if jibun else None
        how = "주소"
        if hit is None:
            hit = search(s, KEYWORD_URL, f"대구 {r.구군} {r.단지명} 아파트")
            how = "키워드"
        if hit is None:
            how = "실패"
        rows.append({"단지키": r.단지키, "단지명": r.단지명, "구군": r.구군,
                     "lat": hit[0] if hit else None, "lon": hit[1] if hit else None, "방식": how})
        if i % 100 == 0:
            print(f"  {i}/{len(todo)}")
        time.sleep(0.05)

    out = pd.concat([done, pd.DataFrame(rows)], ignore_index=True)
    out.to_csv(COORDS_CSV, index=False, encoding="utf-8-sig")
    fail = (out["방식"] == "실패").sum()
    print(f"저장: {COORDS_CSV} (실패 {fail}개 — 지도에서 제외됨)")


if __name__ == "__main__":
    main()
