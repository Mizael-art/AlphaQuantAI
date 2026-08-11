"""
analysis
========

Pacote responsável pela camada de análise de mais alto nível: combina
indicadores e estrutura de mercado para produzir tendência final,
suporte/resistência, zonas de liquidez e o score de confluência.
"""

from analysis.liquidity import LiquidityZones, find_liquidity_zones
from analysis.score import calculate_score
from analysis.support_resistance import find_support_resistance
from analysis.trend import determine_trend

__all__ = [
    "determine_trend",
    "find_support_resistance",
    "LiquidityZones",
    "find_liquidity_zones",
    "calculate_score",
]
