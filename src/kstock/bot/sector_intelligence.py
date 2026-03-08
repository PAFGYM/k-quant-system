"""v9.5.4 섹터 딥다이브 인텔리전스 — 애널리스트급 심층 섹터 분석.

주호님의 집중 투자 스타일을 서포트:
- 국내외 기관 리포트 통합 분석
- 글로벌 밸류체인 + 피어 비교
- 유튜브 방송 인사이트 통합
- 자체 퀀트봇 리포트 생성 (기관 이상 수준)
- 섹터별 핵심 지표 추적

단순 데이터 나열이 아닌, AI가 분석·종합·판단한 리서치 리포트.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from kstock.core.tz import KST

logger = logging.getLogger(__name__)

# ── 섹터 정의: 딥다이브 가능한 섹터 목록 ──────────────────────────

SECTOR_DEEP_DIVE_CONFIG: dict[str, dict] = {
    "반도체": {
        "label": "반도체/HBM",
        "emoji": "🔬",
        "keywords": ["반도체", "HBM", "메모리", "파운드리", "DRAM", "NAND", "AI칩"],
        "domestic_tickers": ["005930", "000660", "042700"],
        "global_peers": ["TSM", "INTC", "NVDA", "MU", "ASML", "AMAT", "LRCX", "AMD"],
        "etf_code": "091160",
        "key_metrics": [
            "DRAM 현물가 추이", "NAND 현물가 추이", "HBM 가격 동향",
            "AI 서버 출하량", "TSMC 월간매출", "글로벌 파운드리 가동률",
        ],
        "value_chain": {
            "upstream": ["장비(ASML,AMAT,LRCX)", "소재(동진쎄미,솔브레인)"],
            "midstream": ["메모리(삼성전자,SK하이닉스)", "파운드리(삼성전자)"],
            "downstream": ["AI서버(NVDA,AMD)", "스마트폰(Apple,삼성)", "자동차"],
        },
    },
    "2차전지": {
        "label": "2차전지/EV",
        "emoji": "🔋",
        "keywords": ["2차전지", "배터리", "양극재", "음극재", "전해질", "분리막", "리튬"],
        "domestic_tickers": ["373220", "006400", "003670", "247540", "086520"],
        "global_peers": ["TSLA", "ALB", "SQM", "BYDDY", "PCRFY"],
        "etf_code": "305540",
        "key_metrics": [
            "리튬 현물가", "니켈 가격", "코발트 가격",
            "미국 EV 판매량", "유럽 EV 판매량", "중국 NEV 판매량",
            "전고체 배터리 개발 진행", "IRA 보조금 정책",
        ],
        "value_chain": {
            "upstream": ["원자재(ALB,SQM,포스코퓨처엠)", "장비(에코프로비엠)"],
            "midstream": ["셀(LG에너지,삼성SDI,SK이노)", "소재(에코프로,엘앤에프)"],
            "downstream": ["완성차(현대차,기아,TSLA)", "ESS", "전동공구"],
        },
    },
    "바이오": {
        "label": "바이오/제약",
        "emoji": "🧬",
        "keywords": ["바이오", "제약", "CDMO", "바이오시밀러", "신약", "FDA", "임상"],
        "domestic_tickers": ["207940", "068270", "326030", "028300", "196170"],
        "global_peers": ["LLY", "NVO", "AMGN", "REGN", "TEVA"],
        "etf_code": "244580",
        "key_metrics": [
            "FDA 승인 일정", "특허 만료(Patent Cliff)",
            "CDMO 수주잔고", "바이오시밀러 시장규모",
            "글로벌 신약 파이프라인", "ADC/GLP-1 시장 동향",
        ],
        "value_chain": {
            "upstream": ["원료의약품(API)", "바이오 장비"],
            "midstream": ["CDMO(삼성바이오)", "제조(셀트리온,SK바이오)"],
            "downstream": ["글로벌 제약사 위탁", "병원/유통"],
        },
    },
    "방산": {
        "label": "방산/조선/항공",
        "emoji": "🛡️",
        "keywords": ["방산", "조선", "방위", "무기", "드론", "항공", "함정", "미사일"],
        "domestic_tickers": ["012450", "079550", "047810", "329180", "329660"],
        "global_peers": ["LMT", "RTX", "NOC", "BA", "GD", "HII"],
        "etf_code": None,
        "key_metrics": [
            "한국 방산 수출액", "국방예산 증가율",
            "폴란드/중동 수주", "KF-21 양산 일정",
            "해군 함정 수주", "드론 관련 정책",
            "글로벌 지정학 긴장도",
        ],
        "value_chain": {
            "upstream": ["소재/부품(두산)", "전자장비"],
            "midstream": ["체계종합(한화에어로,KAI)", "미사일(LIG넥스원)"],
            "downstream": ["정부 조달", "수출(폴란드,사우디,호주)"],
        },
    },
    "자동차": {
        "label": "자동차/EV/모빌리티",
        "emoji": "🚗",
        "keywords": ["자동차", "전기차", "EV", "완성차", "자율주행", "수소차"],
        "domestic_tickers": ["005380", "000270", "012330"],
        "global_peers": ["TM", "TSLA", "F", "GM", "RIVN", "XPEV"],
        "etf_code": "091170",
        "key_metrics": [
            "글로벌 판매량", "미국 EV 점유율", "IRA 보조금",
            "수소차 보급", "자율주행 규제", "인도/동남아 시장",
        ],
        "value_chain": {
            "upstream": ["배터리(LG에너지,삼성SDI)", "반도체(차량용)"],
            "midstream": ["완성차(현대차,기아)", "부품(현대모비스)"],
            "downstream": ["딜러십", "모빌리티서비스", "충전인프라"],
        },
    },
    "AI/플랫폼": {
        "label": "AI/소프트웨어/플랫폼",
        "emoji": "🤖",
        "keywords": ["AI", "인공지능", "플랫폼", "클라우드", "LLM", "GPU", "로봇"],
        "domestic_tickers": ["035420", "035720", "018260", "036570"],
        "global_peers": ["GOOGL", "META", "MSFT", "AMZN", "BIDU"],
        "etf_code": "098560",
        "key_metrics": [
            "AI 서비스 MAU", "클라우드 매출 성장",
            "광고 매출 추이", "LLM 비용 효율",
            "AI 인프라 투자(CAPEX)", "글로벌 AI 규제 동향",
        ],
        "value_chain": {
            "upstream": ["AI칩(NVDA,AMD)", "HBM(SK하이닉스)"],
            "midstream": ["클라우드(NAVER,카카오)", "SW개발"],
            "downstream": ["광고", "커머스", "핀테크", "콘텐츠"],
        },
    },
}


# ── 데이터 수집 함수들 ────────────────────────────────────────────


def _gather_broker_reports(db, sector_config: dict) -> list[dict]:
    """증권사 리포트에서 섹터 관련 내용 추출."""
    keywords = sector_config["keywords"]
    try:
        reports = db.get_reports_by_sector(keywords, limit=15)
        return reports or []
    except Exception as e:
        logger.debug("broker reports gather failed: %s", e)
        return []


def _gather_youtube_insights(db, sector_config: dict) -> list[dict]:
    """유튜브 인텔리전스에서 섹터 관련 인사이트 추출."""
    try:
        yt_data = db.get_recent_youtube_intelligence(hours=72, limit=20)
        if not yt_data:
            return []

        keywords = [k.lower() for k in sector_config["keywords"]]
        relevant = []
        for yt in yt_data:
            # mentioned_sectors 체크
            sectors = yt.get("mentioned_sectors", [])
            if isinstance(sectors, str):
                try:
                    sectors = json.loads(sectors)
                except Exception:
                    sectors = []
            sector_match = any(
                any(kw in s.lower() for kw in keywords)
                for s in sectors if isinstance(s, str)
            )

            # mentioned_tickers 체크
            tickers = yt.get("mentioned_tickers", [])
            if isinstance(tickers, str):
                try:
                    tickers = json.loads(tickers)
                except Exception:
                    tickers = []
            ticker_match = any(
                t.get("ticker", "") in sector_config.get("domestic_tickers", [])
                for t in tickers if isinstance(t, dict)
            )

            # title/summary 키워드 체크
            title = (yt.get("title", "") + " " + yt.get("full_summary", "")).lower()
            text_match = any(kw in title for kw in keywords)

            if sector_match or ticker_match or text_match:
                relevant.append(yt)

        return relevant[:5]
    except Exception as e:
        logger.debug("youtube insights gather failed: %s", e)
        return []


def _gather_global_news(db, sector_config: dict) -> list[dict]:
    """글로벌 뉴스에서 섹터 관련 내용 추출."""
    try:
        keywords = sector_config["keywords"]
        cutoff = (datetime.now() - timedelta(hours=72)).strftime("%Y-%m-%d %H:%M:%S")
        with db._connect() as conn:
            rows = conn.execute(
                "SELECT title, content_summary, url, source, created_at "
                "FROM global_news WHERE created_at >= ? "
                "ORDER BY created_at DESC LIMIT 100",
                (cutoff,),
            ).fetchall()
        if not rows:
            return []

        relevant = []
        for row in rows:
            d = dict(row) if hasattr(row, "keys") else {
                "title": row[0], "content_summary": row[1],
                "url": row[2], "source": row[3], "created_at": row[4],
            }
            text = (d.get("title", "") + " " + d.get("content_summary", "")).lower()
            if any(kw.lower() in text for kw in keywords):
                relevant.append(d)
        return relevant[:10]
    except Exception as e:
        logger.debug("global news gather failed: %s", e)
        return []


def _gather_sector_momentum(db, sector_config: dict) -> dict:
    """섹터 ETF 모멘텀 데이터 조회."""
    try:
        snapshots = db.get_sector_snapshots(limit=1)
        if not snapshots:
            return {}
        latest = snapshots[0]
        sectors_json = json.loads(latest.get("sectors_json", "[]"))
        for s in sectors_json:
            if isinstance(s, dict) and s.get("sector", "") == sector_config.get("label", "").split("/")[0]:
                return s
            # partial match
            sector_name = sector_config.get("label", "").split("/")[0]
            if isinstance(s, dict) and sector_name in s.get("sector", ""):
                return s
        return {}
    except Exception as e:
        logger.debug("sector momentum gather failed: %s", e)
        return {}


def _gather_event_adjustments(db, sector_config: dict) -> list[dict]:
    """활성 이벤트 점수 조정 중 해당 섹터 관련."""
    try:
        now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        with db._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM event_score_adjustments WHERE expires_at > ?",
                (now_str,),
            ).fetchall()
        if not rows:
            return []

        keywords = [k.lower() for k in sector_config["keywords"]]
        relevant = []
        for row in rows:
            d = dict(row)
            sectors_str = d.get("affected_sectors", "[]")
            try:
                sectors = json.loads(sectors_str) if isinstance(sectors_str, str) else sectors_str
            except Exception:
                sectors = []
            if any(any(kw in s.lower() for kw in keywords) for s in sectors if isinstance(s, str)):
                d["affected_sectors"] = sectors
                relevant.append(d)
        return relevant
    except Exception as e:
        logger.debug("event adjustments gather failed: %s", e)
        return []


def _gather_holdings_in_sector(db, sector_config: dict) -> list[dict]:
    """포트폴리오에서 해당 섹터 보유 종목."""
    try:
        holdings = db.get_active_holdings()
        if not holdings:
            return []
        domestic = set(sector_config.get("domestic_tickers", []))
        return [h for h in holdings if h.get("ticker", "") in domestic]
    except Exception as e:
        logger.debug("holdings gather failed: %s", e)
        return []


async def _gather_global_peer_data(sector_config: dict) -> list[dict]:
    """글로벌 피어 주가 데이터 (yfinance)."""
    peers = sector_config.get("global_peers", [])
    if not peers:
        return []
    try:
        import yfinance as yf
        import asyncio

        def _fetch():
            results = []
            for ticker in peers[:8]:
                try:
                    stock = yf.Ticker(ticker)
                    info = stock.info or {}
                    hist = stock.history(period="1mo")
                    if hist.empty:
                        continue

                    current = float(hist["Close"].iloc[-1])
                    prev_1w = float(hist["Close"].iloc[-6]) if len(hist) >= 6 else current
                    prev_1m = float(hist["Close"].iloc[0])

                    results.append({
                        "ticker": ticker,
                        "name": info.get("shortName", ticker),
                        "price": current,
                        "return_1w": round((current - prev_1w) / prev_1w * 100, 2),
                        "return_1m": round((current - prev_1m) / prev_1m * 100, 2),
                        "market_cap_b": round(info.get("marketCap", 0) / 1e9, 1),
                        "pe_ratio": info.get("trailingPE", 0),
                        "currency": info.get("currency", "USD"),
                    })
                except Exception:
                    continue
            return results

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _fetch)
    except Exception as e:
        logger.debug("global peer data fetch failed: %s", e)
        return []


# ── 종합 데이터 수집 ─────────────────────────────────────────────


async def gather_sector_research(
    db, sector_key: str, include_peers: bool = True,
) -> dict[str, Any]:
    """섹터 딥다이브용 전체 데이터 수집.

    Args:
        db: Database store
        sector_key: SECTOR_DEEP_DIVE_CONFIG의 키
        include_peers: 글로벌 피어 데이터 포함 여부 (API 호출)

    Returns:
        수집된 모든 데이터를 담은 dict
    """
    config = SECTOR_DEEP_DIVE_CONFIG.get(sector_key)
    if not config:
        return {"error": f"Unknown sector: {sector_key}"}

    # 동기 데이터 수집 (DB 조회)
    broker_reports = _gather_broker_reports(db, config)
    youtube_insights = _gather_youtube_insights(db, config)
    global_news = _gather_global_news(db, config)
    sector_momentum = _gather_sector_momentum(db, config)
    event_adjs = _gather_event_adjustments(db, config)
    my_holdings = _gather_holdings_in_sector(db, config)

    # 비동기 데이터 (글로벌 피어)
    peer_data = []
    if include_peers:
        try:
            peer_data = await _gather_global_peer_data(config)
        except Exception as e:
            logger.debug("peer data gather failed: %s", e)

    return {
        "sector_key": sector_key,
        "config": config,
        "broker_reports": broker_reports,
        "youtube_insights": youtube_insights,
        "global_news": global_news,
        "sector_momentum": sector_momentum,
        "event_adjustments": event_adjs,
        "my_holdings": my_holdings,
        "global_peers": peer_data,
        "gathered_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
    }


# ── AI 딥다이브 리포트 생성 ──────────────────────────────────────


def _build_deep_dive_prompt(research: dict) -> str:
    """AI 딥다이브 리포트 생성용 프롬프트 구성."""
    config = research["config"]
    sector_label = config["label"]

    parts = [
        f"당신은 한국 증시 전문 수석 애널리스트입니다.",
        f"'{sector_label}' 섹터에 대한 심층 리서치 리포트를 작성하세요.",
        f"국내외 기관 리포트를 분석한 것 이상의 깊이와 통찰을 제공해야 합니다.",
        f"집중 투자자(섹터 전문가)를 위한 보고서로, 피상적 분석은 금지입니다.",
        "",
        "=== 지시사항 ===",
        "1. 단순 데이터 나열이 아닌, 데이터 간 연결고리와 인사이트를 도출",
        "2. 섹터 내 밸류체인 상하류 영향 분석 (누가 수혜, 누가 피해)",
        "3. 글로벌 피어 대비 한국 기업의 밸류에이션 갭 분석",
        "4. 향후 1~3개월 핵심 모니터링 포인트",
        "5. 리스크 요인과 기회 요인을 균형있게",
        "6. 실제 투자 판단에 도움되는 구체적 결론",
        "",
        "=== 출력 형식 (JSON) ===",
        "{",
        '  "executive_summary": "3-5줄 핵심 요약",',
        '  "sector_outlook": "강세/약세/중립 + 이유 (2-3줄)",',
        '  "value_chain_analysis": "밸류체인 분석 (5-8줄, 업스트림→다운스트림 영향)",',
        '  "global_comparison": "글로벌 피어 대비 분석 (3-5줄)",',
        '  "key_catalysts": ["촉매1", "촉매2", "촉매3"],',
        '  "risk_factors": ["리스크1", "리스크2", "리스크3"],',
        '  "monitoring_points": ["모니터링1", "모니터링2", "모니터링3"],',
        '  "investment_thesis": "투자 전략 제안 (3-5줄)",',
        '  "top_picks": [{"name": "종목명", "ticker": "코드", "reason": "이유"}],',
        '  "confidence": 0.8',
        "}",
    ]

    # 데이터 주입
    parts.append("\n=== 수집된 데이터 ===\n")

    # 1. 섹터 모멘텀
    mom = research.get("sector_momentum", {})
    if mom:
        parts.append(f"[섹터 ETF 모멘텀]")
        parts.append(
            f"1주 수익률: {mom.get('return_1w_pct', 'N/A')}%, "
            f"1개월: {mom.get('return_1m_pct', 'N/A')}%, "
            f"3개월: {mom.get('return_3m_pct', 'N/A')}%"
        )
        parts.append(f"모멘텀점수: {mom.get('momentum_score', 'N/A')}, "
                     f"신호: {mom.get('signal', 'N/A')}")
        parts.append("")

    # 2. 밸류체인
    vc = config.get("value_chain", {})
    if vc:
        parts.append("[밸류체인 구조]")
        for pos, desc in vc.items():
            parts.append(f"  {pos}: {', '.join(desc)}")
        parts.append("")

    # 3. 증권사 리포트
    reports = research.get("broker_reports", [])
    if reports:
        parts.append(f"[증권사 리포트 ({len(reports)}건)]")
        for r in reports[:10]:
            broker = r.get("broker", "")
            title = r.get("title", "")
            target = r.get("target_price", 0)
            opinion = r.get("opinion", "")
            name = r.get("summary", "") or r.get("name", "")
            line = f"  - {broker}: {name} | {title}"
            if target:
                line += f" | 목표 {target:,.0f}원"
            if opinion:
                line += f" ({opinion})"
            parts.append(line)
        parts.append("")

    # 4. 글로벌 피어
    peers = research.get("global_peers", [])
    if peers:
        parts.append(f"[글로벌 피어 ({len(peers)}개)]")
        for p in peers:
            parts.append(
                f"  - {p['name']}({p['ticker']}): "
                f"${p['price']:.1f}, 1주 {p['return_1w']:+.1f}%, "
                f"1개월 {p['return_1m']:+.1f}%, "
                f"시총 ${p['market_cap_b']:.0f}B, "
                f"PER {p.get('pe_ratio', 'N/A')}"
            )
        parts.append("")

    # 5. 유튜브 인사이트
    yt = research.get("youtube_insights", [])
    if yt:
        parts.append(f"[유튜브 방송 인사이트 ({len(yt)}건)]")
        for y in yt[:5]:
            source = y.get("source", "")
            outlook = y.get("market_outlook", "")
            impl = y.get("investment_implications", "")
            summary = y.get("full_summary", "")[:200]
            parts.append(f"  - [{source}] 전망: {outlook}")
            if impl:
                parts.append(f"    → {impl[:150]}")
            elif summary:
                parts.append(f"    → {summary[:150]}")
        parts.append("")

    # 6. 글로벌 뉴스
    news = research.get("global_news", [])
    if news:
        parts.append(f"[관련 글로벌 뉴스 ({len(news)}건)]")
        for n in news[:8]:
            title = n.get("title", "")[:80]
            summary = n.get("content_summary", "")[:100]
            parts.append(f"  - {title}")
            if summary:
                parts.append(f"    {summary}")
        parts.append("")

    # 7. 이벤트 조정
    events = research.get("event_adjustments", [])
    if events:
        parts.append("[활성 이벤트 점수 조정]")
        for ev in events:
            adj = ev.get("score_adjustment", 0)
            summary = ev.get("event_summary", "")[:80]
            parts.append(f"  - {summary}: {'%+d' % adj}점")
        parts.append("")

    # 8. 내 보유종목
    holdings = research.get("my_holdings", [])
    if holdings:
        parts.append("[현재 보유종목]")
        for h in holdings:
            name = h.get("name", "")
            ticker = h.get("ticker", "")
            pnl = h.get("pnl_pct", 0) or 0
            qty = h.get("quantity", 0)
            parts.append(f"  - {name}({ticker}): {qty}주, 수익 {pnl:+.1f}%")
        parts.append("")

    # 핵심 지표 리마인더
    metrics = config.get("key_metrics", [])
    if metrics:
        parts.append(f"[핵심 모니터링 지표]\n  {', '.join(metrics)}")

    return "\n".join(parts)


async def generate_sector_deep_dive(
    db,
    sector_key: str,
    anthropic_client=None,
    include_peers: bool = True,
) -> dict[str, Any]:
    """섹터 딥다이브 리포트 생성.

    1. 전체 데이터 수집
    2. AI로 종합 분석 리포트 생성
    3. DB에 캐시 저장

    Returns:
        {report: {...}, research: {...}, error: str|None}
    """
    config = SECTOR_DEEP_DIVE_CONFIG.get(sector_key)
    if not config:
        return {"error": f"Unknown sector: {sector_key}", "report": None}

    # 캐시 체크 (6시간 이내)
    try:
        cached = db.get_sector_deep_dive(sector_key, hours=6)
        if cached:
            logger.info("sector_deep_dive cache hit for %s", sector_key)
            return {"report": cached, "research": None, "error": None, "cached": True}
    except Exception:
        pass

    # 데이터 수집
    research = await gather_sector_research(db, sector_key, include_peers=include_peers)
    if "error" in research:
        return {"error": research["error"], "report": None}

    # AI 리포트 생성
    if not anthropic_client:
        return {
            "error": "No AI client available",
            "report": None,
            "research": research,
        }

    prompt = _build_deep_dive_prompt(research)

    try:
        response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = response.content[0].text.strip()

        # JSON 파싱
        # ```json ... ``` 블록 추출
        if "```json" in raw_text:
            raw_text = raw_text.split("```json")[1].split("```")[0].strip()
        elif "```" in raw_text:
            raw_text = raw_text.split("```")[1].split("```")[0].strip()

        report = json.loads(raw_text)
        report["sector_key"] = sector_key
        report["sector_label"] = config["label"]
        report["generated_at"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

        # DB 저장
        try:
            db.save_sector_deep_dive(
                sector_key=sector_key,
                report_json=json.dumps(report, ensure_ascii=False),
                data_sources=json.dumps({
                    "broker_reports": len(research.get("broker_reports", [])),
                    "youtube_insights": len(research.get("youtube_insights", [])),
                    "global_news": len(research.get("global_news", [])),
                    "global_peers": len(research.get("global_peers", [])),
                    "event_adjustments": len(research.get("event_adjustments", [])),
                }, ensure_ascii=False),
            )
        except Exception as e:
            logger.warning("save_sector_deep_dive failed: %s", e)

        return {"report": report, "research": research, "error": None, "cached": False}

    except json.JSONDecodeError:
        # JSON 파싱 실패 시 raw text 반환
        report = {
            "sector_key": sector_key,
            "sector_label": config["label"],
            "executive_summary": raw_text[:500],
            "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
            "parse_error": True,
        }
        return {"report": report, "research": research, "error": None}
    except Exception as e:
        logger.error("sector deep dive AI generation failed: %s", e)
        return {"error": str(e), "report": None, "research": research}


# ── 텔레그램 포맷 ────────────────────────────────────────────────


def format_deep_dive_telegram(report: dict) -> str:
    """딥다이브 리포트를 텔레그램 메시지로 포맷."""
    if not report:
        return "❌ 리포트 생성 실패"

    config = SECTOR_DEEP_DIVE_CONFIG.get(report.get("sector_key", ""), {})
    emoji = config.get("emoji", "📊")
    label = report.get("sector_label", "섹터")
    generated = report.get("generated_at", "")

    lines = [
        f"{emoji} {label} 딥다이브 리서치",
        "━" * 28,
        f"⏰ {generated}",
    ]

    # Executive Summary
    summary = report.get("executive_summary", "")
    if summary:
        lines.extend(["", "📋 핵심 요약", summary])

    # Sector Outlook
    outlook = report.get("sector_outlook", "")
    if outlook:
        # 전망에 따른 이모지
        if any(kw in outlook for kw in ["강세", "긍정", "상승"]):
            ol_emoji = "📈"
        elif any(kw in outlook for kw in ["약세", "부정", "하락"]):
            ol_emoji = "📉"
        else:
            ol_emoji = "➡️"
        lines.extend(["", f"{ol_emoji} 섹터 전망", outlook])

    # Value Chain Analysis
    vc = report.get("value_chain_analysis", "")
    if vc:
        lines.extend(["", "🔗 밸류체인 분석", vc])

    # Global Comparison
    gc = report.get("global_comparison", "")
    if gc:
        lines.extend(["", "🌍 글로벌 비교", gc])

    # Key Catalysts
    catalysts = report.get("key_catalysts", [])
    if catalysts:
        lines.extend(["", "🚀 핵심 촉매"])
        for c in catalysts[:4]:
            lines.append(f"  • {c}")

    # Risk Factors
    risks = report.get("risk_factors", [])
    if risks:
        lines.extend(["", "⚠️ 리스크 요인"])
        for r in risks[:4]:
            lines.append(f"  • {r}")

    # Monitoring Points
    monitors = report.get("monitoring_points", [])
    if monitors:
        lines.extend(["", "👁️ 모니터링 포인트"])
        for m in monitors[:4]:
            lines.append(f"  • {m}")

    # Investment Thesis
    thesis = report.get("investment_thesis", "")
    if thesis:
        lines.extend(["", "💡 투자 전략", thesis])

    # Top Picks
    picks = report.get("top_picks", [])
    if picks and isinstance(picks, list):
        lines.extend(["", "🎯 주목 종목"])
        for p in picks[:3]:
            if isinstance(p, dict):
                name = p.get("name", "")
                ticker = p.get("ticker", "")
                reason = p.get("reason", "")
                lines.append(f"  • {name}({ticker}): {reason}")

    # Confidence
    conf = report.get("confidence", 0)
    if conf:
        conf_bar = "●" * int(conf * 10) + "○" * (10 - int(conf * 10))
        lines.extend(["", f"확신도: [{conf_bar}] {conf*100:.0f}%"])

    return "\n".join(lines)


def format_deep_dive_for_context(report: dict) -> str:
    """AI 컨텍스트 주입용 요약 포맷."""
    if not report:
        return ""

    label = report.get("sector_label", "")
    summary = report.get("executive_summary", "")
    outlook = report.get("sector_outlook", "")
    thesis = report.get("investment_thesis", "")
    catalysts = report.get("key_catalysts", [])
    risks = report.get("risk_factors", [])

    parts = [f"[섹터 딥다이브: {label}]"]
    if summary:
        parts.append(f"요약: {summary[:200]}")
    if outlook:
        parts.append(f"전망: {outlook[:100]}")
    if catalysts:
        parts.append(f"촉매: {', '.join(c[:30] for c in catalysts[:3])}")
    if risks:
        parts.append(f"리스크: {', '.join(r[:30] for r in risks[:3])}")
    if thesis:
        parts.append(f"전략: {thesis[:150]}")

    return "\n".join(parts)


# ── 유틸리티 ─────────────────────────────────────────────────────


def get_available_sectors() -> list[dict]:
    """딥다이브 가능한 섹터 목록."""
    return [
        {
            "key": key,
            "label": cfg["label"],
            "emoji": cfg["emoji"],
        }
        for key, cfg in SECTOR_DEEP_DIVE_CONFIG.items()
    ]


def detect_user_focus_sectors(db) -> list[str]:
    """유저 포트폴리오에서 집중 투자 중인 섹터 감지."""
    try:
        holdings = db.get_active_holdings()
        if not holdings:
            return []

        sector_counts: dict[str, int] = {}
        for h in holdings:
            ticker = h.get("ticker", "")
            for key, cfg in SECTOR_DEEP_DIVE_CONFIG.items():
                if ticker in cfg.get("domestic_tickers", []):
                    sector_counts[key] = sector_counts.get(key, 0) + 1
                    break

        # 보유 종목이 있는 섹터만 (보유 수 기준 정렬)
        return sorted(sector_counts, key=sector_counts.get, reverse=True)
    except Exception:
        return []
