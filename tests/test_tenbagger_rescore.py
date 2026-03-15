import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


class _DummyScore:
    def __init__(self, score: float):
        self.tenbagger_score = score

    def to_dict(self):
        return {
            "tenbagger_score": self.tenbagger_score,
            "tam_score": 0,
            "policy_score": 0,
            "moat_score": 0,
            "revenue_score": 0,
            "discovery_score": 0,
            "momentum_score": 0,
            "consensus_score": 0,
            "price_at_score": 0,
        }


class _FakeDB:
    def __init__(self):
        self.universe = [
            {"ticker": "105840", "name": "우진", "market": "KRX", "sector": "nuclear_smr", "tenbagger_score": 88},
            {"ticker": "083650", "name": "비에이치아이", "market": "KRX", "sector": "nuclear_smr", "tenbagger_score": 87},
        ]
        self.saved_runs = []

    def get_tenbagger_universe(self):
        return [dict(item) for item in self.universe]

    def get_supply_demand(self, ticker, days=20):
        return []

    def get_tenbagger_catalysts(self, ticker=None, status="pending"):
        return []

    def get_tenbagger_score_trend(self, ticker, weeks=8):
        previous = {"105840": 88, "083650": 87}[ticker]
        return [{"ticker": ticker, "score_date": "2026-03-09", "tenbagger_score": previous}]

    def upsert_tenbagger_universe(self, ticker, **kwargs):
        for item in self.universe:
            if item["ticker"] == ticker:
                item.update(kwargs)
                break

    def save_tenbagger_score(self, ticker, score_date, **scores):
        return None

    def upsert_job_run(self, name, day, status="success", message=""):
        self.saved_runs.append((name, day, status, message))


def test_job_tenbagger_rescore_reports_key_changes():
    from kstock.bot.mixins.scheduler import SchedulerMixin

    mixin = SchedulerMixin.__new__(SchedulerMixin)
    mixin.db = _FakeDB()
    mixin.chat_id = 123

    context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

    def _compute(**kwargs):
        if kwargs["ticker"] == "105840":
            return _DummyScore(92)
        return _DummyScore(89)

    with patch(
        "kstock.signal.tenbagger_screener.compute_tenbagger_score",
        side_effect=_compute,
    ), patch(
        "kstock.signal.tenbagger_screener.format_sector_summary",
        return_value="섹터 요약",
    ):
        asyncio.run(mixin.job_tenbagger_rescore(context))

    sent_text = context.bot.send_message.await_args.kwargs["text"]
    assert "상향 2 · 하향 0 · 유지 0" in sent_text
    assert "평균 변화 +3.0점" in sent_text
    assert "우진: 88 → 92 (+4.0점)" in sent_text
    assert "비에이치아이: 87 → 89 (+2.0점)" in sent_text
    assert "섹터 요약" in sent_text
