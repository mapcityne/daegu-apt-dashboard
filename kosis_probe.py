"""KOSIS 통계표 점검 (GitHub Actions 수동 실행용). 키는 출력하지 않는다.

시군구 단위 미분양·인구이동·주택 준공 통계표를 검색하고, 후보 표의 항목·분류·최신 시점을 출력한다.
"""
import json
import os
import re
import sys

import requests

KEY = os.environ.get("KOSIS_KEY")
BASE = "https://kosis.kr/openapi"
SEARCH = ["시군구별 미분양", "미분양주택현황", "시군구별 이동자수", "주택건설 준공실적", "준공실적 시군구"]
CANDIDATES = [("101", "DT_1YL202001E"), ("116", "DT_MLTM_2082"), ("116", "DT_MLTM_2080"),
              ("101", "DT_1B26001_A01"), ("116", "DT_MLTM_5373"), ("116", "DT_MLTM_666")]


def log(*a):
    print(re.sub(r"(apiKey=)[^&\s]+", r"\1***", " ".join(str(x) for x in a)), flush=True)


def get(path, **params):
    r = requests.get(f"{BASE}/{path}", params={"apiKey": KEY, "format": "json", "jsonVD": "Y", **params},
                     timeout=60)
    try:
        return r.json()
    except ValueError:
        return {"raw": r.text[:300]}


def main():
    if not KEY:
        sys.exit("KOSIS_KEY 없음")
    for q in SEARCH:
        res = get("statisticsSearch.do", method="getList", searchNm=q, resultCount=15)
        log(f"\n## 검색: {q}")
        if isinstance(res, list):
            for t in res:
                log(f"  {t.get('ORG_ID')}/{t.get('TBL_ID')} | {t.get('TBL_NM')} | "
                    f"{t.get('STRT_PRD_DE')}~{t.get('END_PRD_DE')} | {t.get('ORG_NM', '')}")
        else:
            log("  응답:", json.dumps(res, ensure_ascii=False)[:300])

    for org, tbl in CANDIDATES:
        log(f"\n## 메타: {org}/{tbl}")
        tblm = get("statisticsData.do", method="getMeta", type="TBL", orgId=org, tblId=tbl)
        log("  표:", json.dumps(tblm, ensure_ascii=False)[:300])
        prd = get("statisticsData.do", method="getMeta", type="PRD", orgId=org, tblId=tbl)
        log("  주기·기간:", json.dumps(prd, ensure_ascii=False)[:400])
        itm = get("statisticsData.do", method="getMeta", type="ITM", orgId=org, tblId=tbl)
        if isinstance(itm, list):
            objs = {}
            for x in itm:
                objs.setdefault((x.get("OBJ_ID"), x.get("OBJ_NM")), []).append(
                    f"{x.get('ITM_ID')}:{x.get('ITM_NM')}")
            for (oid, onm), vals in objs.items():
                log(f"  [{oid}] {onm} ({len(vals)}개): {', '.join(vals[:40])}")
        else:
            log("  항목 응답:", json.dumps(itm, ensure_ascii=False)[:300])


if __name__ == "__main__":
    main()
