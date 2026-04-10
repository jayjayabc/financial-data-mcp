# financial-data MCP 사용 가이드

DART(전자공시시스템)과 FISIS(금융통계정보시스템) MCP 서버를 Claude Code / Claude Desktop에서 활용하는 실전 가이드입니다.

## 설치 확인 (최초 1회)

```bash
# 프로젝트 루트에서
python scripts/preflight.py
```

모든 체크가 통과하면 준비 완료. 실패 항목이 있으면 hint를 따라 해결하세요.

---

## 제공 도구 (11개)

### DART (전자공시시스템)

| 도구 | 설명 |
|------|------|
| `dart_search_company` | 회사명 → 기업코드(corp_code) 검색 |
| `dart_company_overview` | 기업개황 (대표자, 업종 등) |
| `dart_search_disclosures` | 공시 검색 (기간/유형별) |
| `dart_financial_statements` | 주요 재무계정 (자산/부채/매출) |
| `dart_full_financial_statements` | 전체 재무제표 (sj_div 필터 추천) |
| `dart_multi_company_financials` | 다중 회사 비교 (최대 20개) |

### FISIS (금융통계정보시스템)

| 도구 | 설명 |
|------|------|
| `fisis_list_statistics` | 통계목록 검색 |
| `fisis_get_statistics` | 금융통계 데이터 조회 |
| `fisis_list_companies` | 금융회사 목록 |

### 운영/진단

| 도구 | 설명 |
|------|------|
| `dart_quota_status` | DART 일일 API quota 현황 (20,000건/일) |
| `get_api_reference` | DART/FISIS 코드표 + 사용 예시 |

---

## 실전 시나리오 10가지

Claude Code에서 그대로 복사해서 써보세요.

### 1. 단일 기업 재무제표 조회 (가장 기본)

> **질문**: "삼성전자 2024년 손익계산서 보여줘"
>
> **도구 흐름**:
> 1. `dart_search_company(name='삼성전자')` → corp_code='00126380'
> 2. `dart_full_financial_statements(corp_code='00126380', bsns_year='2024', sj_div='IS')`
>
> **팁**: `sj_div='IS'`로 손익계산서만 필터하면 토큰 ~75% 절감.

---

### 2. 연간 주요 재무지표만 빠르게

> **질문**: "삼성전자 2024년 자산/매출/영업이익만"
>
> **도구 흐름**:
> 1. `dart_search_company(name='삼성전자')`
> 2. `dart_financial_statements(corp_code='00126380', bsns_year='2024')`
>
> **팁**: `dart_financial_statements`(주요계정)는 `dart_full_financial_statements`보다 훨씬 작고 빠릅니다. 단순 지표만 필요하면 이걸 쓰세요.

---

### 3. 분기별 실적 추이

> **질문**: "삼성전자 2024년 분기별 매출 추이 알려줘"
>
> **도구 흐름**:
> 1. `dart_search_company(name='삼성전자')`
> 2. `dart_financial_statements(corp_code='00126380', bsns_year='2024', reprt_code='11013')` # 1분기
> 3. `dart_financial_statements(corp_code='00126380', bsns_year='2024', reprt_code='11012')` # 반기
> 4. `dart_financial_statements(corp_code='00126380', bsns_year='2024', reprt_code='11014')` # 3분기
> 5. `dart_financial_statements(corp_code='00126380', bsns_year='2024', reprt_code='11011')` # 사업
>
> **팁**: 각 분기는 누적 또는 당분기 기준인지 LLM이 명시하여 분석합니다.

---

### 4. 경쟁사 비교 (핵심 기능)

> **질문**: "삼성전자, SK하이닉스, LG전자 2024년 영업이익률 비교해줘"
>
> **도구 흐름**:
> 1. `dart_search_company(name='삼성전자')` → 00126380
> 2. `dart_search_company(name='SK하이닉스')` → 00164779
> 3. `dart_search_company(name='LG전자')` → 00401731
> 4. `dart_multi_company_financials(corp_codes=['00126380','00164779','00401731'], bsns_year='2024')`
>
> **팁**: 다중 회사 비교는 한 번의 API 호출로 최대 20개 회사 조회 가능 → quota 절약.

---

### 5. 기업개황 (상장일, 업종, 대표자 등)

> **질문**: "현대자동차 회사 정보 알려줘 (상장일, 대표자, 본사 주소 등)"
>
> **도구 흐름**:
> 1. `dart_search_company(name='현대자동차')`
> 2. `dart_company_overview(corp_code='...')`

---

### 6. 특정 기업의 최근 공시 조회

> **질문**: "삼성전자 2025년 1분기 공시 목록 보여줘"
>
> **도구 흐름**:
> 1. `dart_search_company(name='삼성전자')`
> 2. `dart_search_disclosures(corp_code='00126380', bgn_de='20250101', end_de='20250331')`

---

### 7. 업종별/기간별 공시 스크리닝

> **질문**: "2024년 하반기 코스닥 상장사 주요사항보고 공시 목록"
>
> **도구 흐름**:
> 1. `dart_search_disclosures(bgn_de='20240701', end_de='20241231', corp_cls='K', pblntf_ty='B')`
>
> **팁**: `pblntf_ty='B'` = 주요사항보고. `get_api_reference`로 전체 유형 코드 확인.

---

### 8. 은행 업권 통계 조회

> **질문**: "2024년 국내 은행 요약재무제표 보여줘"
>
> **도구 흐름**:
> 1. `fisis_list_statistics(lrg_div='01')` → 은행 통계 목록에서 stat_cd 확인
> 2. `fisis_list_companies(lrg_div='01')` → 은행 목록 확인
> 3. `fisis_get_statistics(stat_cd='<확인한코드>', strt_yymm='202401', end_yymm='202412', lrg_div='01')`

---

### 9. 보험사 / 금융투자업권 비교

> **질문**: "삼성생명과 삼성화재 총자산 추이 (최근 1년)"
>
> **도구 흐름**:
> 1. `fisis_list_companies(lrg_div='03')` → 삼성생명/삼성화재 finance_cd 확인
> 2. `fisis_list_statistics(lrg_div='03')` → 자산 관련 통계코드 확인
> 3. `fisis_get_statistics(stat_cd='...', strt_yymm='202401', end_yymm='202412', finance_cd='...')` (2번 호출)

---

### 10. DART quota 확인 (배치 작업 전)

> **질문**: "오늘 내가 DART API 얼마나 썼어?"
>
> **도구 흐름**:
> 1. `dart_quota_status()`
>
> **반환 예시**:
> ```json
> {
>   "today": "2025-04-10",
>   "today_count": 150,
>   "daily_limit": 20000,
>   "remaining": 19850,
>   "usage_pct": 0.75,
>   "near_limit": false,
>   "history_last_7_days": {"2025-04-10": 150, "2025-04-09": 230, ...}
> }
> ```
>
> **팁**: 50개 이상 기업 배치 분석 전에 이 도구로 확인. 20,000건 한도 근접 시 경고 포함.

---

## 토큰·API 절약 팁

### 1. `sj_div` 필터 적극 활용
`dart_full_financial_statements`는 전체 재무제표를 반환하므로 200행+가 나올 수 있습니다. 특정 표만 필요하면:
- `sj_div='BS'` → 재무상태표만
- `sj_div='IS'` → 손익계산서만
- `sj_div='CF'` → 현금흐름표만

**실측**: 필터 시 약 75% 토큰 절감.

### 2. 주요계정이면 `dart_financial_statements` 사용
자산/부채/매출/영업이익/순이익만 필요하면 `dart_full_financial_statements` 대신 `dart_financial_statements`를 쓰세요. 응답이 훨씬 작습니다.

### 3. 다중 회사는 `dart_multi_company_financials`로 한 번에
3~20개 회사를 비교할 땐 개별 호출 대신 다중 도구로 한 번에. API 호출 1회 + 토큰도 절약.

### 4. 동일 질문 반복 시 캐시 활용
같은 `(corp_code, bsns_year, reprt_code)` 조합은 1시간 동안 메모리 캐시됩니다. 같은 세션에서 여러 번 물어봐도 추가 API 호출 없음.

### 5. 기업코드는 자동 캐시됨
`dart_search_company`가 처음 호출되면 ~90,000건 기업코드 목록(약 8MB)을 다운로드해서 30일간 디스크 캐시합니다. 이후 검색은 즉시 반환.

---

## 자동 복구 기능

### CFS → OFS 자동 폴백
`dart_full_financial_statements`는 기본 `fs_div='CFS'`(연결재무제표)로 조회하는데, 소규모·비상장 기업은 연결을 안 만드는 경우가 있습니다. 이럴 때 자동으로 `fs_div='OFS'`(개별)로 재조회하고 응답에 `note` 필드로 알려줍니다.

### 일시 장애 자동 재시도
DART/FISIS가 503/504/429를 주면 1초 → 2초 → 4초 지수 백오프로 3회까지 자동 재시도합니다. 일시 장애 대부분은 사용자 개입 없이 해결됩니다.

### 입력 검증
잘못된 `corp_code`(숫자 8자리 아님), 잘못된 `bsns_year`(4자리 아님) 등은 API 호출 전에 걸러지고 친화적 에러 메시지를 반환합니다 (quota 낭비 방지).

---

## 에러 응답 형식

도구가 실패하면 접두사로 에러 종류를 표시합니다:

| 접두사 | 의미 | LLM이 해야 할 것 |
|--------|------|------------------|
| `[input error]` | 입력 검증 실패 | 파라미터 수정 후 재호출 |
| `[api error]` | DART/FISIS API 오류 또는 네트워크 오류 | 메시지에 따라 판단 (키 오류면 사용자에게 보고, 일시 장애면 재시도) |
| `[internal error]` | 예상치 못한 서버 오류 | 사용자에게 보고 |

---

## 로깅 (디버깅용)

환경변수 `LOG_LEVEL=DEBUG`로 세부 로그 확인:

```bash
LOG_LEVEL=DEBUG claude   # Claude Code CLI
```

stderr로 다음이 출력됩니다:
- 캐시 hit/miss
- API 호출 + 마스킹된 파라미터
- 재시도 발생
- quota 증가
