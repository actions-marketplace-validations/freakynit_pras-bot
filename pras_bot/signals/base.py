"""Base class for all spam-signal detectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


def linear(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    """Linear interpolation between (x0, y0) and (x1, y1), clamped to [0, 100]."""
    if x1 == x0:
        return y0
    ratio = (x - x0) / (x1 - x0)
    val = y0 + ratio * (y1 - y0)
    return max(0.0, min(100.0, val))


def clamp_score(value: Any) -> float:
    """Coerce an LLM-returned score into a float in [0, 100]; 50 if invalid."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 50.0
    if f != f:  # NaN guard
        return 50.0
    return max(0.0, min(100.0, f))


# System prompt that constrains LLM signals to JSON-only output.
LLM_JSON_SYSTEM = (
    "You are a strict JSON API. Respond with a single valid JSON object "
    "and absolutely nothing else — no markdown, no prose, no code fences."
)


class ScoredSignal(ABC):
    """Every signal must return a score in [0, 100], or ``None`` to skip itself.

    0   = looks perfectly legitimate
    100 = extremely suspicious / almost certainly spam
    None = signal disabled / not applicable (excluded from the weighted average)
    """

    def __init__(self, gh: Any, config: dict[str, Any], pr_data: dict[str, Any]) -> None:
        self.gh = gh
        self.config = config
        self.pr_data = pr_data

    @abstractmethod
    def score(self) -> float | None:
        ...

    @classmethod
    def name(cls) -> str:
        """Signal name (matches config keys). Derived from class name by convention."""
        import re
        # LinesChangedSignal → lines_changed
        base = cls.__name__.replace("Signal", "")
        return re.sub(r"(?<!^)(?=[A-Z])", "_", base).lower()

    # ------------------------------------------------------------------
    # NLP-signal helpers
    # ------------------------------------------------------------------

    def _resolve_provider(self) -> str:
        """Effective provider for an NLP signal: ``'off'`` | ``'non_llm'`` | ``'llm'``.

        Returns ``'off'`` (skip) when the signal asks for ``'llm'`` but LLM is
        not enabled in config — so disabling LLM never biases the score toward
        a neutral 50; the signal simply drops out of the average.
        """
        sig_cfg = self.config.get("signals", {}).get(self.name(), {})
        provider = sig_cfg.get("provider", "off")
        # YAML 1.1 (PyYAML) coerces bare `off`/`no`/`yes`/`on` to booleans;
        # tolerate that so users can write `provider: off` unquoted.
        if provider is False or provider is None:
            provider = "off"
        provider = str(provider).lower()
        if provider in ("off", "disabled", "false"):
            return "off"
        if provider not in ("non_llm", "llm"):
            print(f"⚠️  {self.name()}: unknown provider {provider!r}; skipping")
            return "off"
        if provider == "llm" and not self.config.get("llm", {}).get("enabled"):
            print(f"⚠️  {self.name()}: provider='llm' but llm.enabled is false — skipping")
            return "off"
        return provider
