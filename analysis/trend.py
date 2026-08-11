"""
analysis/trend.py
===================

Determina a tendência final do ativo, combinando dois sinais
independentes:

1. Alinhamento das EMAs (20/50/100/200) — sinal de tendência "de
   médio/longo prazo".
2. Estrutura de mercado (HH/HL vs LH/LL) — sinal de tendência "de
   price action".

Quando os dois sinais concordam, a confiança na tendência é maior;
quando divergem, o resultado é classificado como "Ranging" (mercado
em transição/indefinido).
"""

from __future__ import annotations


def _ema_trend(ema20: float, ema50: float, ema100: float, ema200: float) -> str:
    """
    Deriva uma tendência simples a partir do alinhamento das EMAs.

    - EMAs em ordem crescente (20 > 50 > 100 > 200) -> Bullish.
    - EMAs em ordem decrescente (20 < 50 < 100 < 200) -> Bearish.
    - Qualquer outra ordem -> Ranging.
    """
    if ema20 > ema50 > ema100 > ema200:
        return "Bullish"
    if ema20 < ema50 < ema100 < ema200:
        return "Bearish"
    return "Ranging"


def determine_trend(
    ema20: float,
    ema50: float,
    ema100: float,
    ema200: float,
    structure_trend: str,
) -> str:
    """
    Combina a tendência derivada das EMAs com a tendência estrutural
    (price action) para chegar à tendência final do ativo.

    Args:
        ema20, ema50, ema100, ema200: valores atuais das EMAs.
        structure_trend: tendência vinda de
            `structure.market_structure.analyze_market_structure`
            ("Bullish", "Bearish" ou "Ranging").

    Returns:
        "Bullish", "Bearish" ou "Ranging".
    """
    ema_based_trend = _ema_trend(ema20, ema50, ema100, ema200)

    ema_is_directional = ema_based_trend in ("Bullish", "Bearish")
    structure_is_directional = structure_trend in ("Bullish", "Bearish")

    # Os dois sinais concordam: alta confiança.
    if ema_is_directional and structure_is_directional:
        if ema_based_trend == structure_trend:
            return ema_based_trend
        # Divergência total (um Bullish, outro Bearish): mercado em
        # transição, ainda sem confirmação suficiente.
        return "Ranging"

    # Apenas um dos sinais é direcional: usamos a estrutura, por ser
    # mais sensível a mudanças recentes de price action do que as EMAs
    # de longo prazo.
    if structure_is_directional:
        return structure_trend
    if ema_is_directional:
        return ema_based_trend

    # Nenhum dos dois sinais é direcional: mercado indefinido.
    return "Ranging"
