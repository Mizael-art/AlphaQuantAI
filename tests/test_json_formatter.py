from __future__ import annotations

import json

from models.analysis_result import AnalysisResult, StructureResult
from output.json_formatter import to_json_string


def test_to_json_string_serializes_analysis_result() -> None:
    result = AnalysisResult(
        symbol="BTCUSDT",
        timeframe="1H",
        price=100_000.0,
        trend="bullish",
        ema20=1.0,
        ema50=2.0,
        ema100=3.0,
        ema200=4.0,
        rsi=50.0,
        atr=100.0,
        macd=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        volume_avg=10.0,
        structure=StructureResult(False, True, False, False, False, False, None, None),
    )

    assert json.loads(to_json_string(result))["symbol"] == "BTCUSDT"
