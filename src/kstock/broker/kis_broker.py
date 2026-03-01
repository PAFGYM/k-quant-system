"""KIS OpenAPI broker client for K-Quant v3.0.

Wraps python-kis (pykis) for real/virtual trading.
Gracefully degrades when KIS is not configured or pykis not installed.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from kstock.core.tz import KST

logger = logging.getLogger(__name__)

try:
    from pykis import Api as PykisApi, DomainInfo as PykisDomain
    HAS_PYKIS = True
except (ImportError, TypeError, OSError, Exception):
    HAS_PYKIS = False
    PykisApi = None  # type: ignore
    logger.info("pykis not available; KIS broker disabled")


@dataclass
class OrderResult:
    """Result of a buy/sell order."""

    success: bool
    order_id: str = ""
    message: str = ""
    ticker: str = ""
    quantity: int = 0
    price: float = 0
    order_type: str = ""  # "market" or "limit"


@dataclass
class SafetyLimits:
    """Auto-trade safety limits."""

    max_order_pct: float = 15.0
    max_daily_orders: int = 10
    daily_loss_limit_pct: float = -3.0
    require_confirmation: bool = True
    daily_order_count: int = 0
    daily_pnl_pct: float = 0.0

    def can_order(self, order_pct: float) -> tuple[bool, str]:
        if order_pct > self.max_order_pct:
            return False, f"1회 주문 한도 초과 ({order_pct:.1f}% > {self.max_order_pct}%)"
        if self.daily_order_count >= self.max_daily_orders:
            return False, f"일일 주문 횟수 초과 ({self.daily_order_count}/{self.max_daily_orders})"
        if self.daily_pnl_pct <= self.daily_loss_limit_pct:
            return False, f"일일 손실 한도 도달 ({self.daily_pnl_pct:.1f}%)"
        return True, ""

    def record_order(self) -> None:
        self.daily_order_count += 1

    def reset_daily(self) -> None:
        self.daily_order_count = 0
        self.daily_pnl_pct = 0.0


class KisBroker:
    """KIS OpenAPI broker client."""

    def __init__(self, config_path: str = "config/kis_config.yaml") -> None:
        self.connected = False
        self.mode = "virtual"
        self.kis = None
        self.safety = SafetyLimits()
        self._config_path = config_path

        config = self._load_config()
        if config:
            self._connect(config)
        self._load_safety(config)

    def _load_config(self) -> dict | None:
        path = Path(self._config_path)
        if not path.exists():
            return None
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.error("Failed to load KIS config: %s", e)
            return None

        kis_cfg = data.setdefault("kis", {})
        mode = kis_cfg.get("mode", "virtual")

        # 새 구조 (virtual/real 분리) → 현재 모드의 키를 최상위로 복사
        mode_cfg = kis_cfg.get(mode, {})
        if mode_cfg and mode_cfg.get("app_key"):
            for k in ("hts_id", "app_key", "app_secret", "account"):
                kis_cfg[k] = mode_cfg.get(k, "")

        # .env fallback: yaml 값이 비어있으면 환경변수에서 읽기
        env_map = {
            "app_key": "KIS_APP_KEY",
            "app_secret": "KIS_APP_SECRET",
            "account": "KIS_ACCOUNT_NO",
            "hts_id": "KIS_HTS_ID",
        }
        for yaml_key, env_key in env_map.items():
            if not kis_cfg.get(yaml_key):
                env_val = os.environ.get(env_key, "")
                if env_val:
                    kis_cfg[yaml_key] = env_val

        if not kis_cfg.get("app_key"):
            return None
        return data

    def _connect(self, config: dict) -> None:
        kis_cfg = config.get("kis", {})
        if not kis_cfg.get("app_key"):
            logger.info("KIS app_key not configured; broker disabled")
            return

        if not HAS_PYKIS:
            logger.warning("pykis not installed; cannot connect to KIS")
            return

        self.mode = kis_cfg.get("mode", "virtual")
        try:
            is_virtual = (self.mode == "virtual")
            domain = PykisDomain(kind="virtual" if is_virtual else "real")
            key_info = {
                "appkey": kis_cfg.get("app_key", ""),
                "appsecret": kis_cfg.get("app_secret", ""),
            }
            account = kis_cfg.get("account", "")
            account_info = None
            if account:
                parts = account.split("-")
                account_info = {
                    "account_no": parts[0] if parts else account,
                    "product_code": parts[1] if len(parts) > 1 else "01",
                }
            self.kis = PykisApi(
                key_info=key_info,
                domain_info=domain,
                account_info=account_info,
            )
            self.kis.create_token()
            self.connected = True
            logger.info("KIS broker connected (mode=%s)", self.mode)
        except Exception as e:
            logger.error("KIS connection failed: %s", e)
            self.connected = False

    def _load_safety(self, config: dict | None) -> None:
        if not config:
            return
        safety = config.get("safety", {})
        self.safety.max_order_pct = safety.get("max_order_pct", 15.0)
        self.safety.max_daily_orders = safety.get("max_daily_orders", 10)
        self.safety.daily_loss_limit_pct = safety.get("daily_loss_limit_pct", -3.0)
        self.safety.require_confirmation = safety.get("require_confirmation", True)

    def save_credentials(
        self, hts_id: str, app_key: str, app_secret: str, account: str,
        mode: str = "virtual",
    ) -> bool:
        """Save KIS credentials to config and attempt connection.

        Args:
            mode: "virtual" or "real" - which mode to save credentials for.
        """
        path = Path(self._config_path)
        try:
            if path.exists():
                with open(path) as f:
                    data = yaml.safe_load(f) or {}
            else:
                data = {}

            kis_cfg = data.setdefault("kis", {})
            kis_cfg["mode"] = mode

            # 모드별 저장
            mode_cfg = kis_cfg.setdefault(mode, {})
            mode_cfg["hts_id"] = hts_id
            mode_cfg["app_key"] = app_key
            mode_cfg["app_secret"] = app_secret
            mode_cfg["account"] = account

            # 최상위에도 현재 모드 키 복사 (하위 호환)
            kis_cfg["hts_id"] = hts_id
            kis_cfg["app_key"] = app_key
            kis_cfg["app_secret"] = app_secret
            kis_cfg["account"] = account

            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

            self._connect(data)
            return self.connected
        except Exception as e:
            logger.error("Failed to save KIS credentials: %s", e)
            return False

    def switch_mode(self, mode: str) -> bool:
        """모의/실전 모드 전환."""
        path = Path(self._config_path)
        if not path.exists():
            return False
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            kis_cfg = data.get("kis", {})
            mode_cfg = kis_cfg.get(mode, {})
            if not mode_cfg.get("app_key"):
                return False
            kis_cfg["mode"] = mode
            for k in ("hts_id", "app_key", "app_secret", "account"):
                kis_cfg[k] = mode_cfg.get(k, "")
            with open(path, "w") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
            self.connected = False
            self.kis = None
            self._connect(data)
            return self.connected
        except Exception as e:
            logger.error("KIS mode switch failed: %s", e)
            return False

    def get_balance(self) -> dict | None:
        """Get real-time account balance."""
        if not self.connected or not self.kis:
            return None
        try:
            # pykis 0.7: get_kr_stock_balance() → DataFrame, get_kr_deposit() → int
            deposit = self.kis.get_kr_deposit()
            balance_df = self.kis.get_kr_stock_balance()
            holdings = []
            if balance_df is not None and not balance_df.empty:
                for _, row in balance_df.iterrows():
                    holdings.append({
                        "ticker": str(row.get("종목코드", "")),
                        "name": str(row.get("종목명", "")),
                        "quantity": int(row.get("보유수량", 0)),
                        "avg_price": int(row.get("매입평균가", 0)),
                        "current_price": int(row.get("현재가", 0)),
                        "profit_pct": round(float(row.get("수익률", 0)), 2),
                        "eval_amount": int(row.get("평가금액", 0)),
                    })
            total_eval = sum(h["eval_amount"] for h in holdings) + deposit
            total_profit = sum(
                h["eval_amount"] - h["avg_price"] * h["quantity"]
                for h in holdings
            )
            return {
                "holdings": holdings,
                "total_eval": total_eval,
                "total_profit": total_profit,
                "cash": deposit,
            }
        except Exception as e:
            logger.error("KIS balance query failed: %s", e)
            return None

    def get_realtime_price(self, ticker: str) -> float:
        """Get real-time current price."""
        if not self.connected or not self.kis:
            return 0.0
        try:
            return float(self.kis.get_kr_current_price(ticker))
        except Exception as e:
            logger.error("KIS price query failed for %s: %s", ticker, e)
            return 0.0

    def buy(self, ticker: str, quantity: int, price: int | None = None) -> OrderResult:
        """Submit buy order."""
        if not self.connected or not self.kis:
            return OrderResult(success=False, message="KIS 미연결")

        order_type = "limit" if price else "market"
        try:
            # pykis 0.7: buy_kr_stock(ticker, order_amount, price)
            # price=0 → 시장가
            order = self.kis.buy_kr_stock(ticker, quantity, price or 0)

            self.safety.record_order()
            return OrderResult(
                success=True,
                order_id=str(order.get("ODNO", "") if isinstance(order, dict) else ""),
                message="주문 접수 완료",
                ticker=ticker,
                quantity=quantity,
                price=price or 0,
                order_type=order_type,
            )
        except Exception as e:
            logger.error("KIS buy order failed: %s", e)
            return OrderResult(success=False, message=str(e)[:100])

    def sell(self, ticker: str, quantity: int, price: int | None = None) -> OrderResult:
        """Submit sell order."""
        if not self.connected or not self.kis:
            return OrderResult(success=False, message="KIS 미연결")

        order_type = "limit" if price else "market"
        try:
            # pykis 0.7: sell_kr_stock(ticker, order_amount, price)
            order = self.kis.sell_kr_stock(ticker, quantity, price or 0)

            self.safety.record_order()
            return OrderResult(
                success=True,
                order_id=str(order.get("ODNO", "") if isinstance(order, dict) else ""),
                message="매도 주문 접수",
                ticker=ticker,
                quantity=quantity,
                price=price or 0,
                order_type=order_type,
            )
        except Exception as e:
            logger.error("KIS sell order failed: %s", e)
            return OrderResult(success=False, message=str(e)[:100])

    def compute_buy_quantity(self, price: float, total_eval: float, pct: float = 10.0) -> int:
        """Compute quantity for a given % of total portfolio."""
        if price <= 0 or total_eval <= 0:
            return 0
        amount = total_eval * pct / 100
        return int(amount // price)


def format_kis_setup_guide() -> str:
    """Format KIS setup guide for Telegram."""
    return (
        "\u2699\ufe0f KIS OpenAPI \uc124\uc815 \uac00\uc774\ub4dc\n"
        "\u2500" * 25 + "\n\n"
        "1\ub2e8\uacc4: \ud55c\uad6d\ud22c\uc790\uc99d\uad8c \uacc4\uc88c \uac1c\uc124\n"
        "  (\uc774\ubbf8 \uc788\uc73c\uba74 \uac74\ub108\ub6f0\uae30)\n\n"
        "2\ub2e8\uacc4: KIS Developers \uc2e0\uccad\n"
        "  apiportal.koreainvestment.com\n"
        "  \ud2b8\ub808\uc774\ub529 > Open API > KIS Developers\n"
        "  \uc11c\ube44\uc2a4 \uc2e0\uccad + \ubaa8\uc758\ud22c\uc790 \uc2e0\uccad\n\n"
        "3\ub2e8\uacc4: APP Key \ubc1c\uae09\n"
        "  KIS Developers\uc5d0\uc11c \uc571 \ud0a4/\uc2dc\ud06c\ub9bf \ubcf5\uc0ac\n\n"
        "4\ub2e8\uacc4: \uc5ec\uae30\uc5d0 \uc785\ub825\n"
        "  \uc544\ub798 \ud615\uc2dd\uc73c\ub85c \ubcf4\ub0b4\uc8fc\uc138\uc694:\n\n"
        "  KIS_ID: \ud64d\uae38\ub3d9\n"
        "  KIS_KEY: Pa0knAM6JLAjIa93...\n"
        "  KIS_SECRET: V9J3YGPE5q2ZRG5E...\n"
        "  KIS_ACCOUNT: 12345678-01\n\n"
        "5\ub2e8\uacc4: \uc790\ub3d9 \uc5f0\uacb0 + \ubaa8\uc758\ud22c\uc790 \ud14c\uc2a4\ud2b8"
    )


def format_kis_status(broker: KisBroker) -> str:
    """Format KIS connection status for Telegram."""
    if not broker.connected:
        return (
            "\u2699\ufe0f \uc790\ub3d9\ub9e4\ub9e4 \uc124\uc815\n\n"
            "KIS \uc5f0\uacb0: \u274c \ubbf8\uc5f0\uacb0\n"
            "/setup_kis \ub85c \uc124\uc815\ud558\uc138\uc694"
        )
    mode_text = "\ubaa8\uc758\ud22c\uc790" if broker.mode == "virtual" else "\uc2e4\uc804"
    s = broker.safety
    return (
        f"\u2699\ufe0f \uc790\ub3d9\ub9e4\ub9e4 \uc124\uc815\n\n"
        f"KIS \uc5f0\uacb0: \u2705 {mode_text} \ubaa8\ub4dc\n"
        f"1\ud68c \ucd5c\ub300 \uc8fc\ubb38: \uc790\uc0b0\uc758 {s.max_order_pct:.0f}%\n"
        f"\uc77c\uc77c \ucd5c\ub300 \uc8fc\ubb38: {s.max_daily_orders}\ud68c\n"
        f"\uc77c\uc77c \uc190\uc2e4 \ud55c\ub3c4: {s.daily_loss_limit_pct:.0f}%\n"
        f"\uc624\ub298 \uc8fc\ubb38: {s.daily_order_count}\ud68c"
    )
