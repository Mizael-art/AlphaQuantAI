"""
scanner/screener.py
====================

Implementação da varredura multi-símbolo.

Para cada símbolo, roda `app.run_analysis` em DOIS timeframes:

- `htf` (higher timeframe, padrão "4H"): dá o contexto/tendência,
  igual ao topo da hierarquia multi-timeframe (1D -> 4H -> 1H -> 15M)
  já usada no `/snapshot`.
- `ltf` (lower timeframe, padrão "1H"): mede a distância do preço
  atual até a zona de suporte/resistência mais próxima, e serve de
  timeframe de "gatilho" (execução).

Isso reaproveita 100% do pipeline já existente (indicadores,
estrutura, score) — não duplica lógica de análise, só orquestra em
lote e adiciona a métrica de "distância até a zona".

Concorrência: como cada símbolo faz ~4 requisições HTTP à Binance
(klines HTF, preço HTF, klines LTF, preço LTF), rodamos em um
ThreadPoolExecutor para não pagar o tempo de forma serial — sem isso,
escanear 25 símbolos poderia levar mais de um minuto.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from app import InsufficientDataError, run_analysis
from config import (
    SCAN_CONCURRENCY,
    SCAN_ENTRY_ZONE_PCT,
    SCAN_MIN_SCORE_ENTRY,
    SCAN_MIN_SCORE_WATCH,
    SCAN_WATCH_ZONE_PCT,
)


@dataclass(frozen=True, slots=True)
class ScanEntry:
    """Resultado condensado de um símbolo dentro do scan."""

    symbol: str
    status: str  # "zona_de_entrada" | "observar" | "fora_de_zona"
    price: float
    trend_htf: str
    trend_ltf: str
    trend_conflict: bool
    score_htf: int
    score_ltf: int
    nearest_zone_price: float | None
    nearest_zone_type: str | None  # "support" | "resistance"
    distance_to_zone_pct: float | None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "status": self.status,
            "price": self.price,
            "trend_htf": self.trend_htf,
            "trend_ltf": self.trend_ltf,
            "trend_conflict": self.trend_conflict,
            "score_htf": self.score_htf,
            "score_ltf": self.score_ltf,
            "nearest_zone_price": self.nearest_zone_price,
            "nearest_zone_type": self.nearest_zone_type,
            "distance_to_zone_pct": self.distance_to_zone_pct,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Resultado completo da varredura."""

    htf: str
    ltf: str
    symbols_requested: int
    symbols_analyzed: int
    errors: dict[str, str] = field(default_factory=dict)
    entry_zone: list[ScanEntry] = field(default_factory=list)
    watch: list[ScanEntry] = field(default_factory=list)
    out_of_zone: list[ScanEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "htf": self.htf,
            "ltf": self.ltf,
            "symbols_requested": self.symbols_requested,
            "symbols_analyzed": self.symbols_analyzed,
            "errors": self.errors,
            "entry_zone": [e.to_dict() for e in self.entry_zone],
            "watch": [e.to_dict() for e in self.watch],
            "out_of_zone": [e.to_dict() for e in self.out_of_zone],
            "disclaimer": (
                "Varredura pontual (snapshot no momento da chamada), não é "
                "monitoramento contínuo/push. Para atualizar, chame o scan "
                "novamente. Nenhum item aqui é recomendação de entrada — "
                "aplique o Quality Filter e a gestão de risco normalmente."
            ),
        }


def _nearest_zone(
    price: float, support: list[float], resistance: list[float]
) -> tuple[float | None, str | None, float | None]:
    """Encontra o nível de suporte/resistência mais próximo do preço atual."""
    candidates: list[tuple[float, str]] = [(lvl, "support") for lvl in support]
    candidates += [(lvl, "resistance") for lvl in resistance]

    if not candidates or price <= 0:
        return None, None, None

    nearest_price, nearest_type = min(candidates, key=lambda item: abs(item[0] - price))
    distance_pct = round(abs(nearest_price - price) / price * 100, 3)
    return nearest_price, nearest_type, distance_pct


def _classify(
    score_htf: int,
    score_ltf: int,
    distance_pct: float | None,
    trend_conflict: bool,
) -> str:
    """Classifica o símbolo em zona_de_entrada / observar / fora_de_zona."""
    combined_score = round((score_htf + score_ltf) / 2)

    if (
        distance_pct is not None
        and distance_pct <= SCAN_ENTRY_ZONE_PCT
        and combined_score >= SCAN_MIN_SCORE_ENTRY
        and not trend_conflict
    ):
        return "zona_de_entrada"

    if (distance_pct is not None and distance_pct <= SCAN_WATCH_ZONE_PCT) or (
        combined_score >= SCAN_MIN_SCORE_WATCH
    ):
        return "observar"

    return "fora_de_zona"


def _scan_one(symbol: str, htf: str, ltf: str) -> ScanEntry:
    symbol = symbol.strip().upper()

    result_htf = run_analysis(symbol=symbol, timeframe=htf)
    result_ltf = run_analysis(symbol=symbol, timeframe=ltf)

    price = result_ltf.price
    nearest_price, nearest_type, distance_pct = _nearest_zone(
        price, result_ltf.support + result_htf.support, result_ltf.resistance + result_htf.resistance
    )

    trend_conflict = (
        result_htf.trend != "Ranging"
        and result_ltf.trend != "Ranging"
        and result_htf.trend != result_ltf.trend
    )

    status = _classify(result_htf.score, result_ltf.score, distance_pct, trend_conflict)

    note = ""
    if trend_conflict:
        note = f"Conflito de tendência: {htf}={result_htf.trend} vs {ltf}={result_ltf.trend}."

    return ScanEntry(
        symbol=symbol,
        status=status,
        price=price,
        trend_htf=result_htf.trend,
        trend_ltf=result_ltf.trend,
        trend_conflict=trend_conflict,
        score_htf=result_htf.score,
        score_ltf=result_ltf.score,
        nearest_zone_price=nearest_price,
        nearest_zone_type=nearest_type,
        distance_to_zone_pct=distance_pct,
        note=note,
    )


def scan_market(
    symbols: list[str],
    htf: str = "4H",
    ltf: str = "1H",
    include_out_of_zone: bool = False,
) -> ScanResult:
    """
    Executa a varredura para uma lista de símbolos.

    Args:
        symbols: lista de pares, ex. ["BTCUSDT", "ETHUSDT", ...].
        htf: timeframe de contexto/tendência (padrão "4H").
        ltf: timeframe de gatilho/execução (padrão "1H").
        include_out_of_zone: se True, inclui também os símbolos sem
            setup (status "fora_de_zona") no retorno — por padrão eles
            ficam de fora para manter o JSON enxuto.

    Returns:
        `ScanResult` com os símbolos separados por status.
    """
    entries: list[ScanEntry] = []
    errors: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=SCAN_CONCURRENCY) as executor:
        futures = {
            executor.submit(_scan_one, symbol, htf, ltf): symbol for symbol in symbols
        }
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                entries.append(future.result())
            except InsufficientDataError as exc:
                errors[symbol] = str(exc)
            except Exception as exc:  # noqa: BLE001 - erro por símbolo não deve derrubar o scan inteiro.
                errors[symbol] = f"Falha ao analisar {symbol}: {exc}"

    entry_zone = sorted(
        (e for e in entries if e.status == "zona_de_entrada"),
        key=lambda e: (e.score_htf + e.score_ltf),
        reverse=True,
    )
    watch = sorted(
        (e for e in entries if e.status == "observar"),
        key=lambda e: (e.distance_to_zone_pct if e.distance_to_zone_pct is not None else 999),
    )
    out_of_zone = [e for e in entries if e.status == "fora_de_zona"] if include_out_of_zone else []

    return ScanResult(
        htf=htf,
        ltf=ltf,
        symbols_requested=len(symbols),
        symbols_analyzed=len(entries),
        errors=errors,
        entry_zone=entry_zone,
        watch=watch,
        out_of_zone=out_of_zone,
    )
