"""Multi-AI Router — Claude + GPT + Gemini 분업 시스템.

K-Quant v3.6: 각 AI의 강점에 맞는 태스크 분배.
- Claude (Anthropic): 심층 분석, OCR/Vision, 전략 합성
- GPT (OpenAI): 기술적 분석, 구조화된 데이터 출력
- Gemini (Google): 뉴스 감성분석, 빠른 요약, 한국어 속도

Usage:
    router = AIRouter()
    result = await router.analyze("sentiment", prompt, context)
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ── AI Provider Configs ──────────────────────────────────────────────────────

@dataclass
class AIUsageStats:
    """API 호출 통계 추적."""
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    errors: int = 0
    total_latency_ms: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.calls if self.calls > 0 else 0


@dataclass
class ProviderConfig:
    name: str
    api_key: str
    models: dict[str, str] = field(default_factory=dict)
    available: bool = False


# ── Task-to-AI 라우팅 테이블 ─────────────────────────────────────────────────

TASK_ROUTING: dict[str, dict[str, Any]] = {
    # ─ Gemini 담당: 속도 중시, 비용 절약 ─
    "sentiment": {
        "provider": "gemini",
        "model_tier": "fast",
        "fallback": "claude",
        "description": "뉴스/감성 분석 (빠른 한국어 처리)",
    },
    "news_summary": {
        "provider": "gemini",
        "model_tier": "fast",
        "fallback": "claude",
        "description": "뉴스 요약 (속도 우선)",
    },
    "morning_briefing": {
        "provider": "gemini",
        "model_tier": "fast",
        "fallback": "claude",
        "description": "모닝 브리핑 (빠른 생성)",
    },
    "live_market": {
        "provider": "gemini",
        "model_tier": "fast",
        "fallback": "claude",
        "description": "실시간 시황 요약",
    },
    # ─ GPT 담당: 구조화된 출력, 기술 분석 ─
    "technical_analysis": {
        "provider": "gpt",
        "model_tier": "fast",
        "fallback": "claude",
        "description": "기술적 분석 (구조화된 JSON 출력)",
    },
    "fundamental_analysis": {
        "provider": "gpt",
        "model_tier": "fast",
        "fallback": "claude",
        "description": "펀더멘탈 분석 (데이터 해석)",
    },
    "diagnosis_batch": {
        "provider": "gpt",
        "model_tier": "fast",
        "fallback": "claude",
        "description": "보유종목 일괄 진단",
    },
    "sector_analysis": {
        "provider": "gpt",
        "model_tier": "standard",
        "fallback": "claude",
        "description": "섹터 분석 리포트",
    },
    # ─ Claude 담당: 심층 분석, Vision, 전략 ─
    "deep_analysis": {
        "provider": "claude",
        "model_tier": "standard",
        "fallback": "gpt",
        "description": "심층 종합 분석",
    },
    "strategy_synthesis": {
        "provider": "claude",
        "model_tier": "standard",
        "fallback": "gpt",
        "description": "전략 합성 (멀티에이전트 최종)",
    },
    "vision_ocr": {
        "provider": "claude",
        "model_tier": "standard",
        "fallback": None,
        "description": "스크린샷/이미지 분석 (Vision)",
    },
    "chat": {
        "provider": "claude",
        "model_tier": "standard",
        "fallback": "gpt",
        "description": "대화형 AI 질의",
    },
    "pdf_report": {
        "provider": "claude",
        "model_tier": "standard",
        "fallback": "gpt",
        "description": "프리미엄 PDF 리포트 생성",
    },
    "eod_report": {
        "provider": "claude",
        "model_tier": "standard",
        "fallback": "gpt",
        "description": "장 마감 분석 리포트",
    },
    # ─ v3.10 추가 ─
    "us_premarket": {
        "provider": "gpt",
        "model_tier": "standard",
        "fallback": "claude",
        "description": "미국 프리마켓 브리핑",
    },
}


class AIRouter:
    """Multi-AI 라우터: 태스크별 최적 AI 자동 선택."""

    def __init__(self) -> None:
        self.providers: dict[str, ProviderConfig] = {}
        self.stats: dict[str, AIUsageStats] = {
            "claude": AIUsageStats(),
            "gpt": AIUsageStats(),
            "gemini": AIUsageStats(),
        }
        # [v3.6.4] Prompt Caching 통계
        self._cache_hits: int = 0
        self._cache_tokens_saved: int = 0
        self._init_providers()

    def _init_providers(self) -> None:
        """환경변수에서 API 키 로드 + 프로바이더 초기화."""
        # Claude (Anthropic)
        claude_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.providers["claude"] = ProviderConfig(
            name="Claude (Anthropic)",
            api_key=claude_key,
            models={
                "fast": "claude-haiku-4-5-20251001",
                "standard": "claude-sonnet-4-5-20250929",
                "vision": "claude-sonnet-4-20250514",
            },
            available=bool(claude_key),
        )

        # GPT (OpenAI)
        gpt_key = os.getenv("OPENAI_API_KEY", "")
        self.providers["gpt"] = ProviderConfig(
            name="GPT (OpenAI)",
            api_key=gpt_key,
            models={
                "fast": "gpt-4o-mini",
                "standard": "gpt-4o",
            },
            available=bool(gpt_key),
        )

        # Gemini (Google)
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        self.providers["gemini"] = ProviderConfig(
            name="Gemini (Google)",
            api_key=gemini_key,
            models={
                "fast": "gemini-2.0-flash",
                "standard": "gemini-2.0-pro",
            },
            available=bool(gemini_key),
        )

        available = [n for n, p in self.providers.items() if p.available]
        logger.info("AI Router initialized: %s available", ", ".join(available) or "NONE")

    # ── Core API ─────────────────────────────────────────────────────────────

    async def analyze(
        self,
        task: str,
        prompt: str,
        *,
        system: str = "",
        max_tokens: int = 1000,
        temperature: float = 0.3,
        response_format: str = "text",  # "text" or "json"
    ) -> str:
        """태스크에 맞는 AI로 분석 실행.

        Args:
            task: TASK_ROUTING 키 (sentiment, technical_analysis, etc.)
            prompt: 사용자/시스템 프롬프트
            system: 시스템 프롬프트
            max_tokens: 최대 토큰
            temperature: 창의성 (0~1)
            response_format: 응답 형식

        Returns:
            AI 응답 텍스트
        """
        routing = TASK_ROUTING.get(task)
        if not routing:
            logger.warning("Unknown task '%s', falling back to claude", task)
            routing = {"provider": "claude", "model_tier": "standard", "fallback": "gpt"}

        provider_name = routing["provider"]
        model_tier = routing["model_tier"]
        fallback = routing.get("fallback")

        # 1차: 지정된 프로바이더
        provider = self.providers.get(provider_name)
        if provider and provider.available:
            try:
                result = await self._call_provider(
                    provider_name, model_tier, prompt,
                    system=system, max_tokens=max_tokens,
                    temperature=temperature, response_format=response_format,
                )
                if result:
                    return result
            except Exception as e:
                logger.warning("AI %s failed for task '%s': %s", provider_name, task, e)
                self.stats[provider_name].errors += 1

        # 2차: Fallback
        if fallback:
            fb_provider = self.providers.get(fallback)
            if fb_provider and fb_provider.available:
                try:
                    logger.info("Falling back to %s for task '%s'", fallback, task)
                    result = await self._call_provider(
                        fallback, model_tier, prompt,
                        system=system, max_tokens=max_tokens,
                        temperature=temperature, response_format=response_format,
                    )
                    if result:
                        return result
                except Exception as e:
                    logger.warning("Fallback %s also failed: %s", fallback, e)
                    self.stats[fallback].errors += 1

        # 3차: 아무 사용 가능한 프로바이더
        for name, prov in self.providers.items():
            if prov.available and name not in (provider_name, fallback):
                try:
                    return await self._call_provider(
                        name, model_tier, prompt,
                        system=system, max_tokens=max_tokens,
                        temperature=temperature, response_format=response_format,
                    )
                except Exception as e:
                    logger.warning("AI %s last-resort fallback failed for task: %s", name, e)
                    continue

        return "[AI 응답 불가] 모든 AI 프로바이더가 사용 불가합니다."

    # ── Provider-specific Calls ──────────────────────────────────────────────

    async def _call_provider(
        self,
        provider_name: str,
        model_tier: str,
        prompt: str,
        *,
        system: str = "",
        max_tokens: int = 1000,
        temperature: float = 0.3,
        response_format: str = "text",
    ) -> str:
        """프로바이더별 API 호출."""
        provider = self.providers[provider_name]
        model = provider.models.get(model_tier, list(provider.models.values())[0])

        start = time.monotonic()
        try:
            if provider_name == "claude":
                result = await self._call_claude(
                    provider.api_key, model, prompt,
                    system=system, max_tokens=max_tokens, temperature=temperature,
                )
            elif provider_name == "gpt":
                result = await self._call_gpt(
                    provider.api_key, model, prompt,
                    system=system, max_tokens=max_tokens, temperature=temperature,
                    response_format=response_format,
                )
            elif provider_name == "gemini":
                result = await self._call_gemini(
                    provider.api_key, model, prompt,
                    system=system, max_tokens=max_tokens, temperature=temperature,
                )
            else:
                return ""

            elapsed = (time.monotonic() - start) * 1000
            stats = self.stats[provider_name]
            stats.calls += 1
            stats.total_latency_ms += elapsed
            logger.debug(
                "AI %s/%s responded in %.0fms (%d chars)",
                provider_name, model, elapsed, len(result),
            )
            return result
        except Exception:
            elapsed = (time.monotonic() - start) * 1000
            self.stats[provider_name].total_latency_ms += elapsed
            raise

    async def _call_claude(
        self, api_key: str, model: str, prompt: str, *,
        system: str = "", max_tokens: int = 1000, temperature: float = 0.3,
    ) -> str:
        """Anthropic Claude API 호출 (Prompt Caching 적용)."""
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        # [v3.6.4] Prompt Caching: 시스템 프롬프트를 캐시하여 비용 90% 절감
        if system:
            payload["system"] = [{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }]

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Claude API error {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            usage = data.get("usage", {})
            self.stats["claude"].tokens_in += usage.get("input_tokens", 0)
            self.stats["claude"].tokens_out += usage.get("output_tokens", 0)
            # 캐시 히트 통계 추적
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_write = usage.get("cache_creation_input_tokens", 0)
            if cache_read > 0:
                self._cache_hits += 1
                self._cache_tokens_saved += cache_read
            return data["content"][0]["text"]

    async def _call_gpt(
        self, api_key: str, model: str, prompt: str, *,
        system: str = "", max_tokens: int = 1000, temperature: float = 0.3,
        response_format: str = "text",
    ) -> str:
        """OpenAI GPT API 호출."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if response_format == "json":
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"GPT API error {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            usage = data.get("usage", {})
            self.stats["gpt"].tokens_in += usage.get("prompt_tokens", 0)
            self.stats["gpt"].tokens_out += usage.get("completion_tokens", 0)
            return data["choices"][0]["message"]["content"]

    async def _call_gemini(
        self, api_key: str, model: str, prompt: str, *,
        system: str = "", max_tokens: int = 1000, temperature: float = 0.3,
    ) -> str:
        """Google Gemini API 호출."""
        contents = []
        if system:
            contents.append({"role": "user", "parts": [{"text": f"[System Instructions]\n{system}"}]})
            contents.append({"role": "model", "parts": [{"text": "Understood. I will follow these instructions."}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        payload = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
            f":generateContent?key={api_key}"
        )

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Gemini API error {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            # Gemini usage tracking
            usage = data.get("usageMetadata", {})
            self.stats["gemini"].tokens_in += usage.get("promptTokenCount", 0)
            self.stats["gemini"].tokens_out += usage.get("candidatesTokenCount", 0)
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return parts[0].get("text", "")
            return ""

    # ── Vision (Claude Only) ────────────────────────────────────────────────

    async def vision_analyze(
        self, image_bytes: bytes, prompt: str, *, max_tokens: int = 4096,
    ) -> str:
        """이미지 분석 (Claude Vision 전용)."""
        import base64

        provider = self.providers.get("claude")
        if not provider or not provider.available:
            return "[Vision 불가] Claude API 키가 설정되지 않았습니다."

        image_b64 = base64.standard_b64encode(image_bytes).decode("ascii")

        # Detect media type
        if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            media_type = "image/png"
        elif image_bytes[:2] == b"\xff\xd8":
            media_type = "image/jpeg"
        else:
            media_type = "image/jpeg"

        payload = {
            "model": provider.models.get("vision", provider.models["standard"]),
            "max_tokens": max_tokens,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": media_type, "data": image_b64,
                    }},
                    {"type": "text", "text": prompt},
                ],
            }],
        }

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": provider.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            elapsed = (time.monotonic() - start) * 1000
            self.stats["claude"].calls += 1
            self.stats["claude"].total_latency_ms += elapsed

            if resp.status_code != 200:
                raise RuntimeError(f"Vision API error: {resp.status_code}")
            data = resp.json()
            self.stats["claude"].tokens_in += data.get("usage", {}).get("input_tokens", 0)
            self.stats["claude"].tokens_out += data.get("usage", {}).get("output_tokens", 0)
            return data["content"][0]["text"]

    # ── Status & Monitoring ──────────────────────────────────────────────────

    def get_status(self) -> str:
        """AI 프로바이더 상태 요약."""
        lines = ["AI 엔진 상태", "─" * 25]
        for name, provider in self.providers.items():
            status = "✅" if provider.available else "❌"
            stats = self.stats[name]
            model_count = len(provider.models)
            line = f"{status} {provider.name}: {model_count}모델"
            if stats.calls > 0:
                line += f" | {stats.calls}회 호출 | 평균 {stats.avg_latency_ms:.0f}ms"
                if stats.errors > 0:
                    line += f" | ⚠️ {stats.errors}오류"
            lines.append(line)

        # 비용 추정 (대략)
        claude_cost = (
            self.stats["claude"].tokens_in * 0.25 / 1_000_000
            + self.stats["claude"].tokens_out * 1.25 / 1_000_000
        )
        gpt_cost = (
            self.stats["gpt"].tokens_in * 0.15 / 1_000_000
            + self.stats["gpt"].tokens_out * 0.60 / 1_000_000
        )
        gemini_cost = (
            self.stats["gemini"].tokens_in * 0.075 / 1_000_000
            + self.stats["gemini"].tokens_out * 0.30 / 1_000_000
        )
        total = claude_cost + gpt_cost + gemini_cost
        lines.append("")
        lines.append(f"💰 세션 비용: ~${total:.4f}")
        lines.append(f"   Claude: ${claude_cost:.4f} | GPT: ${gpt_cost:.4f} | Gemini: ${gemini_cost:.4f}")

        # 캐시 절감 통계
        if self._cache_hits > 0:
            saved_cost = self._cache_tokens_saved * 2.70 / 1_000_000  # $3 - $0.30 = $2.70 saved per MTok
            lines.append(f"\n🗄 캐시 히트: {self._cache_hits}회 | 절감 토큰: {self._cache_tokens_saved:,}")
            lines.append(f"   예상 절감: ~${saved_cost:.4f}")

        return "\n".join(lines)

    def get_routing_table(self) -> str:
        """현재 라우팅 테이블 표시."""
        lines = ["AI 업무 분담표", "═" * 30]
        by_provider: dict[str, list[str]] = {"gemini": [], "gpt": [], "claude": []}
        for task, config in TASK_ROUTING.items():
            provider = config["provider"]
            if provider in by_provider:
                by_provider[provider].append(f"  • {config['description']}")

        icons = {"gemini": "🟢 Gemini", "gpt": "🔵 GPT", "claude": "🟣 Claude"}
        for prov, tasks in by_provider.items():
            available = self.providers.get(prov, ProviderConfig("", "")).available
            status = "✅" if available else "❌"
            lines.append(f"\n{icons[prov]} {status}")
            lines.extend(tasks)

        return "\n".join(lines)
