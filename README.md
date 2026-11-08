# 대구 아파트 실거래가 대시보드

대구 9개 구·군 아파트 매매 실거래가를 수집해 구·단지별 추이와 지도를 한 화면에서 본다.
(2023.7. 군위군 편입으로 대구는 7구 2군, 총 9개 구·군)

## 파일 구성

| 파일 | 역할 |
|---|---|
| `collect.py` | 1단계. 국토부 API 수집 → `data/daegu_apt_trades.csv` |
| `geocode.py` | 3단계 준비. 단지 지번 주소 → 위경도 (`data/complex_coords.csv`) |
| `app.py` | 2·3단계. Streamlit 대시보드 |
| `mapview.py` | 지도(folium) 생성 |
| `common.py` | 구·군 코드, 평형대 구간, 전처리 |
| `make_sample.py` | 키 없이 화면 확인용 **가상** 데이터 생성 |

## 준비

1. 공공데이터포털에서 **「국토교통부_아파트 매매 실거래가 상세 자료」** 활용신청
   - PublicDataReader 1.1.x는 상세 자료 API(`RTMSDataSvcAptTradeDev`)를 호출한다.
     일반 「아파트 매매 실거래가 자료」만 신청하면 키 미등록 오류가 난다.
   - 승인 직후 1~2시간은 키가 동작하지 않을 수 있다.
2. 카카오 개발자센터 → 애플리케이션 추가 → **REST API 키** 확인 (지도 좌표용, 무료)
3. `.env.example`을 `.env`로 복사하고 두 키 입력

```bash
pip install -r requirements.txt
```

## 실행 순서

```bash
python collect.py        # 최근 36개월, 9×36=324회 호출, 수 분 소요
python geocode.py        # 단지 수만큼 호출, 새 단지만 추가 변환
streamlit run app.py     # 브라우저에서 http://localhost:8501
```

- 매월 갱신: `collect.py` → `geocode.py` 다시 실행. 이미 받은 달은 건너뛰고, 최근 2개월만 새로 받는다(신고 지연·해제 반영).
- 키 없이 화면만 보려면: `python make_sample.py` → `streamlit run app.py` (상단에 샘플 경고 표시)

## 서비스 배포 (GitHub + Streamlit Community Cloud)

1. **데이터 자동 갱신**: 저장소 Settings → Secrets and variables → Actions에 `DATA_GO_KR_KEY`, `KAKAO_REST_KEY` 등록 → Actions 탭 → `update-data` → Run workflow.
   이후 매월 2일·16일 자동 수집 후 `data/` CSV를 커밋한다.
2. **웹 배포**: share.streamlit.io → GitHub 로그인 → Create app → 저장소 선택, Main file `app.py` → Deploy.
   앱은 `data/` CSV만 읽으므로 Streamlit 쪽에는 키가 필요 없다. 데이터 커밋 시 앱이 자동 재배포된다.
3. 첫 수집 전에는 샘플 데이터(경고 표시)로 뜬다.

## 화면 구성

- **필터(사이드바)**: 구·군, 평형대(전용면적), 기간, 가격 기준(평당가/거래금액), 3개월 이동평균, 해제 거래·직거래 제외
- **상단 지표**: 거래 건수, 중위 거래금액, 중위 평당가, 최근 3개월 평당가와 전년 동기 대비 증감
- **단지 지도**: 점 크기 = 거래 수, 색 = 중위 평당가. 점 클릭 시 팝업(최근 거래가)과 아래 단지 상세 연동
- **구·군별 추이**: 월별 중위 가격 선 그래프 + 월별 거래량
- **단지 상세**: 거래 산점도(평형대별 색) + 월 중위선, 평형대별 요약, 최근 거래 30건

## 기준·유의사항

- 평당가 = 거래금액 ÷ (전용면적 ÷ 3.3058). 분양면적 기준 시세와 다르다.
- 평형대: 40㎡ 미만 / 40~60 / 60~85(국민평형) / 85~102 / 102~135 / 135㎡ 이상
- 최근 1~2개월은 신고 기한(계약 후 30일) 때문에 건수가 적게 잡힌다.
- 단지 식별은 `단지일련번호`(aptSeq) 기준. 좌표 변환 실패 단지는 지도에서만 빠진다.
- `.env`는 공유하지 않는다.
