"""
analysis/support_resistance.py
================================

Identifica níveis de suporte e resistência a partir dos swing points,
agrupando (clusterizando) níveis de preço próximos entre si em uma
única zona — evitando retornar dezenas de níveis quase idênticos.
"""

from __future__ import annotations

import pandas as pd

from config import ANALYSIS_CONFIG


def _cluster_levels(levels: list[float], tolerance_pct: float) -> list[float]:
    """
    Agrupa níveis de preço próximos (dentro de `tolerance_pct`) em uma
    única zona, representada pela média dos níveis do cluster.
    """
    if not levels:
        return []

    sorted_levels = sorted(levels)
    clusters: list[list[float]] = [[sorted_levels[0]]]

    for level in sorted_levels[1:]:
        last_cluster_avg = sum(clusters[-1]) / len(clusters[-1])
        distance_pct = abs(level - last_cluster_avg) / last_cluster_avg * 100

        if distance_pct <= tolerance_pct:
            clusters[-1].append(level)
        else:
            clusters.append([level])

    return [round(sum(cluster) / len(cluster), 6) for cluster in clusters]


def find_support_resistance(
    df: pd.DataFrame,
    swings: pd.DataFrame,
    current_price: float,
    max_levels: int | None = None,
    tolerance_pct: float | None = None,
) -> tuple[list[float], list[float]]:
    """
    Deriva níveis de suporte e resistência a partir dos swing points.

    Args:
        df: DataFrame OHLCV (usado apenas como referência, hoje não é
            estritamente necessário mas mantido para futura expansão,
            ex.: considerar volume por nível).
        swings: DataFrame de swing points (colunas "price", "type").
        current_price: preço atual do ativo, usado para separar níveis
            abaixo (suporte) e acima (resistência) do preço.
        max_levels: quantidade máxima de níveis retornados por lado
            (padrão: `config.ANALYSIS_CONFIG.max_levels_returned`).
        tolerance_pct: tolerância percentual para clusterizar níveis
            próximos (padrão: `config.ANALYSIS_CONFIG.price_cluster_tolerance_pct`).

    Returns:
        Tupla (support, resistance), cada uma como lista de floats,
        ordenadas por proximidade ao preço atual.
    """
    max_levels = max_levels or ANALYSIS_CONFIG.max_levels_returned
    tolerance_pct = tolerance_pct or ANALYSIS_CONFIG.price_cluster_tolerance_pct

    if swings.empty:
        return [], []

    below_price = swings.loc[swings["price"] < current_price, "price"].tolist()
    above_price = swings.loc[swings["price"] > current_price, "price"].tolist()

    support_levels = _cluster_levels(below_price, tolerance_pct)
    resistance_levels = _cluster_levels(above_price, tolerance_pct)

    # Suporte: níveis mais próximos do preço atual primeiro (do maior
    # para o menor, já que estão abaixo do preço).
    support_levels = sorted(support_levels, reverse=True)[:max_levels]

    # Resistência: níveis mais próximos do preço atual primeiro (do
    # menor para o maior, já que estão acima do preço).
    resistance_levels = sorted(resistance_levels)[:max_levels]

    return support_levels, resistance_levels
