"""
analysis/liquidity.py
=======================

Identificação simplificada de zonas de liquidez.

Em Smart Money Concepts, "liquidez" refere-se a regiões de preço onde
há concentração de ordens (stops de compradores/vendedores),
tipicamente logo acima de swing highs recentes (liquidez de compra) e
logo abaixo de swing lows recentes (liquidez de venda).

Esta implementação inicial marca essas zonas com base nos swing points
já detectados — servindo de base para uma futura integração com dados
de order book (profundidade) e Open Interest.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True, slots=True)
class LiquidityZones:
    """Zonas de liquidez estimadas a partir dos swing points recentes."""

    buy_side: list[float]
    sell_side: list[float]


def find_liquidity_zones(swings: pd.DataFrame, max_zones: int = 3) -> LiquidityZones:
    """
    Estima zonas de liquidez de compra (acima dos swing highs) e de
    venda (abaixo dos swing lows), usando os `max_zones` swings mais
    recentes de cada tipo.

    Args:
        swings: DataFrame de swing points (colunas "price", "type"),
            ordenado cronologicamente (saída de `structure.swings.get_swing_points`).
        max_zones: número máximo de zonas retornadas por lado.

    Returns:
        `LiquidityZones` com os níveis estimados de liquidez de compra
        (buy_side) e de venda (sell_side).
    """
    if swings.empty:
        return LiquidityZones(buy_side=[], sell_side=[])

    recent_highs = swings.loc[swings["type"] == "high", "price"].tail(max_zones)
    recent_lows = swings.loc[swings["type"] == "low", "price"].tail(max_zones)

    # Liquidez de compra: logo acima dos swing highs (onde ficam os
    # stops de quem vendeu/shorteou o rompimento).
    buy_side = [round(price, 6) for price in recent_highs.tolist()]

    # Liquidez de venda: logo abaixo dos swing lows (onde ficam os
    # stops de quem comprou/segurou o suporte).
    sell_side = [round(price, 6) for price in recent_lows.tolist()]

    return LiquidityZones(buy_side=buy_side, sell_side=sell_side)
