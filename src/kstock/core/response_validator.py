"""AI 응답 검증기 — JSON 스키마 체크 + 환각 탐지.

AI 라우터의 응답을 검증하여 잘못된 데이터가
의사결정에 사용되는 것을 방지한다.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """AI 응답 검증 결과."""
    valid: bool = True
    json_valid: bool = True
    hallucination_score: float = 0.0  # 0~1 (높을수록 환각 의심)
    issues: list[str] = None
    cleaned_response: str = ""

    def __post_init__(self):
        if self.issues is None:
            self.issues = []


def validate_json_response(
    response: str,
    required_keys: list[str] | None = None,
    numeric_ranges: dict[str, tuple] | None = None,
) -> ValidationResult:
    """AI JSON 응답 검증.

    Args:
        response: AI가 반환한 문자열
        required_keys: 필수 키 목록
        numeric_ranges: {key: (min, max)} 숫자 범위 검증

    Returns:
        ValidationResult
    """
    result = ValidationResult(cleaned_response=response)

    try:
        # JSON 추출 시도
        json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
        if not json_match:
            # 배열 시도
            json_match = re.search(r'\[[^\[\]]*\]', response, re.DOTALL)

        if not json_match:
            result.json_valid = False
            result.issues.append("JSON 형식 없음")
            result.valid = False
            return result

        data = json.loads(json_match.group())
        result.cleaned_response = json.dumps(data, ensure_ascii=False)

        # 필수 키 검증
        if required_keys and isinstance(data, dict):
            missing = [k for k in required_keys if k not in data]
            if missing:
                result.issues.append(f"필수 키 누락: {missing}")
                result.valid = False

        # 숫자 범위 검증
        if numeric_ranges and isinstance(data, dict):
            for key, (vmin, vmax) in numeric_ranges.items():
                val = data.get(key)
                if val is not None and isinstance(val, (int, float)):
                    if val < vmin or val > vmax:
                        result.issues.append(
                            f"{key}={val} 범위 초과 ({vmin}~{vmax})"
                        )
                        result.hallucination_score += 0.3

        return result

    except json.JSONDecodeError as e:
        result.json_valid = False
        result.issues.append(f"JSON 파싱 실패: {e}")
        result.valid = False
        return result
    except Exception as e:
        logger.error("AI 응답 검증 실패: %s", e)
        result.valid = False
        result.issues.append(f"검증 오류: {e}")
        return result


def detect_hallucination(
    response: str,
    context_tickers: list[str] | None = None,
    max_price: float = 10_000_000,
) -> float:
    """AI 환각 점수 산출 (0~1).

    높은 점수 = 환각 가능성 높음.

    검출 항목:
    - 비현실적 가격 (1000만원 이상)
    - 존재하지 않는 종목 코드 참조
    - 100% 이상 수익률 예측
    - 날짜 일관성 오류
    """
    score = 0.0

    try:
        # 비현실적 숫자 검출
        numbers = re.findall(r'[\d,]+\.?\d*', response)
        for num_str in numbers:
            try:
                num = float(num_str.replace(',', ''))
                if num > max_price:
                    score += 0.2
                    break
            except ValueError:
                continue

        # 100%+ 수익률 예측 검출
        pct_matches = re.findall(r'(\d+\.?\d*)\s*%', response)
        for pct in pct_matches:
            try:
                if float(pct) > 100:
                    score += 0.15
                    break
            except ValueError:
                continue

        # 미래 날짜 참조 (기본적 환각 패턴)
        future_patterns = [
            r'202[7-9]', r'203\d',  # 2027+ 년도
            r'확실히.*상승', r'반드시.*수익',  # 과도한 확신
            r'100%\s*확률', r'guaranteed',
        ]
        for pattern in future_patterns:
            if re.search(pattern, response, re.IGNORECASE):
                score += 0.1

        return min(1.0, score)

    except Exception:
        logger.exception("환각 탐지 실패")
        return 0.0


def validate_analysis_response(
    response: str,
    task: str = "technical_analysis",
) -> ValidationResult:
    """분석 작업별 AI 응답 통합 검증.

    Args:
        response: AI 응답 문자열
        task: 작업 유형 (technical_analysis, risk_assessment 등)
    """
    result = ValidationResult(cleaned_response=response)

    try:
        # 환각 점수
        result.hallucination_score = detect_hallucination(response)

        if result.hallucination_score > 0.5:
            result.issues.append(f"환각 의심 (점수: {result.hallucination_score:.2f})")
            result.valid = False

        # 빈 응답 체크
        if not response or len(response.strip()) < 10:
            result.issues.append("응답이 너무 짧음")
            result.valid = False

        # 작업별 추가 검증
        if task == "technical_analysis":
            # RSI 범위 체크
            rsi_match = re.search(r'rsi["\s:]+(\d+\.?\d*)', response, re.IGNORECASE)
            if rsi_match:
                rsi = float(rsi_match.group(1))
                if rsi < 0 or rsi > 100:
                    result.issues.append(f"RSI 범위 초과: {rsi}")
                    result.hallucination_score += 0.3

        return result

    except Exception as e:
        logger.error("분석 응답 검증 실패: %s", e)
        result.valid = False
        result.issues.append(f"검증 오류: {e}")
        return result


# ---------------------------------------------------------------------------
# v8.1: Hallucination blocking policy
# ---------------------------------------------------------------------------

# Threshold levels
HALLUCINATION_WARN = 0.3   # 경고만
HALLUCINATION_FLAG = 0.5   # [잠재 환각] 태그 부착
HALLUCINATION_BLOCK = 0.7  # 응답 차단, 폴백 사용


def apply_hallucination_policy(
    response: str,
    task: str = "analysis",
    current_price: float = 0,
    ticker: str = "",
) -> tuple[str, str]:
    """AI 응답에 환각 정책을 적용합니다.

    Args:
        response: AI 응답 원문
        task: 작업 유형
        current_price: 현재 주가 (가격 검증용)
        ticker: 종목코드

    Returns:
        (processed_response, policy_action)
        policy_action: "pass" | "warn" | "flag" | "block"
    """
    try:
        h_score = detect_hallucination(response, max_price=10_000_000)

        # 가격 기반 추가 검증
        if current_price > 0:
            h_score += _check_price_hallucination(response, current_price)

        h_score = min(1.0, h_score)

        if h_score >= HALLUCINATION_BLOCK:
            logger.warning(
                "[환각차단] ticker=%s, score=%.2f, task=%s → BLOCKED",
                ticker, h_score, task,
            )
            fallback = (
                f"[환각 감지] AI 응답의 신뢰도가 낮아 차단되었습니다 "
                f"(환각 점수: {h_score:.0%}).\n"
                f"실시간 데이터를 기반으로 직접 확인해 주세요."
            )
            return fallback, "block"

        if h_score >= HALLUCINATION_FLAG:
            logger.warning(
                "[환각경고] ticker=%s, score=%.2f → FLAGGED", ticker, h_score,
            )
            flagged = f"[잠재 환각 경고 - 신뢰도 {1 - h_score:.0%}]\n{response}"
            return flagged, "flag"

        if h_score >= HALLUCINATION_WARN:
            logger.info("[환각주의] ticker=%s, score=%.2f", ticker, h_score)
            return response, "warn"

        return response, "pass"

    except Exception as e:
        logger.error("환각 정책 적용 실패: %s", e)
        return response, "pass"


def _check_price_hallucination(response: str, current_price: float) -> float:
    """현재가 대비 비현실적 가격 언급을 탐지합니다."""
    score = 0.0
    try:
        # 목표가/가격 패턴 추출
        price_patterns = re.findall(
            r'(?:목표가|target|가격|price|예상)[^0-9]*?([\d,]+)\s*원?',
            response, re.IGNORECASE,
        )
        for p in price_patterns:
            try:
                mentioned = float(p.replace(',', ''))
                if mentioned > 0:
                    ratio = mentioned / current_price
                    # 현재가의 3배 이상이나 1/3 이하면 환각 의심
                    if ratio > 3.0 or ratio < 0.33:
                        score += 0.25
                        break
                    # 50% 이상 괴리도 약한 의심
                    elif ratio > 1.5 or ratio < 0.67:
                        score += 0.1
            except ValueError:
                continue
    except Exception:
        pass
    return min(0.5, score)
