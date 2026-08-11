"""
tests/test_scanner.py
======================

Testes das funções puras do `scanner.screener` (classificação e
cálculo de distância até a zona mais próxima). Não fazem chamadas de
rede — `_scan_one`/`scan_market` (que chamam a Binance via
`app.run_analysis`) não são cobertos aqui, seguindo o mesmo padrão dos
demais testes do projeto, que testam a lógica pura e não a integração
com a Binance.
"""

from __future__ import annotations

from scanner.screener import _classify, _nearest_zone


def test_nearest_zone_picks_closest_support_or_resistance() -> None:
    price = 100.0
    support = [90.0, 98.5]
    resistance = [103.0, 120.0]

    nearest_price, nearest_type, distance_pct = _nearest_zone(price, support, resistance)

    assert nearest_price == 98.5
    assert nearest_type == "support"
    assert distance_pct == 1.5


def test_nearest_zone_returns_none_when_no_levels() -> None:
    nearest_price, nearest_type, distance_pct = _nearest_zone(100.0, [], [])

    assert nearest_price is None
    assert nearest_type is None
    assert distance_pct is None


def test_classify_entry_zone_requires_proximity_score_and_no_conflict() -> None:
    status = _classify(score_htf=75, score_ltf=80, distance_pct=0.3, trend_conflict=False)
    assert status == "zona_de_entrada"


def test_classify_downgrades_to_watch_on_trend_conflict() -> None:
    # Mesmo perto da zona e com score alto, conflito de tendência entre
    # HTF e LTF não deve virar "zona_de_entrada" (é fator de risco).
    status = _classify(score_htf=75, score_ltf=80, distance_pct=0.3, trend_conflict=True)
    assert status == "observar"


def test_classify_watch_by_proximity_even_with_moderate_score() -> None:
    status = _classify(score_htf=55, score_ltf=58, distance_pct=1.2, trend_conflict=False)
    assert status == "observar"


def test_classify_watch_by_score_even_when_far_from_any_zone() -> None:
    status = _classify(score_htf=65, score_ltf=62, distance_pct=8.0, trend_conflict=False)
    assert status == "observar"


def test_classify_out_of_zone_when_far_and_low_score() -> None:
    status = _classify(score_htf=48, score_ltf=52, distance_pct=6.0, trend_conflict=False)
    assert status == "fora_de_zona"
