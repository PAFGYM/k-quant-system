"""Weekly macro event calendar tracker (Section 62 - 매크로 이벤트 트래커).

Manages a weekly macro event calendar (FOMC, CPI, employment data, BOK
decisions, etc.) and estimates market impact by sector and direction.

All functions are pure computation with no external API calls at runtime.
Events are passed in as data; no external calendars are fetched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IMPORTANCE_ORDER: dict[str, int] = {
    "높음": 3,
    "보통": 2,
    "낮음": 1,
}
"""Importance level ordering for sorting events."""

KNOWN_EVENT_IMPACTS: dict[str, dict] = {
    "FOMC 금리결정": {
        "direction": "변동",
        "sectors": ["금융", "부동산", "성장주"],
        "magnitude": "높음",
        "description": "금리 동결/인하/인상에 따라 시장 전체 방향 결정",
    },
    "미국 CPI": {
        "direction": "변동",
        "sectors": ["성장주", "기술주", "채권"],
        "magnitude": "높음",
        "description": "인플레이션 지표, 금리 전망에 직접 영향",
    },
    "미국 고용지표": {
        "direction": "변동",
        "sectors": ["경기민감주", "소비재"],
        "magnitude": "높음",
        "description": "노동시장 강도, 경기 방향성 판단 핵심",
    },
    "한국은행 금리결정": {
        "direction": "변동",
        "sectors": ["금융", "부동산", "건설"],
        "magnitude": "높음",
        "description": "국내 금리 방향, 원/달러 환율에 영향",
    },
    "미국 PPI": {
        "direction": "변동",
        "sectors": ["제조업", "원자재"],
        "magnitude": "보통",
        "description": "생산자물가, CPI 선행 지표",
    },
    "중국 PMI": {
        "direction": "변동",
        "sectors": ["소재", "화학", "철강"],
        "magnitude": "보통",
        "description": "중국 제조업 경기, 한국 수출에 영향",
    },
    "미국 소매판매": {
        "direction": "변동",
        "sectors": ["소비재", "유통"],
        "magnitude": "보통",
        "description": "소비 경기 판단, 내수 관련주 영향",
    },
    "옵션만기일": {
        "direction": "변동",
        "sectors": ["전체"],
        "magnitude": "보통",
        "description": "선물/옵션 만기 변동성 확대 주의",
    },
    "한국 수출입동향": {
        "direction": "변동",
        "sectors": ["수출주", "반도체", "자동차"],
        "magnitude": "보통",
        "description": "수출 증감, 반도체/자동차 실적 선행 지표",
    },
    "일본은행 금리결정": {
        "direction": "변동",
        "sectors": ["원/엔 환율", "수출주"],
        "magnitude": "보통",
        "description": "엔화 방향, 수출 경쟁력에 영향",
    },
}
"""Known macro event impact profiles."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class MacroEvent:
    """A single macro event.

    Attributes:
        date: Date string (YYYY-MM-DD).
        name: Event name in Korean.
        country: Country/region ("한국", "미국", "글로벌").
        importance: Importance level ("높음", "보통", "낮음").
        description: Short Korean description.
    """

    date: str = ""
    name: str = ""
    country: str = ""
    importance: str = "보통"
    description: str = ""


@dataclass
class MacroCalendar:
    """Weekly macro event calendar.

    Attributes:
        week_start: Week start date (YYYY-MM-DD, Monday).
        week_end: Week end date (YYYY-MM-DD, Friday).
        events: List of macro events for the week.
        key_focus: Most important event of the week.
        message: Pre-formatted Telegram message.
    """

    week_start: str = ""
    week_end: str = ""
    events: list[MacroEvent] = field(default_factory=list)
    key_focus: str = ""
    message: str = ""


# ---------------------------------------------------------------------------
# Calendar building
# ---------------------------------------------------------------------------

def _parse_date(date_str: str) -> datetime | None:
    """Parse a date string in YYYY-MM-DD format.

    Args:
        date_str: Date string.

    Returns:
        datetime object or None if parsing fails.
    """
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def _get_week_bounds(reference: str = "") -> tuple[str, str]:
    """Compute Monday-Friday bounds for the current or specified week.

    Args:
        reference: A date string (YYYY-MM-DD) within the target week.
            If empty, uses today.

    Returns:
        Tuple of (monday_str, friday_str) in YYYY-MM-DD format.
    """
    if reference:
        dt = _parse_date(reference)
        if dt is None:
            dt = datetime.now()
    else:
        dt = datetime.now()

    monday = dt - timedelta(days=dt.weekday())
    friday = monday + timedelta(days=4)

    return monday.strftime("%Y-%m-%d"), friday.strftime("%Y-%m-%d")


def build_weekly_calendar(
    events: list[dict],
    week_start: str = "",
) -> MacroCalendar:
    """Build a weekly macro calendar from an event list.

    Each event dict should contain keys: "date", "name", "country",
    "importance", "description".  Events outside the target week
    are filtered out.

    Args:
        events: List of event dicts.
        week_start: Optional week start date (YYYY-MM-DD).
            If empty, the current week is used.

    Returns:
        MacroCalendar with events sorted by date and importance.
    """
    monday, friday = _get_week_bounds(week_start)

    # Parse and filter events
    week_events: list[MacroEvent] = []
    for ev_dict in events:
        ev_date = ev_dict.get("date", "")
        if not ev_date:
            continue

        # Filter to the target week
        if ev_date < monday or ev_date > friday:
            continue

        macro_event = MacroEvent(
            date=ev_date,
            name=ev_dict.get("name", ""),
            country=ev_dict.get("country", ""),
            importance=ev_dict.get("importance", "보통"),
            description=ev_dict.get("description", ""),
        )
        week_events.append(macro_event)

    # Sort by date, then by importance (높음 first)
    week_events.sort(
        key=lambda e: (
            e.date,
            -IMPORTANCE_ORDER.get(e.importance, 0),
        ),
    )

    # Determine key focus (most important event of the week)
    key_focus = ""
    if week_events:
        high_events = [
            e for e in week_events if e.importance == "높음"
        ]
        if high_events:
            key_focus = high_events[0].name
        else:
            key_focus = week_events[0].name

    calendar = MacroCalendar(
        week_start=monday,
        week_end=friday,
        events=week_events,
        key_focus=key_focus,
    )
    calendar.message = format_macro_calendar(calendar)

    logger.info(
        "Built macro calendar %s ~ %s: %d events, focus=%s",
        monday, friday, len(week_events), key_focus,
    )

    return calendar


# ---------------------------------------------------------------------------
# Impact estimation
# ---------------------------------------------------------------------------

def get_market_impact(event: MacroEvent) -> dict:
    """Estimate market impact for a macro event.

    Uses the KNOWN_EVENT_IMPACTS mapping for recognized events.
    For unknown events, returns a neutral/generic assessment.

    Args:
        event: MacroEvent to assess.

    Returns:
        Dict with keys:
            "direction": "긍정" / "부정" / "변동" / "중립"
            "sectors": list of affected sector names
            "magnitude": "높음" / "보통" / "낮음"
            "description": Korean description of the expected impact
    """
    # Try exact match first
    impact = KNOWN_EVENT_IMPACTS.get(event.name)
    if impact:
        return dict(impact)

    # Try partial match
    for known_name, known_impact in KNOWN_EVENT_IMPACTS.items():
        if known_name in event.name or event.name in known_name:
            return dict(known_impact)

    # Fallback: generic assessment based on importance
    if event.importance == "높음":
        return {
            "direction": "변동",
            "sectors": ["전체"],
            "magnitude": "보통",
            "description": f"{event.name}: 주요 이벤트, 변동성 확대 가능",
        }

    return {
        "direction": "중립",
        "sectors": [],
        "magnitude": "낮음",
        "description": f"{event.name}: 시장 영향 제한적",
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

_COUNTRY_FLAG: dict[str, str] = {
    "한국": "KR",
    "미국": "US",
    "글로벌": "GL",
    "중국": "CN",
    "일본": "JP",
    "유럽": "EU",
}

_IMPORTANCE_MARKER: dict[str, str] = {
    "높음": "[중요]",
    "보통": "",
    "낮음": "",
}


def format_macro_calendar(calendar: MacroCalendar) -> str:
    """Format a weekly macro calendar for Telegram.

    Produces clean Korean text without any bold (**) formatting.
    Uses "주호님" for the user greeting.

    Args:
        calendar: MacroCalendar to format.

    Returns:
        Multi-line formatted string suitable for Telegram.

    Example output::

        주호님, 이번 주 매크로 일정입니다.
        기간: 2026-02-23 ~ 2026-02-27

        [핵심 이벤트] FOMC 금리결정

        02/23 (월)
          [중요] US FOMC 금리결정 - 금리 방향 결정
        02/25 (수)
          KR 한국은행 금리결정 - 국내 금리 방향

        이번 주는 FOMC에 주목하세요.
    """
    lines = [
        "주호님, 이번 주 매크로 일정입니다.",
        f"기간: {calendar.week_start} ~ {calendar.week_end}",
    ]

    if calendar.key_focus:
        lines.append("")
        lines.append(f"[핵심 이벤트] {calendar.key_focus}")

    if not calendar.events:
        lines.append("")
        lines.append("이번 주 주요 매크로 이벤트가 없습니다.")
        return "\n".join(lines)

    # Group events by date
    current_date = ""
    day_names = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"}

    for event in calendar.events:
        if event.date != current_date:
            current_date = event.date
            dt = _parse_date(current_date)
            if dt:
                day_name = day_names.get(dt.weekday(), "?")
                date_label = f"{dt.month:02d}/{dt.day:02d} ({day_name})"
            else:
                date_label = current_date
            lines.append("")
            lines.append(date_label)

        # Format individual event line
        country_label = _COUNTRY_FLAG.get(event.country, event.country)
        importance_marker = _IMPORTANCE_MARKER.get(event.importance, "")

        parts = []
        if importance_marker:
            parts.append(importance_marker)
        parts.append(country_label)
        parts.append(event.name)

        event_line = " ".join(parts)
        if event.description:
            event_line += f" - {event.description}"

        lines.append(f"  {event_line}")

    lines.append("")

    if calendar.key_focus:
        lines.append(f"이번 주는 {calendar.key_focus}에 주목하세요.")
    else:
        lines.append("이번 주 매크로 일정을 참고하세요.")

    return "\n".join(lines)


def format_macro_event_alert(
    event: MacroEvent,
    actual_value: str = "",
    expected: str = "",
) -> str:
    """Format an individual macro event result alert.

    Used when a macro event occurs and actual data is released,
    allowing comparison with expectations.

    Args:
        event: The MacroEvent that occurred.
        actual_value: Actual released value as a string.
        expected: Market expectation as a string.

    Returns:
        Formatted Telegram message without bold (**) formatting.

    Example output::

        [매크로 속보] 미국 CPI
        발표: +3.2% (예상: +3.0%)
        중요도: 높음
        내용: 인플레이션 지표, 금리 전망에 직접 영향

        주호님, 예상치를 상회했습니다. 시장 영향을 확인하세요.
    """
    country_label = _COUNTRY_FLAG.get(event.country, event.country)

    lines = [
        f"[매크로 속보] {country_label} {event.name}",
    ]

    if actual_value and expected:
        lines.append(f"발표: {actual_value} (예상: {expected})")
    elif actual_value:
        lines.append(f"발표: {actual_value}")

    lines.append(f"중요도: {event.importance}")

    if event.description:
        lines.append(f"내용: {event.description}")

    # Impact assessment
    impact = get_market_impact(event)
    sectors = impact.get("sectors", [])
    if sectors:
        lines.append(f"영향 섹터: {', '.join(sectors)}")

    lines.append("")

    # User-facing message
    if actual_value and expected:
        lines.append(
            "주호님, 매크로 지표가 발표되었습니다. "
            "시장 반응을 확인하세요."
        )
    else:
        lines.append(
            "주호님, 매크로 이벤트 발생입니다. 참고하세요."
        )

    return "\n".join(lines)
