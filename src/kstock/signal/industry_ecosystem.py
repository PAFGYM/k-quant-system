"""v9.0: 산업 생태계 분석 — 한국 핵심 산업 밸류체인 매핑.

단순 주가 분석을 넘어 "이 산업이 어디로 가고 있는지" 판단.
업스트림/다운스트림 연쇄 효과, 글로벌 동종업 비교.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class IndustryChain:
    """산업 밸류체인 정보."""

    sector: str
    position: str  # upstream, midstream, downstream
    peers: list[str]  # 글로벌 경쟁사 티커
    upstream_tickers: list[str]  # 업스트림 종목 코드
    downstream_tickers: list[str]  # 다운스트림 종목 코드
    key_indicators: list[str]  # 핵심 선행지표


# 한국 핵심 산업 밸류체인 매핑
INDUSTRY_MAP: dict[str, IndustryChain] = {
    # 반도체
    "005930": IndustryChain(  # 삼성전자
        sector="반도체",
        position="midstream",
        peers=["TSM", "INTC", "QCOM"],
        upstream_tickers=["ASML", "AMAT"],  # 장비
        downstream_tickers=["009150", "005935"],  # 삼성전기, 삼성전자우
        key_indicators=["DRAM 현물가", "NAND 현물가", "TSMC 월간매출"],
    ),
    "000660": IndustryChain(  # SK하이닉스
        sector="반도체/HBM",
        position="midstream",
        peers=["MU", "TSM"],
        upstream_tickers=["ASML", "LRCX"],
        downstream_tickers=["NVDA", "AMD"],
        key_indicators=["HBM 가격", "AI 서버 출하량", "NVIDIA 실적"],
    ),
    # 2차전지
    "373220": IndustryChain(  # LG에너지솔루션
        sector="2차전지",
        position="midstream",
        peers=["CATL", "PCRFY"],  # CATL, Panasonic
        upstream_tickers=["003670"],  # 포스코퓨처엠
        downstream_tickers=["005380", "000270"],  # 현대차, 기아
        key_indicators=["리튬 가격", "니켈 가격", "미국 EV 판매량"],
    ),
    "003670": IndustryChain(  # 포스코퓨처엠
        sector="2차전지 소재",
        position="upstream",
        peers=["ALB", "SQM"],
        upstream_tickers=[],
        downstream_tickers=["373220", "006400"],  # LG에너지, 삼성SDI
        key_indicators=["리튬 가격", "코발트 가격", "양극재 출하량"],
    ),
    "006400": IndustryChain(  # 삼성SDI
        sector="2차전지",
        position="midstream",
        peers=["CATL", "PCRFY"],
        upstream_tickers=["003670"],
        downstream_tickers=["000270"],  # 기아
        key_indicators=["전고체 배터리 개발", "유럽 EV 판매"],
    ),
    # 바이오
    "207940": IndustryChain(  # 삼성바이오로직스
        sector="바이오/CDMO",
        position="midstream",
        peers=["LLY", "REGN", "WuXi"],
        upstream_tickers=[],
        downstream_tickers=["068270"],  # 셀트리온
        key_indicators=["CDMO 수주잔고", "바이오시밀러 시장규모"],
    ),
    "068270": IndustryChain(  # 셀트리온
        sector="바이오시밀러",
        position="downstream",
        peers=["TEVA", "AMGN"],
        upstream_tickers=["207940"],
        downstream_tickers=[],
        key_indicators=["FDA 승인일정", "특허만료(Patent Cliff)", "유럽 점유율"],
    ),
    # 방산
    "012450": IndustryChain(  # 한화에어로스페이스
        sector="방산/항공",
        position="midstream",
        peers=["LMT", "RTX", "BA"],
        upstream_tickers=[],
        downstream_tickers=["079550"],  # LIG넥스원
        key_indicators=["한국 방산 수출액", "국방예산", "폴란드 계약"],
    ),
    "079550": IndustryChain(  # LIG넥스원
        sector="방산/미사일",
        position="downstream",
        peers=["LMT", "NOC"],
        upstream_tickers=["012450"],
        downstream_tickers=[],
        key_indicators=["미사일 수출", "중동 수주", "지정학 긴장도"],
    ),
    "047810": IndustryChain(  # 한국항공우주(KAI)
        sector="방산/항공기",
        position="midstream",
        peers=["BA", "AIR.PA"],
        upstream_tickers=[],
        downstream_tickers=[],
        key_indicators=["KF-21 양산", "T-50 수출", "국방예산"],
    ),
    # AI/플랫폼
    "035420": IndustryChain(  # NAVER
        sector="AI/플랫폼",
        position="downstream",
        peers=["GOOGL", "META", "BIDU"],
        upstream_tickers=["000660"],  # SK하이닉스 (HBM)
        downstream_tickers=[],
        key_indicators=["AI 서비스 MAU", "클라우드 매출", "검색 광고 성장"],
    ),
    "035720": IndustryChain(  # 카카오
        sector="플랫폼",
        position="downstream",
        peers=["META", "BIDU"],
        upstream_tickers=[],
        downstream_tickers=[],
        key_indicators=["카카오톡 MAU", "광고 매출", "핀테크 성장"],
    ),
    # 자동차
    "005380": IndustryChain(  # 현대차
        sector="자동차/EV",
        position="downstream",
        peers=["TM", "TSLA", "F"],
        upstream_tickers=["373220", "006400"],  # LG에너지, 삼성SDI
        downstream_tickers=[],
        key_indicators=["글로벌 판매량", "미국 EV 점유율", "IRA 보조금"],
    ),
    "000270": IndustryChain(  # 기아
        sector="자동차/EV",
        position="downstream",
        peers=["TM", "TSLA"],
        upstream_tickers=["373220"],
        downstream_tickers=[],
        key_indicators=["EV6 판매", "글로벌 SUV 점유율"],
    ),
}


def get_industry_context(ticker: str) -> str:
    """종목의 산업 생태계 컨텍스트 텍스트 생성.

    Args:
        ticker: 종목 코드.

    Returns:
        산업 생태계 분석 텍스트. 매핑 없으면 빈 문자열.
    """
    chain = INDUSTRY_MAP.get(ticker)
    if not chain:
        return ""

    lines = [f"[산업 생태계: {chain.sector}]"]
    lines.append(f"  위치: {chain.position}")

    if chain.peers:
        lines.append(f"  글로벌 경쟁사: {', '.join(chain.peers)}")

    if chain.upstream_tickers:
        up_names = [INDUSTRY_MAP.get(t, IndustryChain("", "", [], [], [], [])).sector or t
                    for t in chain.upstream_tickers]
        lines.append(f"  업스트림: {', '.join(up_names)}")

    if chain.downstream_tickers:
        down_names = [INDUSTRY_MAP.get(t, IndustryChain("", "", [], [], [], [])).sector or t
                      for t in chain.downstream_tickers]
        lines.append(f"  다운스트림: {', '.join(down_names)}")

    if chain.key_indicators:
        lines.append(f"  선행지표: {', '.join(chain.key_indicators)}")

    return "\n".join(lines)


def get_related_tickers(ticker: str) -> list[str]:
    """종목과 연관된 밸류체인 종목 코드 리스트."""
    chain = INDUSTRY_MAP.get(ticker)
    if not chain:
        return []

    related = set()
    related.update(chain.upstream_tickers)
    related.update(chain.downstream_tickers)

    # 같은 섹터 종목도 추가
    for code, info in INDUSTRY_MAP.items():
        if code != ticker and info.sector == chain.sector:
            related.add(code)

    return list(related)


def format_industry_for_telegram(ticker: str) -> str:
    """텔레그램 표시용 산업 분석 포맷."""
    chain = INDUSTRY_MAP.get(ticker)
    if not chain:
        return ""

    position_emoji = {
        "upstream": "⬆️",
        "midstream": "🔄",
        "downstream": "⬇️",
    }.get(chain.position, "")

    lines = [
        f"🏭 산업: {chain.sector} {position_emoji}{chain.position}",
    ]
    if chain.peers:
        lines.append(f"  vs {', '.join(chain.peers[:3])}")
    if chain.key_indicators:
        lines.append(f"  📌 {', '.join(chain.key_indicators[:3])}")

    return "\n".join(lines)
