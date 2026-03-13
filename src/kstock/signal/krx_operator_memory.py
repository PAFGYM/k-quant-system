"""Korean market operator memory for 24/7 tactical coaching."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from kstock.signal.market_regime_v2 import REGIME_KR

logger = logging.getLogger(__name__)

_PLAYBOOK_TO_REGIME = {
    "normal": "neutral",
    "caution": "neutral",
    "defense": "bear",
    "crisis": "crash",
}

_DETECT_TO_REGIME = {
    "bubble_attack": "strong_bull",
    "attack": "bull",
    "balanced": "neutral",
    "defense": "bear",
}

_MANAGER_LABELS = {
    "scalp": "리버모어",
    "swing": "오닐",
    "position": "린치",
    "long_term": "버핏",
    "tenbagger": "텐베거",
}


@dataclass
class SimilarRegimeMatch:
    """Most similar stored Korean market session."""

    date: str
    similarity: float
    regime_key: str
    summary: str
    worked: list[str] = field(default_factory=list)


@dataclass
class KRXOperatorMemory:
    """Compact operator memory used by morning/night reports."""

    regime_key: str = "neutral"
    regime_label: str = "횡보장"
    headline: str = ""
    action_bias: list[str] = field(default_factory=list)
    attack_points: list[str] = field(default_factory=list)
    avoid_points: list[str] = field(default_factory=list)
    manager_focus: list[str] = field(default_factory=list)
    top_matches: list[SimilarRegimeMatch] = field(default_factory=list)
    learning_notes: list[str] = field(default_factory=list)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_json(value: Any, default: Any) -> Any:
    if value in (None, "", b""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _normalize_regime_key(
    latest_regime: dict | None,
    playbook: Any = None,
    regime_mode: dict | None = None,
) -> str:
    if latest_regime:
        key = str(latest_regime.get("regime", "") or "").strip()
        if key:
            return key
    if playbook:
        return _PLAYBOOK_TO_REGIME.get(
            str(getattr(playbook, "regime", "") or "").strip(),
            "neutral",
        )
    if regime_mode:
        return _DETECT_TO_REGIME.get(
            str(regime_mode.get("mode", "") or "").strip(),
            "neutral",
        )
    return "neutral"


def _current_headline(
    macro: Any,
    regime_key: str,
    playbook: Any = None,
) -> str:
    if playbook and getattr(playbook, "headline", ""):
        return str(playbook.headline)

    vix = _safe_float(getattr(macro, "vix", 0))
    wti = _safe_float(getattr(macro, "wti_change_pct", 0))
    usd = _safe_float(getattr(macro, "usdkrw_change_pct", 0))
    koru = _safe_float(getattr(macro, "koru_change_pct", 0))

    if wti >= 4.0 and usd >= 0.5:
        return "유가+환율 쇼크 구간, 수급과 방어 업종 우선"
    if vix >= 28 or koru <= -8.0:
        return "변동성 경보 구간, 시초 추격보다 생존과 반격 준비"
    if regime_key in {"bear", "crash"}:
        return "하락장 대응 구간, 강한 종목만 제한적으로 추적"
    if regime_key in {"bull", "strong_bull"}:
        return "상승 모멘텀 구간, 정책·이벤트 수혜를 선점"
    return "중립 구간, 강한 종목 선별과 비중 조절이 핵심"


def _load_manager_scorecards(db: Any) -> list[dict]:
    if not hasattr(db, "_connect"):
        return []
    try:
        with db._connect() as conn:
            max_row = conn.execute(
                "SELECT MAX(calculated_at) AS ts FROM manager_scorecard",
            ).fetchone()
            ts = str(max_row["ts"] or "").strip() if max_row else ""
            if not ts:
                return []
            rows = conn.execute(
                """
                SELECT manager_key, hit_rate, weight_adj, avg_return_5d
                FROM manager_scorecard
                WHERE calculated_at=?
                ORDER BY weight_adj DESC, hit_rate DESC
                """,
                (ts,),
            ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        logger.debug("load_manager_scorecards failed", exc_info=True)
        return []


def _fallback_manager_focus(regime_key: str) -> list[str]:
    mapping = {
        "crash": [
            "리버모어: 시초 급반등보다 강한 체결과 회복력만 확인",
            "텐베거: 정책·산업 촉매가 남은 소형주만 관찰",
        ],
        "bear": [
            "오닐: 눌림 반등 2일차 확인 전 비중 축소",
            "텐베거: 스몰캡도 이벤트와 수급이 같이 붙는 종목만 추적",
        ],
        "neutral": [
            "린치: 실적·수급이 겹치는 포지션 후보 우선",
            "텐베거: 산업/정책 카탈리스트가 선명한 스몰캡 체크",
        ],
        "bull": [
            "린치: 중기 추세 강화 종목에 비중 확대",
            "텐베거: 이벤트 선점형 스몰캡 발굴 강화",
        ],
        "strong_bull": [
            "리버모어: 돌파와 거래량이 동시에 붙는 종목 우선",
            "텐베거: 재평가 초입의 스몰캡에 공격적으로 집중",
        ],
    }
    return mapping.get(regime_key, mapping["neutral"])


def _derive_manager_focus(db: Any, regime_key: str) -> list[str]:
    rows = _load_manager_scorecards(db)
    if not rows:
        return _fallback_manager_focus(regime_key)

    lines: list[str] = []
    for row in rows[:2]:
        key = str(row.get("manager_key", "") or "")
        label = _MANAGER_LABELS.get(key, key)
        hit_rate = _safe_float(row.get("hit_rate", 0))
        weight = _safe_float(row.get("weight_adj", 1.0))
        lines.append(f"{label}: 최근 적중률 {hit_rate:.0f}% · 가중치 {weight:.2f}x")
    if regime_key in {"bear", "crash"} and not any("텐베거" in line for line in lines):
        lines.append("텐베거: 급락장에서도 산업/정책 촉매가 유지되는 종목만 추적")
    return lines[:3]


def _keyword_buckets(macro: Any, regime_key: str, playbook: Any = None) -> tuple[list[str], list[str]]:
    attack: list[str] = []
    avoid: list[str] = []

    wti = _safe_float(getattr(macro, "wti_change_pct", 0))
    usd = _safe_float(getattr(macro, "usdkrw_change_pct", 0))
    vix = _safe_float(getattr(macro, "vix", 0))
    nq = _safe_float(getattr(macro, "nq_futures_change_pct", 0))

    if wti >= 3.5:
        attack.extend(["방산", "정유/에너지", "전력/원전"])
        avoid.extend(["항공", "화학"])
    if usd >= 0.5:
        attack.append("달러 수혜 수출주")
        avoid.append("내수 고밸류 성장주")
    if nq <= -1.0 or vix >= 24:
        avoid.extend(["지수 레버리지 추격", "리딩방 과열주"])
    if regime_key in {"bull", "strong_bull"}:
        attack.extend(["이벤트 선점 스몰캡", "반도체/AI"])
    if regime_key in {"bear", "crash"}:
        attack.extend(["숏커버 후보", "수급 방어주"])
        avoid.extend(["거래량만 터진 테마주", "손절선 아래 종목"])

    if playbook:
        for pick in list(getattr(playbook, "strong_stocks", []) or [])[:3]:
            attack.append(f"{pick.name}")
        for pick in list(getattr(playbook, "avoid_stocks", []) or [])[:3]:
            avoid.append(f"{pick.name}")

    dedup_attack = list(dict.fromkeys(item for item in attack if item))
    dedup_avoid = list(dict.fromkeys(item for item in avoid if item))
    return dedup_attack[:5], dedup_avoid[:5]


def _load_recent_learning_notes(db: Any, macro: Any, regime_key: str) -> list[str]:
    getter = getattr(db, "get_learning_history", None)
    if not callable(getter):
        return []

    current_keywords = []
    if _safe_float(getattr(macro, "wti_change_pct", 0)) >= 3.0:
        current_keywords.extend(["유가", "에너지", "방산", "원전"])
    if _safe_float(getattr(macro, "usdkrw_change_pct", 0)) >= 0.5:
        current_keywords.extend(["환율", "달러", "수출"])
    if regime_key in {"bear", "crash"}:
        current_keywords.extend(["변동성", "헤지", "조정", "하락"])
    if regime_key in {"bull", "strong_bull"}:
        current_keywords.extend(["AI", "반도체", "정책", "랠리", "수혜"])

    try:
        rows = getter(days=14) or []
    except Exception:
        logger.debug("load_recent_learning_notes failed", exc_info=True)
        return []

    scored: list[tuple[int, str]] = []
    for row in rows:
        text = " ".join(
            str(row.get(key, "") or "")
            for key in ("description", "impact_summary")
        )
        if not text:
            continue
        hits = sum(1 for keyword in current_keywords if keyword and keyword in text)
        if hits <= 0 and current_keywords:
            continue
        snippet = str(row.get("impact_summary", "") or row.get("description", "") or "").strip()
        if len(snippet) > 78:
            snippet = snippet[:75].rstrip() + "..."
        if snippet:
            scored.append((hits, snippet))

    scored.sort(key=lambda item: (-item[0], item[1]))
    dedup: list[str] = []
    seen: set[str] = set()
    for _, snippet in scored:
        if snippet in seen:
            continue
        seen.add(snippet)
        dedup.append(snippet)
        if len(dedup) >= 2:
            break
    return dedup


def _load_trade_lessons(db: Any) -> list[str]:
    getter = getattr(db, "get_trade_lessons", None)
    if not callable(getter):
        return []
    try:
        rows = getter(limit=6) or []
    except Exception:
        logger.debug("load_trade_lessons failed", exc_info=True)
        return []

    notes: list[str] = []
    for row in rows:
        lesson = str(row.get("lesson", "") or "").strip()
        if not lesson:
            continue
        if len(lesson) > 72:
            lesson = lesson[:69].rstrip() + "..."
        notes.append(lesson)
        if len(notes) >= 2:
            break
    return notes


def _similarity_score(
    current: dict[str, float | str],
    past_regime: dict | None,
    past_cross: dict,
) -> float:
    score = 100.0
    score -= min(abs(_safe_float(current.get("vix")) - _safe_float(past_cross.get("vix"))) * 2.2, 24.0)
    score -= min(abs(_safe_float(current.get("usdkrw_change_pct")) - _safe_float(past_cross.get("usdkrw_change_pct"))) * 18.0, 18.0)
    score -= min(abs(_safe_float(current.get("wti_change_pct")) - _safe_float(past_cross.get("wti_change_pct"))) * 1.3, 18.0)
    score -= min(abs(_safe_float(current.get("composite_score")) - _safe_float(past_cross.get("composite_score"))) * 3.5, 20.0)

    current_direction = str(current.get("direction", "") or "")
    past_direction = str(past_cross.get("direction", "") or "")
    if current_direction and current_direction == past_direction:
        score += 6.0
    elif current_direction and past_direction and current_direction != past_direction:
        score -= 6.0

    current_regime = str(current.get("regime_key", "") or "")
    past_regime_key = str((past_regime or {}).get("regime", "") or "")
    if current_regime and past_regime_key and current_regime == past_regime_key:
        score += 8.0

    return round(max(0.0, min(99.0, score)), 1)


def _match_summary(regime_row: dict | None, cross_row: dict) -> tuple[str, list[str]]:
    direction = str(cross_row.get("direction", "") or "neutral")
    composite = _safe_float(cross_row.get("composite_score", 0))
    risk_flags = _safe_json(cross_row.get("risk_flags_json", "[]"), [])
    worked: list[str] = []

    if regime_row:
        sector_rotation = _safe_json(regime_row.get("sector_rotation_json", "{}"), {})
        portfolio_guide = _safe_json(regime_row.get("portfolio_guide_json", "{}"), {})
        summary = str(regime_row.get("description", "") or "").strip()
        for sector, guidance in list(sector_rotation.items())[:2]:
            if guidance:
                worked.append(f"{sector} {guidance}")
        new_buy = str(portfolio_guide.get("new_buy", "") or "").strip()
        hedging = str(portfolio_guide.get("hedging", "") or "").strip()
        if new_buy:
            worked.append(f"매수 {new_buy}")
        if hedging:
            worked.append(f"헤지 {hedging}")
    else:
        summary = f"{direction} 흐름 (점수 {composite:+.1f})"

    for flag in risk_flags[:1]:
        if flag:
            worked.append(f"리스크 {flag}")

    if not summary:
        summary = f"{direction} 흐름 (점수 {composite:+.1f})"
    return summary, list(dict.fromkeys(worked))[:3]


def _build_similar_matches(
    db: Any,
    current: dict[str, float | str],
) -> list[SimilarRegimeMatch]:
    regime_getter = getattr(db, "get_market_regime", None)
    cross_getter = getattr(db, "get_cross_market_impact", None)
    if not callable(cross_getter):
        return []

    try:
        cross_rows = cross_getter(days=45) or []
    except Exception:
        logger.debug("get_cross_market_impact failed", exc_info=True)
        return []

    regime_rows: list[dict] = []
    if callable(regime_getter):
        try:
            regime_rows = regime_getter(days=45) or []
        except Exception:
            logger.debug("get_market_regime failed", exc_info=True)
            regime_rows = []

    regime_by_date = {str(row.get("date", "") or ""): row for row in regime_rows}
    matches: list[SimilarRegimeMatch] = []
    latest_date = str(current.get("date", "") or "")
    for row in cross_rows:
        date = str(row.get("date", "") or "").strip()
        if not date or date == latest_date:
            continue
        regime_row = regime_by_date.get(date)
        similarity = _similarity_score(current, regime_row, row)
        summary, worked = _match_summary(regime_row, row)
        matches.append(
            SimilarRegimeMatch(
                date=date,
                similarity=similarity,
                regime_key=str((regime_row or {}).get("regime", "") or "neutral"),
                summary=summary,
                worked=worked,
            ),
        )

    matches.sort(key=lambda item: (-item.similarity, item.date), reverse=False)
    return matches[:2]


def build_krx_operator_memory(
    db: Any,
    macro: Any | None = None,
    *,
    playbook: Any = None,
    regime_mode: dict | None = None,
) -> KRXOperatorMemory:
    """Build compact operator memory for Korean market coaching."""
    latest_regime = None
    regime_getter = getattr(db, "get_market_regime", None)
    if callable(regime_getter):
        try:
            rows = regime_getter(days=1) or []
            latest_regime = rows[0] if rows else None
        except Exception:
            logger.debug("build_krx_operator_memory latest regime failed", exc_info=True)

    latest_cross = None
    cross_getter = getattr(db, "get_latest_cross_market", None)
    if callable(cross_getter):
        try:
            latest_cross = cross_getter() or None
        except Exception:
            logger.debug("build_krx_operator_memory latest cross failed", exc_info=True)

    regime_key = _normalize_regime_key(latest_regime, playbook=playbook, regime_mode=regime_mode)
    regime_label = REGIME_KR.get(regime_key, regime_key or "중립")
    headline = _current_headline(macro, regime_key, playbook=playbook)

    current = {
        "date": str((latest_cross or {}).get("date", "") or ""),
        "regime_key": regime_key,
        "direction": str((latest_cross or {}).get("direction", "") or getattr(macro, "regime", "") or ""),
        "composite_score": _safe_float((latest_cross or {}).get("composite_score", 0)),
        "vix": _safe_float(getattr(macro, "vix", (latest_cross or {}).get("vix", 0))),
        "usdkrw_change_pct": _safe_float(
            getattr(macro, "usdkrw_change_pct", (latest_cross or {}).get("usdkrw_change_pct", 0)),
        ),
        "wti_change_pct": _safe_float(
            getattr(macro, "wti_change_pct", (latest_cross or {}).get("wti_change_pct", 0)),
        ),
    }

    attack_points, avoid_points = _keyword_buckets(macro, regime_key, playbook=playbook)
    top_matches = _build_similar_matches(db, current)
    action_bias: list[str] = []

    if _safe_float(getattr(macro, "vix", 0)) >= 24:
        action_bias.append("시초 30분은 추격매수보다 변동성 소화와 강한 종목 선별이 우선")
    if _safe_float(getattr(macro, "koru_change_pct", 0)) <= -8.0:
        action_bias.append("지수 레버리지 복원보다 개별 강세주와 인버스 방어가 우선")
    if playbook and getattr(playbook, "summary", ""):
        action_bias.append(str(playbook.summary))
    if latest_regime:
        guide = _safe_json(latest_regime.get("portfolio_guide_json", "{}"), {})
        new_buy = str(guide.get("new_buy", "") or "").strip()
        position_size = str(guide.get("position_size", "") or "").strip()
        hedging = str(guide.get("hedging", "") or "").strip()
        if new_buy:
            action_bias.append(f"매수는 {new_buy}")
        if position_size:
            action_bias.append(f"포지션은 {position_size}")
        if hedging:
            action_bias.append(f"헤지는 {hedging}")

    learning_notes = _load_recent_learning_notes(db, macro, regime_key)
    learning_notes.extend(
        note for note in _load_trade_lessons(db)
        if note not in learning_notes
    )

    return KRXOperatorMemory(
        regime_key=regime_key,
        regime_label=regime_label,
        headline=headline,
        action_bias=list(dict.fromkeys(action_bias))[:4],
        attack_points=attack_points,
        avoid_points=avoid_points,
        manager_focus=_derive_manager_focus(db, regime_key)[:3],
        top_matches=top_matches,
        learning_notes=learning_notes[:3],
    )


def format_operator_memory_lines(memory: KRXOperatorMemory) -> list[str]:
    """Format compact morning lines."""
    lines: list[str] = []
    if memory.headline:
        lines.append(f"- 현재 판단: {memory.headline}")
    if memory.top_matches:
        match = memory.top_matches[0]
        lines.append(
            f"- 유사 장세: {match.date} · {match.regime_key} · 유사도 {match.similarity:.0f}"
        )
        if match.worked:
            lines.append(f"  당시 유효: {', '.join(match.worked[:2])}")
    if memory.action_bias:
        lines.append(f"- 이번 장 핵심: {memory.action_bias[0]}")
    if memory.attack_points:
        lines.append(f"- 오늘 공략: {', '.join(memory.attack_points[:3])}")
    if memory.avoid_points:
        lines.append(f"- 회피: {', '.join(memory.avoid_points[:3])}")
    if memory.learning_notes:
        lines.append(f"- 최근 학습: {memory.learning_notes[0]}")
    if memory.manager_focus:
        lines.append(f"- 우선 매니저: {', '.join(memory.manager_focus[:2])}")
    return lines[:7]


def format_operator_memory_report_lines(memory: KRXOperatorMemory) -> list[str]:
    """Format richer nightly operator lines."""
    lines = [
        f"  → 현재 레짐: {memory.regime_label}",
    ]
    if memory.headline:
        lines.append(f"  → 한줄 판단: {memory.headline}")
    if memory.top_matches:
        for match in memory.top_matches[:2]:
            lines.append(
                f"  → 유사 장세 {match.date}: {match.summary} (유사도 {match.similarity:.0f})"
            )
            if match.worked:
                lines.append(f"     당시 유효: {', '.join(match.worked[:2])}")
    if memory.action_bias:
        lines.append(f"  → 운용 태세: {memory.action_bias[0]}")
    if memory.attack_points:
        lines.append(f"  → 공략 포인트: {', '.join(memory.attack_points[:4])}")
    if memory.avoid_points:
        lines.append(f"  → 회피 포인트: {', '.join(memory.avoid_points[:4])}")
    if memory.manager_focus:
        lines.append(f"  → 우선 매니저: {', '.join(memory.manager_focus[:3])}")
    if memory.learning_notes:
        lines.append(f"  → 학습 메모: {memory.learning_notes[0]}")
    return lines
