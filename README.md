# scroll — 기업 주가 크롤링 앱

`companylist.txt`에 등록된 국내 상장 기업의 현재 주가를 네이버 금융에서 **순차적으로** 조회하고, 진행률과 함께 텍스트로 출력하는 크롤링 애플리케이션입니다.

상세 요구사항은 [docs/PRD.md](docs/PRD.md)를 참고하세요.

---

## TDD 개발 방식

모든 기능은 **Red → Green → Refactor** 사이클로 구현합니다.

| 단계 | 설명 |
|------|------|
| **Red** | 실패하는 테스트를 먼저 작성한다. |
| **Green** | 테스트를 통과하는 최소 구현을 추가한다. |
| **Refactor** | 중복 제거·이름 정리·구조 개선 후 테스트가 여전히 통과하는지 확인한다. |

### 개발 루프

```bash
# 1. 실패 테스트 작성 후 실행 (Red)
pytest test/test_scroll.py::test_대상_함수 -v

# 2. src/scroll.py 구현 (Green)
# 3. 전체 테스트 실행
pytest test/ -v

# 4. 리팩터링 후 재실행 (Refactor)
pytest test/ -v
```

### 원칙

- 외부 HTTP 호출은 테스트에서 **mock / fake session**으로 대체한다.
- `scroll()`은 `fetcher` 주입으로 네트워크 없이 검증 가능해야 한다.
- 경계 조건은 단위 테스트 + 웹 스모크 테스트 양쪽에서 확인한다.
- 새 기능 추가 시 **테스트 → 구현** 순서를 지킨다.

---

## 태스크 리스트

각 에픽은 TDD 사이클 단위로 나뉩니다. 체크박스는 구현·테스트 완료 여부를 나타냅니다.

---

### Phase 0 — 프로젝트 셋업

- [x] `requirements.txt` 작성 (`requests`, `pytest`)
- [x] `pytest.ini` 설정
- [x] `src/__init__.py` 패키지 초기화
- [x] `companylist.txt` 샘플 입력 파일 준비

---

### Phase 1 — 입력 파싱 (`load_company_list`)

**목표**: `companylist.txt`를 읽어 `Company(name, code)` 목록을 반환한다.

#### Red

- [x] 정상 줄 파싱 테스트 (`기업명,종목코드`) — `test/test_phase1_load_company_list.py`
- [x] 빈 줄·주석(`#`) 무시 테스트
- [x] 앞뒤 공백 trim 테스트
- [x] 잘못된 형식·비숫자 코드 스킵 테스트
- [x] 빈 파일 → 빈 목록 테스트
- [x] 프로젝트 `companylist.txt` 연동 테스트

#### Green

- [x] `Company` dataclass 정의
- [x] `load_company_list(path)` 구현
- [x] 쉼표 구분, 6자리 숫자 코드 검증 (`\d{6}`)
- [x] EOF까지 순차 읽기

#### Refactor

- [x] 파싱 로직을 단일 함수로 정리
- [x] `Path` 타입 힌트 통일

---

### Phase 2 — 출력 포맷 (`format_line`, `ScrollResult`)

**목표**: `[현재번호/전체번호] 기업명 종목코드 현재주가` 한 줄을 생성한다.

#### Red

- [x] `format_line(1, 3, company, "72,500")` 포맷 테스트 — `test/test_phase2_format.py`
- [x] `ScrollResult.to_line()` 위임 테스트
- [x] `[1/1]`, `[n/n]` 경계 케이스 테스트
- [x] `N/A` 조회 실패 포맷 테스트
- [x] `ScrollResult` 필드 보존 테스트

#### Green

- [x] `format_line()` 구현
- [x] `ScrollResult` dataclass + `to_line()` 구현

#### Refactor

- [x] 출력 책임을 `ScrollResult`에 캡슐화

---

### Phase 3 — 순차 크롤링 오케스트레이션 (`scroll`)

**목표**: 목록을 **한 건씩** 처리하고, 각 결과를 출력한 뒤 EOF에서 종료한다.

#### Red

- [x] 3건 목록 순차 호출 순서 검증 (`fetcher` mock) — `test/test_phase3_scroll.py`
- [x] 출력 줄 `[1/3]` … `[3/3]` 검증
- [x] 빈 목록 → `fetcher` 미호출·즉시 종료 테스트
- [x] `ScrollResult` 목록 반환 테스트
- [x] 단일 건 `[1/1]` 테스트
- [x] `TextIO` 출력 테스트
- [x] `delay_seconds` 대기·마지막 항목 미대기 테스트
- [x] 병렬 호출 없음(순차성) 테스트

#### Green

- [x] `scroll(path, output, fetcher, delay_seconds)` 구현
- [x] `enumerate(companies, start=1)`로 진행률 계산
- [x] `output` 콜백 / `TextIO` 지원
- [x] 요청 간 `delay_seconds` 대기

#### Refactor

- [x] `fetcher` 주입으로 테스트 가능한 구조 확보
- [x] `write` 헬퍼 분기 정리

---

### Phase 4 — 주가 조회 (`fetch_current_price`)

**목표**: 네이버 금융 실시간 API로 현재 주가를 조회한다.

| 소스 | URL |
|------|-----|
| 실시간 API | `polling.finance.naver.com/api/realtime/domestic/stock/{code}` |

#### Red

- [x] 실시간 API 응답 파싱 테스트 — `test/test_phase4_fetch_price.py`
- [x] API 성공 시 단일 호출 테스트
- [x] API 실패 시 `N/A` 반환 테스트
- [x] 주가 천 단위 콤마 정규화 테스트 (`_normalize_price`)
- [x] `User-Agent` 헤더 테스트

#### Green

- [x] `fetch_current_price(code, session)` 구현
- [x] `User-Agent` 헤더 포함
- [x] `PRICE_UNAVAILABLE = "N/A"` 상수 정의
- [x] 예외·실패 시 `N/A` 반환

#### Refactor

- [x] `_parse_realtime_payload`, `_normalize_price` 분리
- [x] `Session` 주입으로 HTTP mock 용이하게 구성

---

### Phase 5 — CLI (`main`)

**목표**: 커맨드라인에서 크롤링을 실행한다.

#### Red

- [x] `--input` 없는 파일 → exit code 1 테스트 — `test/test_phase5_cli.py`
- [x] `--input`, `--delay`, `--secure` 인자 전달 테스트
- [x] 기본 SSL 검증 비활성화 테스트

#### Green

- [x] `argparse` 기반 CLI (`--input`, `--delay`, `--secure`)
- [x] 입력 파일 없음 시 stderr 메시지 + exit 1
- [x] `python -m src.scroll` 엔트리포인트
- [x] `resolve_verify_ssl()` — 기본 SSL 비활성, 프록시 호환

#### Refactor

- [x] `_build_parser()` 분리

---

### Phase 6 — 경계 스모크 웹 (`smoke_web`)

**목표**: 브라우저에서 경계 조건을 빠르게 확인한다.

#### Red

- [x] `run_boundary_smoke_tests()` 6개 케이스 — `test/test_phase6_smoke_web.py`
- [x] `/api/health` 응답 테스트
- [x] `/api/smoke/boundary` 응답 테스트
- [x] `/api/smoke/live` 잘못된 코드 검증 테스트
- [x] 404 엔드포인트 테스트

#### Green

- [x] `run_boundary_smoke_tests()` — 모킹 기반 6종 경계 케이스
  - 빈 파일
  - 공백만
  - 1건 목록 (`[1/1]`)
  - 잘못된 코드 스킵
  - 조회 실패 (`N/A`)
  - 다건 순차 (`[1/n]` … `[n/n]`)
- [x] `run_live_smoke(code)` — 실제 네이버 1건 조회
- [x] `ThreadingHTTPServer` 웹 서버 (`127.0.0.1:8765`)
- [x] 엔드포인트: `/`, `/api/health`, `/api/smoke/boundary`, `/api/smoke/live`
- [x] HTML UI (경계 테스트 버튼, 실시간 조회 폼)

#### Refactor

- [x] `_run_scroll_on_text()` 임시 파일 헬퍼 추출
- [x] JSON/HTML 응답 헬퍼 분리

---

### Phase 7 — 통합 검증 (완료 기준)

- [x] `pytest test/ -v` 전체 통과 (46 tests)
- [x] `python -m src.scroll --input companylist.txt` 실제 크롤링 확인
- [x] 경계 스모크 자동화 통합 테스트
- [x] [docs/PRD.md](docs/PRD.md) 완료 기준과 일치 여부 점검

---

### Phase 8 — MongoDB 저장 (`mongo_store`)

**목표**: 크롤링 결과를 **텍스트 출력과 동시에** 로컬 MongoDB에 저장한다. MongoDB는 외부에서 별도 실행한다.

| 항목 | 값 |
|------|-----|
| 데이터베이스 | `Scrawl` |
| 컬렉션 | `CURRENT-PRICE` |
| 필드 | `기업코드`, `기업명`, `현재주가` |
| 기본 URI | `mongodb://127.0.0.1:27017` |

#### 사전 준비

```bash
# 1. MongoDB 실행 (별도 터미널)
mongod

# 2. DB·컬렉션 초기화 (최초 1회)
mongosh mongodb://127.0.0.1:27017 mongodb/init_scrawl_mongodb.js
# 또는
python mongodb/init_scrawl_mongodb.py
```

#### Red

- [x] `save_current_price()` 문서 생성 테스트 — `test/test_phase8_mongo_store.py`
- [x] `기업코드` 기준 upsert 테스트
- [x] `scroll()` 출력과 동시 저장 호출 순서 테스트 (mock)
- [x] MongoDB 연결 실패 시 동작 테스트
- [x] `--mongo` CLI 플래그 전달 테스트

#### Green

- [x] `src/mongo_store.py` — `MongoStore` / `save_current_price()` 구현
- [x] `scroll()`에서 `--mongo` 활성 시 건별 저장
- [x] `기업코드` unique index upsert
- [x] `MONGODB_URI` 환경 변수 지원

#### Refactor

- [x] `price_saver` 콜백 주입으로 테스트 가능한 구조
- [x] `scroll()`과 MongoDB 책임 분리

---

## 디렉터리 구조

```
crawl/
├── companylist.txt
├── requirements.txt
├── pytest.ini
├── README.md
├── docs/PRD.md
├── report/               # Phase별 진행 보고서 (01~07)
├── mongodb/
│   ├── init_scrawl_mongodb.js   # MongoDB 초기화 (mongosh)
│   └── init_scrawl_mongodb.py   # MongoDB 초기화 (Python)
├── src/
│   ├── __init__.py
│   ├── company_list.py   # 파일 읽기 (companylist.txt)
│   ├── price_fetcher.py  # 웹 주가 조회 (네이버 실시간 API)
│   ├── mongo_store.py    # MongoDB 저장
│   ├── scroll.py         # 순차 크롤링 오케스트레이션 + CLI
│   └── smoke_web.py
└── test/
    ├── test_setup.py
    ├── test_phase1_load_company_list.py
    ├── test_phase2_format.py
    ├── test_phase3_scroll.py
    ├── test_phase4_fetch_price.py
    ├── test_phase5_cli.py
    ├── test_phase6_smoke_web.py
    ├── test_phase7_integration.py
    └── test_phase8_mongo_store.py
```

---

## 사용법

### 설치

```bash
pip install -r requirements.txt
```

### CLI 크롤링

```bash
python -m src.scroll --input companylist.txt
python -m src.scroll --input companylist.txt --delay 0.5
python -m src.scroll --input companylist.txt --secure   # SSL 검증 필요 시
python -m src.scroll --input companylist.txt --mongo     # MongoDB 저장
```

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--input` | `companylist.txt` | 기업 목록 파일 |
| `--delay` | `0.3` | 요청 간 대기(초) |
| `--secure` | off | SSL 인증서 검증 활성화 |
| `--mongo` | off | MongoDB에 현재 주가 저장 |
| `--mongo-uri` | `mongodb://127.0.0.1:27017` | MongoDB 연결 URI |

기본적으로 SSL 검증은 비활성화되어 프록시 환경에서도 `--secure` 없이 동작합니다.  
`SCROLL_VERIFY_SSL=1` 환경 변수로도 검증을 켤 수 있습니다.

**출력 예시**

```
[1/3] 삼성전자 005930 72,500
[2/3] SK하이닉스 000660 198,000
[3/3] NAVER 035420 215,500
```

### MongoDB 초기화 (Phase 8 사전 준비)

MongoDB는 앱 외부에서 별도로 실행합니다.

```bash
# MongoDB 서버 실행 (별도 터미널)
mongod

# DB Scrawl + 컬렉션 CURRENT-PRICE 생성 (최초 1회)
mongosh mongodb://127.0.0.1:27017 mongodb/init_scrawl_mongodb.js
```

| 항목 | 값 |
|------|-----|
| 데이터베이스 | `Scrawl` |
| 컬렉션 | `CURRENT-PRICE` |
| 필드 | `기업코드`, `기업명`, `현재주가` |

Python으로 초기화하려면:

```bash
pip install pymongo
python mongodb/init_scrawl_mongodb.py
```

### 테스트

```bash
pytest test/ -v
```

### 웹 스모크 테스트

```bash
python -m src.smoke_web
# 브라우저: http://127.0.0.1:8765
```

| 엔드포인트 | 설명 |
|------------|------|
| `GET /` | 스모크 테스트 UI |
| `GET /api/health` | 서버 상태 |
| `GET /api/smoke/boundary` | 경계 케이스 일괄 실행 (모킹) |
| `GET /api/smoke/live?code=005930` | 실제 네이버 금융 1건 조회 |

---

## TDD 작업 순서 요약

신규 기능을 추가할 때는 아래 순서를 따릅니다.

1. **PRD / README 태스크**에서 해당 Phase 확인
2. `test/`에 실패 테스트 작성 (**Red**)
3. `src/scroll.py` 또는 `src/smoke_web.py`에 최소 구현 (**Green**)
4. `pytest test/ -v` 통과 확인
5. 구조 개선 (**Refactor**) 후 재실행
6. README 체크박스·`report/` 보고서 갱신

---

## Phase 보고서

| 번호 | Phase | 보고서 |
|------|-------|--------|
| 01 | 입력 파싱 | [report/01-phase1-report.md](report/01-phase1-report.md) |
| 02 | 출력 포맷 | [report/02-phase2-report.md](report/02-phase2-report.md) |
| 03 | 순차 크롤링 | [report/03-phase3-report.md](report/03-phase3-report.md) |
| 04 | 주가 조회 | [report/04-phase4-report.md](report/04-phase4-report.md) |
| 05 | CLI | [report/05-phase5-report.md](report/05-phase5-report.md) |
| 06 | 스모크 웹 | [report/06-phase6-report.md](report/06-phase6-report.md) |
| 07 | 통합 검증 | [report/07-phase7-report.md](report/07-phase7-report.md) |
| 08 | MongoDB 저장 | [report/08-phase8-report.md](report/08-phase8-report.md) |
