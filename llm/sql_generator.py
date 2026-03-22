"""
Text-to-SQL 2단계 LLM 호출 모듈 (Groq API 사용)

1단계: 질문 + 스키마 → LLM이 SQL 생성
2단계: SQL 실행 결과 + 질문 → LLM이 최종 답변 생성
"""

import re
from typing import Generator

from openai import OpenAI

from db.loader import get_schema_summary, run_query_safe
from llm.prompts import (
    SQL_SYSTEM,
    ANSWER_SYSTEM,
    build_sql_user_message,
    build_answer_user_message,
)

MODEL = "llama-3.3-70b-versatile"
MAX_TOKENS_SQL    = 1000
MAX_TOKENS_ANSWER = 2000


def _extract_sql_blocks(text: str) -> list[str]:
    """응답에서 SQL 코드블록 추출"""
    # ```sql ... ``` 형식
    blocks = re.findall(r"```sql\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if blocks:
        return blocks
    # ``` ... ``` 형식 (언어 태그 없음)
    blocks = re.findall(r"```\s*(SELECT.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    return blocks


def generate_answer(
    client: OpenAI,
    question: str,
    conversation_history: list[dict],
) -> Generator[str, None, None]:
    """
    2단계 Text-to-SQL 파이프라인 (스트리밍)

    Yields: 답변 텍스트 조각들 (streaming)
    """
    schema = get_schema_summary()

    # ── 1단계: SQL 생성 ──────────────────────────────────────
    sql_user_msg = build_sql_user_message(question, schema)
    sql_response = client.chat.completions.create(
        model=MODEL,
        max_tokens=MAX_TOKENS_SQL,
        messages=[
            {"role": "system", "content": SQL_SYSTEM},
            {"role": "user",   "content": sql_user_msg},
        ],
    )
    sql_text = sql_response.choices[0].message.content

    # SQL 블록 추출
    sql_queries = _extract_sql_blocks(sql_text)

    # ── SQL 실행 ────────────────────────────────────────────
    result_parts = []

    if not sql_queries:
        result_parts.append("(SQL을 생성하지 못했습니다. 직접 데이터를 조회합니다.)")
    else:
        for i, sql in enumerate(sql_queries, 1):
            df, err = run_query_safe(sql, max_rows=200)
            if err:
                result_parts.append(f"[쿼리 {i} 오류] {err}\nSQL: {sql}")
            elif df is not None and not df.empty:
                result_parts.append(f"[쿼리 {i} 결과 ({len(df)}행)]\n{df.to_string(index=False, max_rows=100)}")
            else:
                result_parts.append(f"[쿼리 {i}] 결과 없음")

    query_results = "\n\n".join(result_parts)

    # ── 2단계: 최종 답변 생성 (스트리밍) ────────────────────
    answer_user_msg = build_answer_user_message(question, query_results)

    # 대화 이력에서 마지막 사용자 메시지 제외 (aug_msg로 교체)
    history_without_last = conversation_history[:-1]
    messages_for_answer = (
        [{"role": "system", "content": ANSWER_SYSTEM}]
        + history_without_last
        + [{"role": "user", "content": answer_user_msg}]
    )

    stream = client.chat.completions.create(
        model=MODEL,
        max_tokens=MAX_TOKENS_ANSWER,
        messages=messages_for_answer,
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
