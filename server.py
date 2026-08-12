"""
server.py
=========

API HTTP (FastAPI) do AlphaQuant Engine -- é isto que o AlphaQuant X
(GPT customizado / outro consumidor) chama via Action/HTTP.

Endpoints:
    GET /snapshot   -- Market Snapshot multi-timeframe completo
                        (indicadores + estrutura + SMC + volume
                        profile + estatística + derivativos +
                        confluência + consenso multi-exchange).
                        Este é o endpoint principal.
    GET /analyze    -- análise de um único timeframe (compat. Fase 1).
    GET /scan       -- varredura multi-símbolo (scanner/).
    GET /health     -- health check.
    GET /openapi.json -- schema OpenAPI (gerado automaticamente pelo
                        FastAPI), usado para configurar a Action do GPT.

Nota de reconstrução: este arquivo veio vazio (0 bytes) no zip
`AlphaQuantEngine_v2_6_structure_consensus`. Foi reconstruído a partir
do README (seção "Uso") e das assinaturas reais de
`snapshot.build_market_snapshot`, `app.run_analysis` e
`scanner.scan_market` -- ver CHANGELOG_v2.6_rebuild.md.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app import InsufficientDataError, run_analysis
from backtest.costs import CostModel
from backtest.history_fetcher import HistoryFetcher, HistoryFetchError
from backtest.performance import calculate_performance
from backtest.registry import StrategyNotRegisteredError, available_strategies, build_strategy
from backtest.simulator import BacktestSimulator
from config import (
    DEFAULT_SCAN_HTF,
    DEFAULT_SCAN_LTF,
    DEFAULT_SCAN_SYMBOLS,
    DEFAULT_SYMBOL,
    DEFAULT_TIMEFRAME,
    SCAN_MAX_SYMBOLS,
)
from providers import DataUnavailableError, build_default_router
from scanner.screener import scan_market
from snapshot.market_snapshot import DEFAULT_TIMEFRAMES, build_market_snapshot


class FlexibleJSONResponse(Response):
    """
    Resposta JSON que não recorta o schema OpenAPI a um `response_model`
    fixo (`additionalProperties: true` implícito) -- o payload varia
    conforme timeframes/erros/consenso multi-exchange disponíveis, e
    engessar um schema aqui obrigaria a reimportar a Action do GPT a
    cada campo novo adicionado nos motores internos.
    """

    media_type = "application/json"

    def render(self, content) -> bytes:  # noqa: ANN001 - assinatura herdada do Starlette.
        return json.dumps(content, ensure_ascii=False, default=str).encode("utf-8")


# Endpoints que usam FlexibleJSONResponse não têm um shape fixo -- por
# isso não declaram `response_model`. Sem isso, o FastAPI não consegue
# inferir o schema OpenAPI da resposta e cai em `{"type": "string"}`
# (trata como corpo opaco). O validador de schema do ChatGPT Actions
# aceita objeto livre, mas exige a chave `properties` presente mesmo
# quando vazia -- só `additionalProperties: true` sozinho é rejeitado
# ("object schema missing properties"). Este override documenta
# corretamente "isto é um objeto JSON, com campos variáveis" nos 4
# endpoints de payload dinâmico.
_FREEFORM_JSON_OBJECT_RESPONSES: dict[int | str, dict] = {
    200: {
        "description": "Successful Response",
        "content": {
            "application/json": {
                "schema": {"type": "object", "properties": {}, "additionalProperties": True}
            }
        },
    }
}


class HealthResponse(BaseModel):
    status: str


class BacktestStrategiesResponse(BaseModel):
    strategies: list[str]


app = FastAPI(
    title="AlphaQuant Engine",
    description=(
        "Backend de dados de mercado (Spot + Futures + multi-exchange) "
        "para o AlphaQuant X: indicadores técnicos, estrutura de "
        "mercado, Smart Money Concepts, Volume Profile, estatística, "
        "derivativos e consenso multi-exchange (preço e estrutura), "
        "consumidos sem depender de prints de gráfico."
    ),
    version="3.1",
    servers=[{"url": "https://alphaquantai-1.onrender.com", "description": "Produção (Render)"}],
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Health check simples -- não toca em nenhuma API externa."""
    return HealthResponse(status="ok")


@app.get("/snapshot", response_class=FlexibleJSONResponse, responses=_FREEFORM_JSON_OBJECT_RESPONSES)
def get_snapshot(
    symbol: str = Query(default=DEFAULT_SYMBOL, description="Par de negociação, ex.: ETHUSDT."),
    timeframes: str = Query(
        default=",".join(DEFAULT_TIMEFRAMES),
        description="Timeframes separados por vírgula, ex.: 15m,1H,4H,1D.",
    ),
) -> dict:
    """
    Market Snapshot completo: indicadores, estrutura, SMC, volume
    profile, estatística, derivativos, confluência multi-timeframe e
    (quando habilitado em `config.ENABLE_CROSS_EXCHANGE`) consenso
    multi-exchange de preço e estrutura. **Endpoint principal.**
    """
    tf_tuple = tuple(tf.strip() for tf in timeframes.split(",") if tf.strip())
    try:
        result = build_market_snapshot(symbol=symbol, timeframes=tf_tuple)
    except Exception as exc:  # noqa: BLE001 - erro de topo, reportado como HTTP 502.
        raise HTTPException(status_code=502, detail=f"Falha ao gerar snapshot: {exc}") from exc

    return result.to_dict()


@app.get("/analyze", response_class=FlexibleJSONResponse, responses=_FREEFORM_JSON_OBJECT_RESPONSES)
def get_analyze(
    symbol: str = Query(default=DEFAULT_SYMBOL, description="Par de negociação, ex.: ETHUSDT."),
    timeframe: str = Query(default=DEFAULT_TIMEFRAME, description="Timeframe único, ex.: 4H."),
) -> dict:
    """Análise de um único timeframe -- mantido por compatibilidade (escopo da Fase 1)."""
    try:
        result = run_analysis(symbol=symbol, timeframe=timeframe)
    except InsufficientDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Falha ao gerar análise: {exc}") from exc

    return result.to_dict()


@app.get("/scan", response_class=FlexibleJSONResponse, responses=_FREEFORM_JSON_OBJECT_RESPONSES)
def get_scan(
    symbols: str | None = Query(
        default=None,
        description=f"Símbolos separados por vírgula (padrão: watchlist de {len(DEFAULT_SCAN_SYMBOLS)} ativos em config.DEFAULT_SCAN_SYMBOLS).",
    ),
    htf: str = Query(default=DEFAULT_SCAN_HTF, description="Timeframe de contexto/tendência."),
    ltf: str = Query(default=DEFAULT_SCAN_LTF, description="Timeframe de gatilho/execução."),
    include_out_of_zone: bool = Query(default=False, description="Inclui também símbolos sem setup no retorno."),
) -> dict:
    """Varredura multi-símbolo pontual -- ver `scanner/screener.py` para a lógica de classificação."""
    symbol_list = (
        [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if symbols
        else DEFAULT_SCAN_SYMBOLS
    )
    if len(symbol_list) > SCAN_MAX_SYMBOLS:
        raise HTTPException(
            status_code=422,
            detail=f"Máximo de {SCAN_MAX_SYMBOLS} símbolos por chamada, {len(symbol_list)} recebidos.",
        )

    result = scan_market(symbol_list, htf=htf, ltf=ltf, include_out_of_zone=include_out_of_zone)
    return result.to_dict()


# ----------------------------------------------------------------------
# Backtest
# ----------------------------------------------------------------------
# O motor de backtest (backtest/) já existia completo (HistoryFetcher,
# BacktestSimulator, Strategy, calculate_performance) mas não tinha
# NENHUM endpoint HTTP -- por isso o GPT não conseguia rodar backtest:
# não havia como chamar isso via Action. Os dois endpoints abaixo
# fecham esse buraco.


class BacktestCostModelRequest(BaseModel):
    """Custos de execução (bps). Default é zero em cada campo -- ver `backtest/costs.py`: um backtest sem custo informado é reportado como resultado BRUTO, nunca com fricção "realista" inventada."""

    spread_bps: float = 0.0
    slippage_bps: float = 0.0
    commission_bps: float = 0.0


class BacktestRequest(BaseModel):
    symbol: str = Field(description="Par de negociação, ex.: ETHUSDT. Aceita formato TradingView (ex.: 'BYBIT:ETHUSDT.P').")
    timeframe: str = Field(description="Timeframe dos candles do backtest, ex.: 1H, 4H, 1D.")
    start: datetime = Field(description="Início do range histórico (ISO 8601).")
    end: datetime | None = Field(default=None, description="Fim do range histórico (padrão: agora).")
    strategy: str = Field(default="sma_cross", description="Nome da estratégia registrada. Ver GET /backtest/strategies.")
    strategy_params: dict[str, Any] = Field(default_factory=dict, description="Parâmetros da estratégia (ex.: {\"fast_period\": 10}).")
    cost_model: BacktestCostModelRequest = Field(default_factory=BacktestCostModelRequest)
    min_candles: int = Field(default=50, description="Mínimo de candles exigido no range -- abaixo disso, erro em vez de rodar com amostra insuficiente.")


@app.get("/backtest/strategies", response_model=BacktestStrategiesResponse)
def get_backtest_strategies() -> BacktestStrategiesResponse:
    """Lista as estratégias registradas e utilizáveis no campo `strategy` de POST /backtest."""
    return BacktestStrategiesResponse(strategies=available_strategies())


@app.post("/backtest", response_class=FlexibleJSONResponse, responses=_FREEFORM_JSON_OBJECT_RESPONSES)
def post_backtest(request: BacktestRequest) -> dict:
    """Roda backtest bar-a-bar (sem lookahead) de uma estratégia registrada sobre histórico real. Retorna performance (win rate, R médio, profit factor, drawdown) e trades. Erros de dados voltam como HTTP 422 com o motivo, nunca resultado parcial."""
    try:
        strategy = build_strategy(request.strategy, request.strategy_params)
    except StrategyNotRegisteredError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    cost_model = CostModel(
        spread_bps=request.cost_model.spread_bps,
        slippage_bps=request.cost_model.slippage_bps,
        commission_bps=request.cost_model.commission_bps,
    )

    fetcher = HistoryFetcher(router=build_default_router())
    try:
        history = fetcher.fetch(
            symbol=request.symbol,
            timeframe=request.timeframe,
            start=request.start,
            end=request.end,
            min_candles=request.min_candles,
        )
    except (HistoryFetchError, DataUnavailableError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    simulator = BacktestSimulator(strategy=strategy, cost_model=cost_model)
    try:
        trades = simulator.run(history.candles)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    performance = calculate_performance(trades) if trades else None

    return {
        "meta": history.to_meta_dict(),
        "strategy": {"name": strategy.name, "params": request.strategy_params},
        "cost_model": {
            "is_zero_cost": cost_model.is_zero_cost,
            "spread_bps": cost_model.spread_bps,
            "slippage_bps": cost_model.slippage_bps,
            "commission_bps": cost_model.commission_bps,
        },
        "trades_count": len(trades),
        "rejected_signals_count": len(simulator.rejected_signals),
        "performance": performance.to_dict() if performance is not None else None,
        "performance_note": (
            None
            if performance is not None
            else "A estratégia não gerou nenhum trade válido no período -- sem base para métricas de performance."
        ),
        "trades": [t.to_dict() for t in trades],
    }
