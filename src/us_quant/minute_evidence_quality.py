"""Compatibility import for the shared pure minute-session evaluator."""

from us_quant.trading.domain import market_evidence_quality as _quality
from us_quant.trading.domain.market_evidence_quality import *  # noqa: F401,F403

__all__ = _quality.__all__
