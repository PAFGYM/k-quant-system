"""Tests for bot/account_diagnosis.py - Portfolio-level 8-item diagnosis."""

import pytest
from kstock.bot.account_diagnosis import (
    DiagnosisItem,
    AccountDiagnosis,
    diagnose_account,
    format_diagnosis_report,
    format_solution_detail,
    format_account_history,
    _diagnose_returns,
    _diagnose_concentration,
    _diagnose_losses,
    _diagnose_cash_ratio,
    _diagnose_correlation,
    _diagnose_policy_beneficiary,
    _diagnose_timing,
    _grade_to_score,
    _score_to_grade,
    _generate_solutions,
    SECTOR_MAP,
    POLICY_SECTORS,
    KOSDAQ_BONUS_SECTORS,
)


# ---------------------------------------------------------------------------
# Grade helpers
# ---------------------------------------------------------------------------

class TestGradeHelpers:
    """Test grade conversion helpers."""

    def test_grade_to_score(self):
        assert _grade_to_score("A") == 4
        assert _grade_to_score("B") == 3
        assert _grade_to_score("C") == 2
        assert _grade_to_score("D") == 1

    def test_grade_to_score_unknown(self):
        assert _grade_to_score("X") == 2

    def test_score_to_grade_a(self):
        assert _score_to_grade(3.5) == "A"
        assert _score_to_grade(4.0) == "A"

    def test_score_to_grade_b(self):
        assert _score_to_grade(2.5) == "B"
        assert _score_to_grade(3.4) == "B"

    def test_score_to_grade_c(self):
        assert _score_to_grade(1.5) == "C"
        assert _score_to_grade(2.4) == "C"

    def test_score_to_grade_d(self):
        assert _score_to_grade(1.0) == "D"
        assert _score_to_grade(1.4) == "D"


# ---------------------------------------------------------------------------
# 1. Return diagnosis
# ---------------------------------------------------------------------------

class TestDiagnoseReturns:
    """Test return diagnosis vs KOSPI."""

    def test_grade_a_high_alpha(self):
        item = _diagnose_returns(total_profit_pct=10.0, kospi_return_pct=2.0)
        assert item.grade == "A"
        assert "초과" in item.summary

    def test_grade_b_market_level(self):
        item = _diagnose_returns(total_profit_pct=3.0, kospi_return_pct=2.0)
        assert item.grade == "B"
        assert "수준" in item.summary or "알파" in item.summary

    def test_grade_c_underperform(self):
        item = _diagnose_returns(total_profit_pct=-1.0, kospi_return_pct=2.0)
        assert item.grade == "C"
        assert "부진" in item.summary

    def test_grade_d_major_underperform(self):
        item = _diagnose_returns(total_profit_pct=-10.0, kospi_return_pct=2.0)
        assert item.grade == "D"
        assert "크게 부진" in item.summary

    def test_name_is_returns(self):
        item = _diagnose_returns(0.0)
        assert item.name == "수익률"

    def test_emoji_present(self):
        item = _diagnose_returns(10.0, 2.0)
        assert item.emoji != ""


# ---------------------------------------------------------------------------
# 2. Concentration diagnosis
# ---------------------------------------------------------------------------

class TestDiagnoseConcentration:
    """Test concentration diagnosis."""

    def test_empty_holdings(self):
        item = _diagnose_concentration([])
        assert item.grade == "B"

    def test_zero_total_eval(self):
        item = _diagnose_concentration([{"eval_amount": 0}])
        assert item.grade == "B"

    def test_single_stock_30pct_warning(self):
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 35_000_000},
            {"ticker": "000660", "name": "SK하이닉스", "eval_amount": 15_000_000},
            {"ticker": "035420", "name": "NAVER", "eval_amount": 50_000_000},
        ]
        item = _diagnose_concentration(holdings)
        # NAVER is 50% -> D grade
        assert item.grade == "D"

    def test_sector_40pct_warning(self):
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 25_000_000},
            {"ticker": "000660", "name": "SK하이닉스", "eval_amount": 25_000_000},
            {"ticker": "035420", "name": "NAVER", "eval_amount": 25_000_000},
            {"ticker": "207940", "name": "삼성바이오", "eval_amount": 25_000_000},
        ]
        # 반도체 sector 50% -> sector violation
        item = _diagnose_concentration(holdings)
        assert item.grade in ("C", "D")

    def test_well_diversified(self):
        # All different sectors, equal weight -> low HHI
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 10_000_000},   # 반도체
            {"ticker": "035420", "name": "NAVER", "eval_amount": 10_000_000},      # 소프트웨어
            {"ticker": "207940", "name": "삼성바이오", "eval_amount": 10_000_000},  # 바이오
            {"ticker": "005380", "name": "현대차", "eval_amount": 10_000_000},      # 자동차
            {"ticker": "055550", "name": "신한지주", "eval_amount": 10_000_000},    # 금융
            {"ticker": "005490", "name": "POSCO", "eval_amount": 10_000_000},      # 철강
            {"ticker": "017670", "name": "SK텔레콤", "eval_amount": 10_000_000},   # 통신
            {"ticker": "352820", "name": "하이브", "eval_amount": 10_000_000},     # 엔터
        ]
        item = _diagnose_concentration(holdings)
        assert item.grade in ("A", "B")

    def test_name_is_concentration(self):
        item = _diagnose_concentration([])
        assert item.name == "편중도"


# ---------------------------------------------------------------------------
# 3. Loss diagnosis
# ---------------------------------------------------------------------------

class TestDiagnoseLosses:
    """Test loss stock diagnosis."""

    def test_no_losses_grade_a(self):
        holdings = [
            {"name": "삼성전자", "profit_pct": 5.0},
            {"name": "NAVER", "profit_pct": 3.0},
        ]
        item = _diagnose_losses(holdings)
        assert item.grade == "A"
        assert "없음" in item.summary

    def test_small_losses_grade_b(self):
        holdings = [
            {"name": "삼성전자", "profit_pct": -2.0},
        ]
        item = _diagnose_losses(holdings)
        assert item.grade == "B"

    def test_warning_losses_grade_c(self):
        holdings = [
            {"name": "삼성전자", "profit_pct": -7.0},
        ]
        item = _diagnose_losses(holdings)
        assert item.grade == "C"
        assert "손절 검토" in item.summary

    def test_critical_losses_grade_d(self):
        holdings = [
            {"name": "삼성전자", "profit_pct": -12.0},
            {"name": "NAVER", "profit_pct": -15.0},
        ]
        item = _diagnose_losses(holdings)
        assert item.grade == "D"
        assert "즉시" in item.summary
        assert len(item.details) == 2

    def test_name_is_losses(self):
        item = _diagnose_losses([])
        assert item.name == "손실종목"


# ---------------------------------------------------------------------------
# 4. Cash ratio diagnosis
# ---------------------------------------------------------------------------

class TestDiagnoseCashRatio:
    """Test cash ratio diagnosis."""

    def test_zero_eval(self):
        item = _diagnose_cash_ratio(cash=0, total_eval=0)
        assert item.grade == "B"

    def test_appropriate_cash(self):
        # 15% cash, 15% target -> within ±5%
        item = _diagnose_cash_ratio(cash=1_500_000, total_eval=8_500_000, regime_cash_pct=15)
        assert item.grade == "A"
        assert "적정" in item.summary

    def test_excess_cash(self):
        # 40% cash -> excess
        item = _diagnose_cash_ratio(cash=4_000_000, total_eval=6_000_000, regime_cash_pct=15)
        assert item.grade == "C"
        assert "과다" in item.summary

    def test_low_cash(self):
        # 2% cash -> too low
        item = _diagnose_cash_ratio(cash=200_000, total_eval=9_800_000, regime_cash_pct=15)
        assert item.grade in ("C", "D")

    def test_regime_label(self):
        item = _diagnose_cash_ratio(cash=1_000_000, total_eval=9_000_000, regime_cash_pct=15,
                                     regime_label="Balanced")
        assert "Balanced" in item.summary

    def test_name_is_cash(self):
        item = _diagnose_cash_ratio(0, 100)
        assert item.name == "현금비중"


# ---------------------------------------------------------------------------
# 5. Correlation diagnosis
# ---------------------------------------------------------------------------

class TestDiagnoseCorrelation:
    """Test correlation diagnosis."""

    def test_no_correlated_pairs(self):
        holdings = [
            {"ticker": "005930", "name": "삼성전자"},
            {"ticker": "035420", "name": "NAVER"},
            {"ticker": "207940", "name": "삼성바이오"},
        ]
        item = _diagnose_correlation(holdings)
        assert item.grade == "A"

    def test_one_correlated_pair(self):
        holdings = [
            {"ticker": "005930", "name": "삼성전자"},
            {"ticker": "000660", "name": "SK하이닉스"},
            {"ticker": "035420", "name": "NAVER"},
        ]
        item = _diagnose_correlation(holdings)
        assert item.grade == "B"
        assert "반도체" in item.summary

    def test_multiple_correlated_pairs(self):
        holdings = [
            {"ticker": "005930", "name": "삼성전자"},
            {"ticker": "000660", "name": "SK하이닉스"},
            {"ticker": "035420", "name": "NAVER"},
            {"ticker": "035720", "name": "카카오"},
        ]
        item = _diagnose_correlation(holdings)
        assert item.grade == "C"
        assert "동반하락" in item.summary

    def test_unknown_tickers_no_pairs(self):
        holdings = [
            {"ticker": "999999", "name": "Unknown1"},
            {"ticker": "888888", "name": "Unknown2"},
        ]
        item = _diagnose_correlation(holdings)
        # Unknown tickers -> "기타" sector, but multiple "기타" doesn't count
        assert item.grade == "A"

    def test_name_is_correlation(self):
        item = _diagnose_correlation([])
        assert item.name == "상관관계"


# ---------------------------------------------------------------------------
# 7. Policy beneficiary diagnosis
# ---------------------------------------------------------------------------

class TestDiagnosePolicyBeneficiary:
    """Test policy beneficiary diagnosis."""

    def test_empty_holdings(self):
        item = _diagnose_policy_beneficiary([])
        assert item.grade == "B"

    def test_high_policy_ratio(self):
        holdings = [
            {"ticker": "055550", "name": "신한지주"},     # 금융
            {"ticker": "005380", "name": "현대차"},       # 자동차
            {"ticker": "207940", "name": "삼성바이오"},   # 바이오
        ]
        item = _diagnose_policy_beneficiary(holdings)
        assert item.grade == "A"

    def test_no_policy_stocks(self):
        holdings = [
            {"ticker": "005930", "name": "삼성전자"},     # 반도체
            {"ticker": "005490", "name": "POSCO"},        # 철강
        ]
        item = _diagnose_policy_beneficiary(holdings)
        assert item.grade == "D"
        assert "없음" in item.summary

    def test_name_is_policy(self):
        item = _diagnose_policy_beneficiary([])
        assert item.name == "정책수혜"


# ---------------------------------------------------------------------------
# 8. Timing diagnosis
# ---------------------------------------------------------------------------

class TestDiagnoseTiming:
    """Test timing diagnosis."""

    def test_no_action_needed(self):
        holdings = [
            {"name": "삼성전자", "profit_pct": 5.0},
        ]
        item = _diagnose_timing(holdings)
        assert item.grade == "A"
        assert "없음" in item.summary

    def test_sell_candidates(self):
        holdings = [
            {"name": "삼성전자", "profit_pct": -12.0},
        ]
        item = _diagnose_timing(holdings)
        assert item.grade == "C"
        assert "손절" in item.summary

    def test_add_buy_candidates(self):
        holdings = [
            {"name": "삼성전자", "profit_pct": -3.0},
        ]
        item = _diagnose_timing(holdings)
        assert item.grade == "B"
        assert len(item.details) >= 1

    def test_trailing_stop(self):
        holdings = [
            {"name": "삼성전자", "profit_pct": 20.0},
        ]
        item = _diagnose_timing(holdings)
        assert item.grade == "B"
        assert any("트레일링" in d for d in item.details)

    def test_name_is_timing(self):
        item = _diagnose_timing([])
        assert item.name == "타이밍"


# ---------------------------------------------------------------------------
# Full diagnosis
# ---------------------------------------------------------------------------

class TestDiagnoseAccount:
    """Test full account diagnosis."""

    def test_returns_8_items(self):
        diag = diagnose_account([], total_profit_pct=0)
        assert len(diag.items) == 8

    def test_overall_grade_present(self):
        diag = diagnose_account([], total_profit_pct=0)
        assert diag.overall_grade in ("A", "B", "C", "D")

    def test_overall_score_0_100(self):
        diag = diagnose_account([], total_profit_pct=0)
        assert 0 <= diag.overall_score <= 100

    def test_good_portfolio_high_grade(self):
        holdings = [
            {"ticker": "055550", "name": "신한지주", "eval_amount": 10_000_000,
             "avg_price": 40000, "quantity": 250, "profit_pct": 5.0},
            {"ticker": "005380", "name": "현대차", "eval_amount": 10_000_000,
             "avg_price": 200000, "quantity": 50, "profit_pct": 8.0},
            {"ticker": "207940", "name": "삼성바이오", "eval_amount": 10_000_000,
             "avg_price": 800000, "quantity": 12, "profit_pct": 3.0},
        ]
        diag = diagnose_account(
            holdings, total_profit_pct=5.0, cash=5_000_000, total_eval=30_000_000,
        )
        assert diag.overall_grade in ("A", "B")

    def test_bad_portfolio_low_grade(self):
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 80_000_000,
             "avg_price": 70000, "quantity": 1000, "profit_pct": -15.0},
            {"ticker": "000660", "name": "SK하이닉스", "eval_amount": 20_000_000,
             "avg_price": 150000, "quantity": 100, "profit_pct": -8.0},
        ]
        diag = diagnose_account(
            holdings, total_profit_pct=-13.0, cash=0, total_eval=100_000_000,
        )
        assert diag.overall_grade in ("C", "D")
        assert len(diag.solutions) >= 1

    def test_valuation_placeholder_is_b(self):
        diag = diagnose_account([])
        val_item = [i for i in diag.items if i.name == "밸류에이션"][0]
        assert val_item.grade == "B"
        assert "PER" in val_item.summary or "연동" in val_item.summary

    def test_solutions_generated_for_cd_items(self):
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 90_000_000,
             "avg_price": 70000, "quantity": 1000, "profit_pct": -12.0},
        ]
        diag = diagnose_account(holdings, total_profit_pct=-12.0)
        assert len(diag.solutions) >= 1
        sol_types = [s["type"] for s in diag.solutions]
        assert "stop_loss" in sol_types


# ---------------------------------------------------------------------------
# Solution generation
# ---------------------------------------------------------------------------

class TestGenerateSolutions:
    """Test solution generation from diagnosis items."""

    def test_stop_loss_solution(self):
        items = [DiagnosisItem(name="손실종목", grade="D", summary="2종목 -10%")]
        sols = _generate_solutions(items, [], 0, 0)
        assert any(s["type"] == "stop_loss" for s in sols)
        assert any(s["urgency"] == "critical" for s in sols)

    def test_concentration_solution(self):
        items = [DiagnosisItem(name="편중도", grade="C", summary="삼성 30%")]
        sols = _generate_solutions(items, [], 0, 0)
        assert any(s["type"] == "reduce_concentration" for s in sols)

    def test_cash_excess_solution(self):
        items = [DiagnosisItem(name="현금비중", grade="C", summary="40% 현금 과다")]
        sols = _generate_solutions(items, [], 0, 0)
        assert any(s["type"] == "increase_investment" for s in sols)

    def test_cash_shortage_solution(self):
        items = [DiagnosisItem(name="현금비중", grade="D", summary="2% 현금 부족")]
        sols = _generate_solutions(items, [], 0, 0)
        assert any(s["type"] == "increase_cash" for s in sols)

    def test_policy_solution(self):
        items = [DiagnosisItem(name="정책수혜", grade="D", summary="없음")]
        sols = _generate_solutions(items, [], 0, 0)
        assert any(s["type"] == "add_policy_stock" for s in sols)

    def test_no_solution_for_a_b_items(self):
        items = [
            DiagnosisItem(name="수익률", grade="A", summary="Good"),
            DiagnosisItem(name="편중도", grade="B", summary="OK"),
        ]
        sols = _generate_solutions(items, [], 0, 0)
        assert sols == []


# ---------------------------------------------------------------------------
# Formatting - diagnosis report
# ---------------------------------------------------------------------------

class TestFormatDiagnosisReport:
    """Test diagnosis report formatting."""

    def test_no_bold(self):
        diag = diagnose_account([], total_profit_pct=0)
        text = format_diagnosis_report(diag)
        assert "**" not in text

    def test_contains_juho(self):
        diag = diagnose_account([], total_profit_pct=0)
        text = format_diagnosis_report(diag)
        assert "주호님" in text

    def test_contains_grade(self):
        diag = diagnose_account([], total_profit_pct=0)
        text = format_diagnosis_report(diag)
        assert "종합 등급" in text

    def test_contains_all_item_names(self):
        diag = diagnose_account([], total_profit_pct=0)
        text = format_diagnosis_report(diag)
        for name in ["수익률", "편중도", "손실종목", "현금비중", "상관관계", "밸류에이션", "정책수혜", "타이밍"]:
            assert name in text

    def test_solutions_shown(self):
        holdings = [
            {"ticker": "005930", "name": "삼성전자", "eval_amount": 90_000_000,
             "avg_price": 70000, "quantity": 1000, "profit_pct": -12.0},
        ]
        diag = diagnose_account(holdings, total_profit_pct=-12.0)
        text = format_diagnosis_report(diag)
        assert "솔루션" in text

    def test_v3_branding(self):
        diag = diagnose_account([])
        text = format_diagnosis_report(diag)
        assert "K-Quant v3.0" in text


# ---------------------------------------------------------------------------
# Formatting - solution detail
# ---------------------------------------------------------------------------

class TestFormatSolutionDetail:
    """Test solution detail formatting."""

    def test_no_solutions(self):
        text = format_solution_detail([])
        assert "주호님" in text
        assert "양호" in text

    def test_with_solutions(self):
        solutions = [
            {"type": "stop_loss", "urgency": "critical",
             "description": "손절 검토: 2종목", "action": "손절선 도달"},
        ]
        text = format_solution_detail(solutions)
        assert "솔루션" in text
        assert "손절" in text
        assert "**" not in text

    def test_urgency_label(self):
        solutions = [
            {"type": "stop_loss", "urgency": "critical", "description": "D", "action": "A"},
            {"type": "reduce_concentration", "urgency": "medium", "description": "D", "action": "A"},
        ]
        text = format_solution_detail(solutions)
        assert "긴급" in text
        assert "권장" in text


# ---------------------------------------------------------------------------
# Formatting - account history
# ---------------------------------------------------------------------------

class TestFormatAccountHistory:
    """Test account history formatting."""

    def test_empty_snapshots(self):
        text = format_account_history([])
        assert "주호님" in text
        assert "기록이 없습니다" in text

    def test_with_snapshots(self):
        snapshots = [
            {"created_at": "2026-02-23T10:00:00", "total_eval": 100_000_000,
             "total_profit_pct": 5.0},
            {"created_at": "2026-02-16T10:00:00", "total_eval": 95_000_000,
             "total_profit_pct": 2.0},
        ]
        text = format_account_history(snapshots)
        assert "계좌 추이" in text
        assert "2026-02-23" in text
        assert "기간 수익률" in text

    def test_no_bold(self):
        text = format_account_history([{"created_at": "2026-02-23", "total_eval": 100, "total_profit_pct": 5}])
        assert "**" not in text


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    """Test module constants."""

    def test_sector_map_has_major_stocks(self):
        assert "005930" in SECTOR_MAP  # 삼성전자
        assert "000660" in SECTOR_MAP  # SK하이닉스
        assert "035420" in SECTOR_MAP  # NAVER

    def test_policy_sectors(self):
        assert "금융" in POLICY_SECTORS
        assert "자동차" in POLICY_SECTORS

    def test_kosdaq_bonus_sectors(self):
        assert "바이오" in KOSDAQ_BONUS_SECTORS
        assert "소프트웨어" in KOSDAQ_BONUS_SECTORS


# ---------------------------------------------------------------------------
# DB integration - solution_tracking
# ---------------------------------------------------------------------------

class TestDBSolutionTracking:
    """Test SQLite solution_tracking CRUD."""

    def test_add_and_get(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        db = SQLiteStore(tmp_path / "test.db")
        sid = db.add_solution("stop_loss", "삼성전자 손절 검토")
        assert sid > 0
        pending = db.get_pending_solutions()
        assert len(pending) == 1
        assert pending[0]["solution_type"] == "stop_loss"

    def test_mark_executed(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        db = SQLiteStore(tmp_path / "test.db")
        sid = db.add_solution("stop_loss", "손절")
        db.mark_solution_executed(sid, profit_change_pct=2.0, alpha_change=1.5)
        pending = db.get_pending_solutions()
        assert len(pending) == 0
        history = db.get_solution_history()
        assert len(history) == 1
        assert history[0]["executed"] == 1
        assert history[0]["profit_change_pct"] == 2.0

    def test_get_solution_stats(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        db = SQLiteStore(tmp_path / "test.db")
        db.add_solution("stop_loss", "A")
        sid2 = db.add_solution("reduce_concentration", "B")
        db.mark_solution_executed(sid2, profit_change_pct=3.0)
        stats = db.get_solution_stats()
        assert stats["total"] == 2
        assert stats["executed"] == 1
        assert stats["effective"] == 1
        assert stats["execution_rate"] == 0.5
        assert stats["effectiveness_rate"] == 1.0

    def test_get_solution_history_limit(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        db = SQLiteStore(tmp_path / "test.db")
        for i in range(5):
            db.add_solution("test", f"solution {i}")
        history = db.get_solution_history(limit=3)
        assert len(history) == 3

    def test_solution_with_snapshot_id(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        db = SQLiteStore(tmp_path / "test.db")
        sid = db.add_solution("stop_loss", "test", before_snapshot_id=42)
        history = db.get_solution_history()
        assert history[0]["before_snapshot_id"] == 42

    def test_stats_empty_db(self, tmp_path):
        from kstock.store.sqlite import SQLiteStore
        db = SQLiteStore(tmp_path / "test.db")
        stats = db.get_solution_stats()
        assert stats["total"] == 0
        assert stats["execution_rate"] == 0
        assert stats["effectiveness_rate"] == 0


# ---------------------------------------------------------------------------
# Bot integration
# ---------------------------------------------------------------------------

class TestBotIntegration:
    """Test bot has required methods and commands."""

    def test_bot_has_history_command(self):
        from kstock.bot.bot import KQuantBot
        assert hasattr(KQuantBot, "cmd_history")

    def test_bot_has_solution_callback(self):
        from kstock.bot.bot import KQuantBot
        assert hasattr(KQuantBot, "_action_solution_detail")

    def test_bot_has_account_diagnosis_sender(self):
        from kstock.bot.bot import KQuantBot
        assert hasattr(KQuantBot, "_send_account_diagnosis")

    def test_import_account_diagnosis(self):
        from kstock.bot.account_diagnosis import (
            diagnose_account,
            format_diagnosis_report,
            format_solution_detail,
            format_account_history,
        )
        assert callable(diagnose_account)
        assert callable(format_diagnosis_report)
        assert callable(format_solution_detail)
        assert callable(format_account_history)


# ---------------------------------------------------------------------------
# Messages.py ML accuracy section
# ---------------------------------------------------------------------------

class TestRecoPerformanceMLSection:
    """Test format_reco_performance with ML accuracy stats."""

    def test_ml_accuracy_shown(self):
        from kstock.bot.messages import format_reco_performance
        stats = {
            "total": 10, "active": 5, "profit": 3, "stop": 2,
            "watch": 0, "avg_closed_pnl": 2.5, "avg_active_pnl": 1.0,
            "ml_accuracy": 72.0, "ml_top5_accuracy": 80.0,
        }
        text = format_reco_performance([], [], [], stats)
        assert "ML 예측 성과" in text
        assert "72%" in text
        assert "80%" in text

    def test_sentiment_shown(self):
        from kstock.bot.messages import format_reco_performance
        stats = {
            "total": 10, "active": 5, "profit": 3, "stop": 2,
            "watch": 0, "avg_closed_pnl": 2.5, "avg_active_pnl": 1.0,
            "sentiment_accuracy": 65.0, "sentiment_pnl_boost": 1.5,
        }
        text = format_reco_performance([], [], [], stats)
        assert "감성분석" in text
        assert "65%" in text
        assert "+1.5%p" in text

    def test_no_ml_stats_no_section(self):
        from kstock.bot.messages import format_reco_performance
        stats = {
            "total": 10, "active": 5, "profit": 3, "stop": 2,
            "watch": 0, "avg_closed_pnl": 2.5, "avg_active_pnl": 1.0,
        }
        text = format_reco_performance([], [], [], stats)
        assert "ML 예측" not in text
        assert "감성분석" not in text

    def test_no_bold_in_performance(self):
        from kstock.bot.messages import format_reco_performance
        stats = {
            "total": 10, "active": 5, "profit": 3, "stop": 2,
            "watch": 0, "avg_closed_pnl": 2.5, "avg_active_pnl": 1.0,
            "ml_accuracy": 72.0, "sentiment_accuracy": 65.0,
        }
        text = format_reco_performance([], [], [], stats)
        assert "**" not in text


# ---------------------------------------------------------------------------
# User preference yaml
# ---------------------------------------------------------------------------

class TestUserPreference:
    """Test user_preference.yaml has diagnosis_learning fields."""

    def test_yaml_has_diagnosis_learning(self):
        import yaml
        with open("config/user_preference.yaml") as f:
            data = yaml.safe_load(f)
        assert "diagnosis_learning" in data
        dl = data["diagnosis_learning"]
        assert "solution_execution_rate" in dl
        assert "solution_effectiveness_rate" in dl
        assert "avg_alpha_change" in dl
        assert "preferred_solution_types" in dl
        assert "diagnosis_count" in dl
