"""텐배거 후보 이상 거래 탐지 모듈.

v10.1: Isolation Forest + Z-Score 기반 비정상 거래 패턴 감지.
5가지 패턴을 탐지하여 폭등 전조를 조기 포착.

- Volume Surge: 뉴스 없이 거래량 급증 → 세력 진입
- Quiet Accumulation: 좁은 가격 범위 + 기관/외인 연속 순매수 → 장기 매집
- Breakout Setup: BB 스퀴즈 + 거래량 점진 증가 → 폭발 직전
- Short Squeeze: 공매도 비율 높음 + 매수 전환 → 숏커버링 폭등
- Credit Divergence: 신용잔고 증가 + 가격 횡보 → 개인 매집
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    from sklearn.ensemble import IsolationForest
    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False


# ── Data Classes ─────────────────────────────────────────

ANOMALY_TYPES = {
    0: "normal",
    1: "volume_surge",
    2: "quiet_accumulation",
    3: "breakout_setup",
    4: "short_squeeze",
    5: "credit_divergence",
}


@dataclass
class AnomalySignal:
    """이상 거래 탐지 결과."""

    ticker: str = ""
    name: str = ""
    anomaly_score: float = 0.0           # 0~100 (높을수록 이상)
    volume_zscore: float = 0.0           # 거래량 Z-Score
    price_compression: float = 0.0       # 가격 압축도 (0~1, 높을수록 좁은 범위)
    institutional_accumulation: float = 0.0  # 기관 매집 강도 (-1~+1)
    short_squeeze_potential: float = 0.0     # 공매도 스퀴즈 가능성 (0~1)
    signal_type: str = "normal"          # ANOMALY_TYPES 값
    signal_type_encoded: int = 0         # ML 피처용 인코딩
    details: str = ""                    # 설명 문자열
    sub_scores: dict = field(default_factory=dict)  # 세부 점수
    # v10.1.1: 공매도 상환압박 + 외인 매매 성격
    short_cover_pressure: float = 0.0    # 공매도 상환 압박도 (0~100)
    foreign_flow_type: int = 0           # 외인 매매 성격 (0=중립, 1=단기매매, 2=장기매집, 3=공매도)


# ── Anomaly Detector ────────────────────────────────────

class AnomalyDetector:
    """Isolation Forest + 규칙 기반 이상 거래 탐지기.

    각 패턴별 점수를 산출하고 종합 anomaly_score 반환.
    """

    def __init__(self, contamination: float = 0.05):
        self.contamination = contamination
        self._iso_forest = None

    def detect_anomalies(
        self,
        ticker: str,
        ohlcv: pd.DataFrame,
        supply_data: list[dict] | None = None,
        short_data: dict | None = None,
        name: str = "",
    ) -> AnomalySignal:
        """종목의 이상 거래 패턴 탐지.

        Args:
            ticker: 종목 코드.
            ohlcv: 일봉 OHLCV DataFrame (최소 40일, 권장 60일+).
            supply_data: 수급 데이터 (supply_demand 테이블, 최신순).
            short_data: 공매도 데이터 dict.
            name: 종목명.

        Returns:
            AnomalySignal with anomaly_score (0~100).
        """
        signal = AnomalySignal(ticker=ticker, name=name)

        if ohlcv is None or ohlcv.empty or len(ohlcv) < 20:
            return signal

        try:
            close = ohlcv["close"].astype(float)
            volume = ohlcv["volume"].astype(float)
            high = ohlcv["high"].astype(float)
            low = ohlcv["low"].astype(float)
        except Exception:
            return signal

        sub_scores = {}

        # ── 1. Volume Surge (거래량 급증) ─────────────────
        vol_score, vol_zscore = self._score_volume_surge(volume)
        sub_scores["volume_surge"] = vol_score
        signal.volume_zscore = vol_zscore

        # ── 2. Quiet Accumulation (조용한 매집) ───────────
        acc_score, compression = self._score_quiet_accumulation(
            close, volume, supply_data,
        )
        sub_scores["quiet_accumulation"] = acc_score
        signal.price_compression = compression

        # ── 3. Breakout Setup (돌파 직전) ─────────────────
        brk_score = self._score_breakout_setup(close, high, low, volume)
        sub_scores["breakout_setup"] = brk_score

        # ── 4. Short Squeeze (숏스퀴즈) ──────────────────
        sq_score, sq_potential = self._score_short_squeeze(
            close, volume, supply_data, short_data,
        )
        sub_scores["short_squeeze"] = sq_score
        signal.short_squeeze_potential = sq_potential

        # ── 5. Credit Divergence (신용 다이버전스) ────────
        cd_score = self._score_credit_divergence(close, supply_data)
        sub_scores["credit_divergence"] = cd_score

        # ── 6. Isolation Forest 보정 (sklearn 있을 때) ────
        iso_bonus = 0.0
        if _HAS_SKLEARN and len(ohlcv) >= 40:
            iso_bonus = self._isolation_forest_score(ohlcv, supply_data)
            sub_scores["isolation_forest"] = iso_bonus

        # ── 6.5: 공매도 상환 압박도 ──────────────────────
        signal.short_cover_pressure = self._calc_short_cover_pressure(
            close, volume, supply_data, short_data,
        )

        # ── 6.6: 외인 매매 성격 분류 ────────────────────
        signal.foreign_flow_type = self._classify_foreign_flow(
            close, volume, supply_data, short_data,
        )

        # ── 종합 점수 계산 ────────────────────────────────
        # 기관 매집 강도
        signal.institutional_accumulation = self._calc_inst_accumulation(supply_data)

        # 최고 점수 패턴 선택
        max_pattern = max(sub_scores, key=sub_scores.get)
        max_score = sub_scores[max_pattern]

        # 종합: 최고 패턴 60% + 기타 평균 20% + IF 20%
        other_scores = [v for k, v in sub_scores.items()
                        if k != max_pattern and k != "isolation_forest"]
        other_avg = np.mean(other_scores) if other_scores else 0

        composite = max_score * 0.60 + other_avg * 0.20 + iso_bonus * 0.20
        signal.anomaly_score = round(max(0.0, min(100.0, composite)), 1)
        signal.sub_scores = {k: round(v, 1) for k, v in sub_scores.items()}

        # 시그널 타입 결정
        if signal.anomaly_score >= 40:
            type_map = {
                "volume_surge": (1, "volume_surge"),
                "quiet_accumulation": (2, "quiet_accumulation"),
                "breakout_setup": (3, "breakout_setup"),
                "short_squeeze": (4, "short_squeeze"),
                "credit_divergence": (5, "credit_divergence"),
            }
            enc, name_str = type_map.get(max_pattern, (0, "normal"))
            signal.signal_type = name_str
            signal.signal_type_encoded = enc
            signal.details = self._build_details(signal, sub_scores, max_pattern)

        return signal

    # ── Pattern Scorers ──────────────────────────────────

    def _score_volume_surge(self, volume: pd.Series) -> tuple[float, float]:
        """거래량 급증 패턴 점수.

        Z-Score 3+ → 강한 이상 신호.
        """
        if len(volume) < 20:
            return 0.0, 0.0

        recent_vol = float(volume.iloc[-1])
        avg_20 = float(volume.iloc[-20:].mean())
        std_20 = float(volume.iloc[-20:].std())

        if std_20 < 1:
            return 0.0, 0.0

        zscore = (recent_vol - avg_20) / std_20

        # 점진적 증가 패턴도 탐지 (최근 5일 평균 vs 이전 15일)
        recent_5 = float(volume.iloc[-5:].mean())
        prev_15 = float(volume.iloc[-20:-5].mean())
        gradual_ratio = recent_5 / max(prev_15, 1)

        score = 0.0
        if zscore >= 4.0:
            score = 90.0
        elif zscore >= 3.0:
            score = 75.0
        elif zscore >= 2.5:
            score = 60.0
        elif zscore >= 2.0:
            score = 45.0
        elif zscore >= 1.5:
            score = 30.0

        # 점진적 거래량 증가 보너스
        if gradual_ratio >= 2.0:
            score += 15.0
        elif gradual_ratio >= 1.5:
            score += 8.0

        return min(100.0, score), round(zscore, 2)

    def _score_quiet_accumulation(
        self,
        close: pd.Series,
        volume: pd.Series,
        supply_data: list[dict] | None,
    ) -> tuple[float, float]:
        """조용한 매집 패턴 점수.

        가격은 좁은 범위, 기관/외인이 연속 순매수.
        """
        if len(close) < 20:
            return 0.0, 0.0

        # 가격 압축도: 20일 범위 / 평균가 (낮을수록 좁음)
        high_20 = float(close.iloc[-20:].max())
        low_20 = float(close.iloc[-20:].min())
        avg_20 = float(close.iloc[-20:].mean())
        compression = 1.0 - ((high_20 - low_20) / max(avg_20, 1))
        compression = max(0.0, min(1.0, compression))

        # 기관/외인 연속 순매수 일수
        consecutive_buy = 0
        if supply_data:
            for sd in supply_data[:20]:
                foreign = sd.get("foreign_net", 0) or 0
                inst = sd.get("institution_net", 0) or 0
                if foreign > 0 or inst > 0:
                    consecutive_buy += 1
                else:
                    break

        score = 0.0
        # 높은 압축도 + 연속 매수 → 매집 패턴
        if compression >= 0.92 and consecutive_buy >= 5:
            score = 85.0
        elif compression >= 0.90 and consecutive_buy >= 3:
            score = 65.0
        elif compression >= 0.88 and consecutive_buy >= 2:
            score = 45.0
        elif compression >= 0.85 and consecutive_buy >= 1:
            score = 25.0

        # 거래량 감소 + 매집 → 추가 점수
        vol_trend = float(volume.iloc[-5:].mean()) / max(float(volume.iloc[-20:-5].mean()), 1)
        if vol_trend < 0.7 and consecutive_buy >= 3:
            score += 15.0

        return min(100.0, score), round(compression, 4)

    def _score_breakout_setup(
        self,
        close: pd.Series,
        high: pd.Series,
        low: pd.Series,
        volume: pd.Series,
    ) -> float:
        """돌파 직전 패턴 점수.

        BB 스퀴즈 + 거래량 점진 증가 + 고가 접근.
        """
        if len(close) < 20:
            return 0.0

        # 볼린저밴드 폭 (좁을수록 스퀴즈)
        sma20 = float(close.iloc[-20:].mean())
        std20 = float(close.iloc[-20:].std())
        bb_width = (std20 * 2) / max(sma20, 1)

        # 20일 고점 대비 현재가
        high_20d = float(high.iloc[-20:].max())
        current = float(close.iloc[-1])
        near_high = current / max(high_20d, 1)

        # 거래량 트렌드 (최근 5일 vs 이전)
        vol_recent = float(volume.iloc[-5:].mean())
        vol_prev = float(volume.iloc[-20:-5].mean())
        vol_trend = vol_recent / max(vol_prev, 1)

        score = 0.0

        # BB 스퀴즈 (폭 < 4%)
        if bb_width < 0.03:
            score += 40.0
        elif bb_width < 0.04:
            score += 25.0
        elif bb_width < 0.05:
            score += 15.0

        # 고가 근접 (95% 이상)
        if near_high >= 0.98:
            score += 30.0
        elif near_high >= 0.95:
            score += 20.0
        elif near_high >= 0.90:
            score += 10.0

        # 거래량 점진 증가
        if vol_trend >= 1.5:
            score += 20.0
        elif vol_trend >= 1.2:
            score += 10.0

        return min(100.0, score)

    def _score_short_squeeze(
        self,
        close: pd.Series,
        volume: pd.Series,
        supply_data: list[dict] | None,
        short_data: dict | None,
    ) -> tuple[float, float]:
        """공매도 스퀴즈 패턴 점수.

        공매도 비율 높음 + 기관 매수 전환 + 거래량 급증.
        """
        if len(close) < 10:
            return 0.0, 0.0

        short_ratio = 0.0
        if short_data:
            short_ratio = float(short_data.get("short_ratio", 0) or 0)
        elif supply_data:
            for sd in supply_data[:5]:
                sr = sd.get("short_ratio", 0) or 0
                if sr > 0:
                    short_ratio = float(sr)
                    break

        # 기관 매수 전환 확인
        inst_buying = False
        if supply_data and len(supply_data) >= 3:
            recent_inst = sum(
                (sd.get("institution_net", 0) or 0)
                for sd in supply_data[:3]
            )
            inst_buying = recent_inst > 0

        # 거래량 급증
        vol_surge = False
        if len(volume) >= 20:
            recent_vol = float(volume.iloc[-1])
            avg_vol = float(volume.iloc[-20:].mean())
            vol_surge = recent_vol > avg_vol * 2.0

        score = 0.0
        sq_potential = 0.0

        if short_ratio >= 10.0:
            score += 40.0
            sq_potential += 0.4
        elif short_ratio >= 5.0:
            score += 25.0
            sq_potential += 0.25
        elif short_ratio >= 3.0:
            score += 10.0
            sq_potential += 0.1

        if inst_buying:
            score += 25.0
            sq_potential += 0.25

        if vol_surge:
            score += 20.0
            sq_potential += 0.2

        # 가격 반등 시작 (최근 3일 연속 양봉)
        if len(close) >= 5:
            recent_gains = sum(
                1 for i in range(-3, 0)
                if float(close.iloc[i]) > float(close.iloc[i - 1])
            )
            if recent_gains >= 3:
                score += 15.0
                sq_potential += 0.15

        return min(100.0, score), min(1.0, sq_potential)

    def _score_credit_divergence(
        self,
        close: pd.Series,
        supply_data: list[dict] | None,
    ) -> float:
        """신용잔고 다이버전스 패턴 점수.

        신용잔고(개인 매수) 증가 + 가격 횡보 → 개인 매집.
        """
        if not supply_data or len(supply_data) < 5 or len(close) < 20:
            return 0.0

        # 가격 횡보 확인 (20일 변동률 < 5%)
        price_change = abs(
            (float(close.iloc[-1]) - float(close.iloc[-20])) / max(float(close.iloc[-20]), 1)
        )
        is_sideways = price_change < 0.05

        # 개인(retail) 순매수 지속 확인
        retail_buying_days = 0
        for sd in supply_data[:10]:
            retail_net = sd.get("retail_net", 0) or 0
            if retail_net > 0:
                retail_buying_days += 1

        score = 0.0
        if is_sideways and retail_buying_days >= 7:
            score = 70.0
        elif is_sideways and retail_buying_days >= 5:
            score = 50.0
        elif is_sideways and retail_buying_days >= 3:
            score = 30.0
        elif retail_buying_days >= 5:
            score = 20.0

        return min(100.0, score)

    # ── Isolation Forest ─────────────────────────────────

    def _isolation_forest_score(
        self,
        ohlcv: pd.DataFrame,
        supply_data: list[dict] | None,
    ) -> float:
        """Isolation Forest로 다변량 이상치 점수 계산.

        Returns: 0~100 score (높을수록 이상).
        """
        if not _HAS_SKLEARN:
            return 0.0

        try:
            features = self._build_if_features(ohlcv, supply_data)
            if features is None or len(features) < 20:
                return 0.0

            clf = IsolationForest(
                contamination=self.contamination,
                random_state=42,
                n_estimators=100,
            )
            clf.fit(features)

            # 마지막 행의 이상치 점수
            last_score = clf.decision_function(features[-1:])
            # decision_function: 음수 = 이상, 양수 = 정상
            # → 0~100 스케일로 변환
            raw = -float(last_score[0])
            # 보통 -0.5~+0.5 범위 → 0~100
            normalized = (raw + 0.5) * 100
            return max(0.0, min(100.0, normalized))
        except Exception:
            logger.debug("Isolation Forest scoring failed", exc_info=True)
            return 0.0

    def _build_if_features(
        self,
        ohlcv: pd.DataFrame,
        supply_data: list[dict] | None,
    ) -> np.ndarray | None:
        """Isolation Forest용 피처 행렬 구성."""
        try:
            close = ohlcv["close"].astype(float)
            volume = ohlcv["volume"].astype(float)
            high = ohlcv["high"].astype(float)
            low = ohlcv["low"].astype(float)

            n = len(ohlcv)
            if n < 20:
                return None

            features = []
            for i in range(20, n):
                window_close = close.iloc[i - 20:i]
                window_vol = volume.iloc[i - 20:i]

                vol_mean = float(window_vol.mean())
                vol_std = float(window_vol.std())

                row = [
                    # 거래량 비율
                    float(volume.iloc[i]) / max(vol_mean, 1),
                    # 거래량 Z-Score
                    (float(volume.iloc[i]) - vol_mean) / max(vol_std, 1),
                    # 가격 변동률
                    (float(close.iloc[i]) - float(close.iloc[i - 1])) / max(float(close.iloc[i - 1]), 1),
                    # 가격 범위 비율 (장중 변동)
                    (float(high.iloc[i]) - float(low.iloc[i])) / max(float(close.iloc[i]), 1),
                    # 20일 가격 범위 / 평균
                    (float(window_close.max()) - float(window_close.min())) / max(float(window_close.mean()), 1),
                    # 볼린저밴드 폭
                    float(window_close.std()) * 2 / max(float(window_close.mean()), 1),
                    # 5일 수익률
                    (float(close.iloc[i]) - float(close.iloc[max(0, i - 5)])) / max(float(close.iloc[max(0, i - 5)]), 1),
                    # 가격-거래량 상관
                    float(np.corrcoef(
                        window_close.values,
                        window_vol.values,
                    )[0, 1]) if vol_std > 0 else 0,
                ]
                features.append(row)

            return np.array(features, dtype=np.float64)
        except Exception:
            return None

    # ── Short Cover Pressure + Foreign Flow Type ─────────

    def _calc_short_cover_pressure(
        self,
        close: pd.Series,
        volume: pd.Series,
        supply_data: list[dict] | None,
        short_data: dict | None,
    ) -> float:
        """공매도 상환 압박도 계산 (0~100).

        높은 점수 = 숏커버 가능성 높음 (매수 압력 증가).

        핵심 로직:
        1. 공매도 잔고 비율이 높을수록 → 상환 압박 증가
        2. 공매도 잔고가 감소 추세 → 이미 상환 시작 (초기 매수 신호)
        3. 주가 상승 + 공매도 잔고 높음 → 손절 압박 (숏스퀴즈 가능)
        4. 거래량 급증 + 공매도 잔고 감소 → 대량 숏커버 진행 중
        """
        score = 0.0

        # --- 공매도 비율/잔고 기본 정보 수집 ---
        short_ratio = 0.0
        short_balance = 0
        short_balance_ratio = 0.0
        prev_short_balance = 0
        balance_trend = 0.0  # 잔고 변화율

        if short_data:
            short_ratio = float(short_data.get("short_ratio", 0) or 0)
            short_balance = int(short_data.get("short_balance", 0) or 0)
            short_balance_ratio = float(short_data.get("short_balance_ratio", 0) or 0)

        # supply_data에서도 공매도 정보 보완
        if supply_data and len(supply_data) >= 2:
            if short_ratio == 0:
                short_ratio = float(supply_data[0].get("short_ratio", 0) or 0)
            if short_balance == 0:
                short_balance = int(supply_data[0].get("short_balance", 0) or 0)

            # 잔고 추세 계산 (최근 vs 이전)
            recent_balances = []
            older_balances = []
            for i, sd in enumerate(supply_data[:10]):
                sb = int(sd.get("short_balance", 0) or 0)
                if sb > 0:
                    if i < 3:
                        recent_balances.append(sb)
                    elif i < 8:
                        older_balances.append(sb)

            if recent_balances and older_balances:
                avg_recent = np.mean(recent_balances)
                avg_older = np.mean(older_balances)
                if avg_older > 0:
                    balance_trend = (avg_recent - avg_older) / avg_older

        # --- 1. 공매도 잔고 비율 (기본 압박) ---
        if short_balance_ratio >= 5.0:
            score += 35.0  # 잔고비율 5%+ → 강한 상환 압박
        elif short_balance_ratio >= 3.0:
            score += 25.0
        elif short_balance_ratio >= 1.5:
            score += 15.0
        elif short_ratio >= 10.0:
            score += 20.0  # 일별 공매도 비율 10%+
        elif short_ratio >= 5.0:
            score += 10.0

        # --- 2. 잔고 감소 추세 (상환 진행 중) ---
        if balance_trend < -0.10:
            score += 20.0  # 10%+ 감소 → 상환 활발
        elif balance_trend < -0.05:
            score += 12.0

        # --- 3. 주가 상승 + 공매도 잔고 (손절 압박) ---
        if len(close) >= 10:
            price_chg_5d = (float(close.iloc[-1]) - float(close.iloc[-5])) / max(float(close.iloc[-5]), 1)
            if price_chg_5d > 0.05 and (short_balance_ratio >= 2.0 or short_ratio >= 5.0):
                score += 25.0  # 주가 5%+ 상승 + 높은 공매도 → 강한 숏커버 압박
            elif price_chg_5d > 0.03 and (short_balance_ratio >= 1.5 or short_ratio >= 3.0):
                score += 15.0

        # --- 4. 거래량 급증 + 잔고 감소 (대량 숏커버) ---
        if len(volume) >= 20 and balance_trend < -0.05:
            recent_vol = float(volume.iloc[-3:].mean())
            avg_vol = float(volume.iloc[-20:].mean())
            if avg_vol > 0 and recent_vol / avg_vol > 2.0:
                score += 20.0  # 거래량 2배+ + 잔고 감소 → 숏커버 진행

        return max(0.0, min(100.0, round(score, 1)))

    def _classify_foreign_flow(
        self,
        close: pd.Series,
        volume: pd.Series,
        supply_data: list[dict] | None,
        short_data: dict | None,
    ) -> int:
        """외국인 매매 성격 분류.

        Returns:
            0: 중립/데이터 없음
            1: 단기 트레이딩 (짧은 주기 매수/매도 반복)
            2: 장기 매집 (일관된 방향 연속 매수)
            3: 공매도 성격 (매도 + 공매도 비율 상승)
        """
        if not supply_data or len(supply_data) < 5:
            return 0

        # 최근 10일 외인 순매수 패턴 분석
        foreign_nets = []
        for sd in supply_data[:10]:
            fn = sd.get("foreign_net", 0) or 0
            foreign_nets.append(float(fn))

        if not foreign_nets:
            return 0

        # --- 방향 일관성 분석 ---
        pos_days = sum(1 for f in foreign_nets if f > 0)
        neg_days = sum(1 for f in foreign_nets if f < 0)
        total_days = len(foreign_nets)
        direction_changes = sum(
            1 for i in range(1, len(foreign_nets))
            if (foreign_nets[i] > 0) != (foreign_nets[i - 1] > 0)
            and foreign_nets[i] != 0 and foreign_nets[i - 1] != 0
        )

        # --- 공매도 성격 판별 ---
        short_ratio = 0.0
        if short_data:
            short_ratio = float(short_data.get("short_ratio", 0) or 0)
        elif supply_data:
            for sd in supply_data[:3]:
                sr = sd.get("short_ratio", 0) or 0
                if sr > 0:
                    short_ratio = float(sr)
                    break

        # 최근 공매도 비율 변화
        short_ratios = []
        for sd in supply_data[:10]:
            sr = sd.get("short_ratio", 0) or 0
            if sr > 0:
                short_ratios.append(float(sr))

        short_increasing = False
        if len(short_ratios) >= 3:
            recent_avg = np.mean(short_ratios[:3])
            older_avg = np.mean(short_ratios[3:]) if len(short_ratios) > 3 else recent_avg
            short_increasing = recent_avg > older_avg * 1.2

        # --- 분류 로직 ---

        # 타입 3: 공매도 성격
        # 외인 순매도 우세 + 공매도 비율 상승 → 외인이 공매도 중
        if neg_days >= pos_days and short_ratio >= 5.0 and short_increasing:
            return 3

        # 타입 3 (보조): 외인 대규모 매도 + 높은 공매도 비율
        if neg_days >= total_days * 0.7 and short_ratio >= 8.0:
            return 3

        # 타입 2: 장기 매집
        # 일관된 방향 (매수 쪽) + 방향 전환 적음
        if pos_days >= total_days * 0.7 and direction_changes <= 2:
            return 2

        # 타입 2 (보조): 5일 연속 순매수
        consecutive_buy = 0
        for fn in foreign_nets:
            if fn > 0:
                consecutive_buy += 1
            else:
                break
        if consecutive_buy >= 5:
            return 2

        # 타입 1: 단기 트레이딩
        # 방향 전환 잦음 (3회 이상) → 단기 매매
        if direction_changes >= 3:
            return 1

        # 타입 1 (보조): 큰 금액 1회 매수 후 매도 반복
        if total_days >= 5:
            max_abs = max(abs(f) for f in foreign_nets)
            avg_abs = np.mean([abs(f) for f in foreign_nets if f != 0]) if any(f != 0 for f in foreign_nets) else 0
            if avg_abs > 0 and max_abs / avg_abs > 3.0:
                return 1

        return 0

    # ── Helpers ───────────────────────────────────────────

    def _calc_inst_accumulation(self, supply_data: list[dict] | None) -> float:
        """기관 매집 강도 (-1 ~ +1)."""
        if not supply_data:
            return 0.0

        total_foreign = 0
        total_inst = 0
        count = min(len(supply_data), 10)
        for sd in supply_data[:count]:
            total_foreign += (sd.get("foreign_net", 0) or 0)
            total_inst += (sd.get("institution_net", 0) or 0)

        combined = total_foreign + total_inst
        if combined == 0:
            return 0.0

        # 정규화: ±10억 기준
        normalized = combined / 1_000_000_000
        return max(-1.0, min(1.0, normalized))

    def _build_details(
        self,
        signal: AnomalySignal,
        sub_scores: dict,
        max_pattern: str,
    ) -> str:
        """이상 패턴 설명 문자열 생성."""
        pattern_labels = {
            "volume_surge": "🔴 거래량 급증",
            "quiet_accumulation": "🟡 조용한 매집",
            "breakout_setup": "🟠 돌파 직전",
            "short_squeeze": "🔵 숏스퀴즈 후보",
            "credit_divergence": "🟢 신용 다이버전스",
        }

        label = pattern_labels.get(max_pattern, max_pattern)
        parts = [f"{label} (점수: {signal.anomaly_score:.0f})"]

        if signal.volume_zscore > 2.0:
            parts.append(f"거래량 Z={signal.volume_zscore:.1f}")
        if signal.price_compression > 0.88:
            parts.append(f"가격압축 {signal.price_compression:.1%}")
        if signal.institutional_accumulation > 0.3:
            parts.append(f"기관매집 {signal.institutional_accumulation:.2f}")
        if signal.short_squeeze_potential > 0.3:
            parts.append(f"숏스퀴즈 {signal.short_squeeze_potential:.1%}")
        if signal.short_cover_pressure >= 40:
            parts.append(f"상환압박 {signal.short_cover_pressure:.0f}")
        flow_labels = {1: "단기매매", 2: "장기매집", 3: "공매도"}
        if signal.foreign_flow_type in flow_labels:
            parts.append(f"외인: {flow_labels[signal.foreign_flow_type]}")

        return " | ".join(parts)


# ── Batch Scanner ────────────────────────────────────────

async def scan_anomalies(
    all_tickers: list[dict],
    yf_client,
    db,
    threshold: float = 60.0,
    max_results: int = 15,
) -> list[AnomalySignal]:
    """전 종목 이상 거래 스캔 (배치).

    Args:
        all_tickers: [{code, name, market, sector}, ...]
        yf_client: OHLCV 조회 클라이언트.
        db: SQLiteStore.
        threshold: anomaly_score 임계치.
        max_results: 최대 반환 수.

    Returns:
        AnomalySignal 리스트 (점수 내림차순).
    """
    detector = AnomalyDetector()
    results = []

    for stock in all_tickers:
        try:
            ticker = stock["code"]
            ohlcv = await yf_client.get_ohlcv(ticker, stock.get("market", "KOSPI"))
            if ohlcv is None or ohlcv.empty or len(ohlcv) < 20:
                continue

            supply = db.get_supply_demand(ticker, days=20) if db else []

            signal = detector.detect_anomalies(
                ticker, ohlcv, supply,
                name=stock.get("name", ticker),
            )

            if signal.anomaly_score >= threshold:
                results.append(signal)
        except Exception:
            logger.debug("Anomaly scan failed for %s", stock.get("code"), exc_info=True)

    results.sort(key=lambda s: s.anomaly_score, reverse=True)
    return results[:max_results]


def format_anomaly_alert(signals: list[AnomalySignal]) -> str:
    """텔레그램 알림용 포맷."""
    if not signals:
        return ""

    lines = [
        "🔍 *텐배거 후보 탐지*",
        f"━━━━━━━━━━━━━━━━━",
        "",
    ]

    pattern_emoji = {
        "volume_surge": "🔴",
        "quiet_accumulation": "🟡",
        "breakout_setup": "🟠",
        "short_squeeze": "🔵",
        "credit_divergence": "🟢",
        "normal": "⚪",
    }

    for i, s in enumerate(signals, 1):
        emoji = pattern_emoji.get(s.signal_type, "⚪")
        lines.append(
            f"{i}. {emoji} *{s.name}* ({s.ticker})"
        )
        lines.append(
            f"   점수: {s.anomaly_score:.0f}/100 | 유형: {s.signal_type}"
        )
        if s.details:
            lines.append(f"   {s.details}")
        lines.append("")

    lines.append(f"_총 {len(signals)}개 이상 종목 감지_")
    return "\n".join(lines)
