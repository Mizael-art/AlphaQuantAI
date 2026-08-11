"""
server.py
=========

Expõe o AlphaQuant Engine como uma API HTTP (FastAPI), para que o
AlphaQuant X (GPT personalizado) consiga consumir os dados via
"Actions" — GPTs não executam código Python local, apenas chamam
endpoints HTTPS que retornam JSON.

Rodar localmente:
    uvicorn server:app --reload --port 8000

Documentação automática (schema OpenAPI):
    http://localhost:8000/docs        (Swagger UI, para testar manualmente)
    http://localhost:8000/openapi.json (schema usado na Action do GPT)
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

from app import InsufficientDataError, run_analysis
from config import (
    DEFAULT_KLINES_LIMIT,
    DEFAULT_SCAN_HTF,
    DEFAULT_SCAN_LTF,
    DEFAULT_SCAN_SYMBOLS,
    DEFAULT_SYMBOL,
    DEFAULT_TIMEFRAME,
    SCAN_MAX_SYMBOLS,
    TIMEFRAME_MAP,
)
from providers import DataUnavailableError
from scanner import scan_market
from snapshot import DEFAULT_TIMEFRAMES, build_market_snapshot
from symbols import SymbolNotRecognizedError


class FlexibleJSONResponse(BaseModel):
    """Resposta com estrutura variável (indicadores, SMC, volume profile, derivativos)."""

    # Os payloads de /analyze e /snapshot têm estrutura profundamente
    # aninhada e variável, então tipar cada campo em um schema Pydantic
    # rígido seria frágil e exigiria reescrever o modelo a cada novo
    # campo adicionado à análise. Este modelo permissivo (extra="allow",
    # sem campos fixos) gera um schema OpenAPI com "properties": {} e
    # "additionalProperties": true — o suficiente para o validador do
    # ChatGPT Actions aceitar o schema (que exige a chave "properties"
    # em todo objeto), enquanto deixa o payload real passar sem perdas.
    model_config = ConfigDict(extra="allow")


class HealthResponse(BaseModel):
    """Resposta do health check."""

    status: str


app = FastAPI(
    title="AlphaQuant Engine API",
    description=(
        "Fornece dados estruturados de mercado (indicadores + estrutura + SMC + "
        "volume profile + derivativos + confluência multi-timeframe) para o "
        "AlphaQuant X analisar automaticamente, sem necessidade de screenshots."
    ),
    version="2.0.0",
    # O schema OpenAPI gerado pelo FastAPI não sabe, por padrão, qual é o
    # domínio público onde a API está hospedada — ele só descreve as rotas.
    # As Actions do GPT EXIGEM um campo "servers" com uma URL absoluta no
    # schema, senão rejeitam a importação com o erro "Não foi possível
    # encontrar uma URL válida em servers". Ajuste esta URL para o domínio
    # real do seu deploy (Render, etc.) sempre que ele mudar.
    servers=[{"url": "https://alphaquantai-1.onrender.com", "description": "Produção (Render)"}],
)

# CORS liberado para qualquer origem: necessário porque a Action do GPT
# chama o endpoint a partir dos servidores da OpenAI, não do navegador
# do usuário — não há como restringir por domínio de forma útil aqui.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get(
    "/analyze",
    summary="Gera a análise estruturada de um símbolo/timeframe (single-timeframe)",
    response_model=FlexibleJSONResponse,
)
def analyze(
    symbol: str = Query(DEFAULT_SYMBOL, description="Par de negociação, ex.: BTCUSDT, ETHUSDT"),
    timeframe: str = Query(
        DEFAULT_TIMEFRAME,
        description=f"Timeframe. Valores aceitos: {list(TIMEFRAME_MAP.keys())}",
    ),
    limit: int = Query(
        DEFAULT_KLINES_LIMIT, ge=200, le=1000, description="Quantidade de candles (200-1000)"
    ),
) -> dict:
    """Endpoint de 1 timeframe: indicadores básicos + estrutura + score. Para análise completa, use /snapshot."""
    try:
        result = run_analysis(symbol=symbol, timeframe=timeframe, limit=limit)
    except SymbolNotRecognizedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DataUnavailableError as exc:
        # Nenhum provider elegível (Bybit/Binance/TradFi) conseguiu
        # fornecer dados válidos — nunca inventamos preço/candle.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InsufficientDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - erro de topo não classificado.
        raise HTTPException(status_code=502, detail=f"Falha ao consultar o provider de dados: {exc}") from exc

    return result.to_dict()


@app.get(
    "/snapshot",
    summary="Gera o Market Snapshot completo (multi-timeframe + SMC + derivativos)",
    response_model=FlexibleJSONResponse,
)
def snapshot(
    symbol: str = Query(DEFAULT_SYMBOL, description="Par de negociação, ex.: BTCUSDT, ETHUSDT"),
    timeframes: str = Query(
        ",".join(DEFAULT_TIMEFRAMES),
        description=(
            "Lista de timeframes separados por vírgula, ex.: '15m,1H,4H,1D'. "
            f"Valores aceitos: {list(TIMEFRAME_MAP.keys())}"
        ),
    ),
) -> dict:
    """Endpoint principal: indicadores, estrutura, SMC, volume profile, estatística, derivativos e confluência multi-timeframe em uma única chamada. Use este em vez de /analyze."""
    requested_timeframes = tuple(tf.strip() for tf in timeframes.split(",") if tf.strip())

    invalid = [tf for tf in requested_timeframes if tf not in TIMEFRAME_MAP]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Timeframes inválidos: {invalid}. Valores aceitos: {list(TIMEFRAME_MAP.keys())}",
        )

    try:
        result = build_market_snapshot(symbol=symbol, timeframes=requested_timeframes)
    except SymbolNotRecognizedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DataUnavailableError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - erro de topo não classificado.
        raise HTTPException(status_code=502, detail=f"Falha ao consultar o provider de dados: {exc}") from exc

    return result.to_dict()


@app.get(
    "/scan",
    summary="Varre vários símbolos e devolve os que estão em zona de entrada ou para observar",
    response_model=FlexibleJSONResponse,
)
def scan(
    symbols: str = Query(
        "",
        description=(
            "Lista de símbolos separados por vírgula, ex.: 'BTCUSDT,ETHUSDT,SOLUSDT'. "
            "Se vazio, usa a watchlist padrão do servidor (DEFAULT_SCAN_SYMBOLS)."
        ),
    ),
    htf: str = Query(
        DEFAULT_SCAN_HTF,
        description=f"Timeframe de contexto/tendência. Valores aceitos: {list(TIMEFRAME_MAP.keys())}",
    ),
    ltf: str = Query(
        DEFAULT_SCAN_LTF,
        description=f"Timeframe de gatilho/execução. Valores aceitos: {list(TIMEFRAME_MAP.keys())}",
    ),
    include_out_of_zone: bool = Query(
        False,
        description="Se True, inclui também os símbolos sem setup no momento (payload maior).",
    ),
) -> dict:
    """
    Varredura multi-símbolo (screener). Roda a mesma análise do
    `/analyze` em cada símbolo (timeframe HTF + LTF) e classifica cada
    um em `entry_zone` (dentro da zona de entrada agora), `watch`
    (aproximando-se / setup se formando) ou `out_of_zone` (sem
    confluência — omitido por padrão).

    Use este endpoint quando o usuário pedir para "procurar
    oportunidades", "varrer o mercado", "ver o que está perto de
    entrada" etc., em vez de pedir análise de um símbolo específico.
    """
    requested = [s.strip().upper() for s in symbols.split(",") if s.strip()] or list(
        DEFAULT_SCAN_SYMBOLS
    )

    if len(requested) > SCAN_MAX_SYMBOLS:
        raise HTTPException(
            status_code=400,
            detail=f"Máximo de {SCAN_MAX_SYMBOLS} símbolos por chamada, recebido {len(requested)}.",
        )

    for tf in (htf, ltf):
        if tf not in TIMEFRAME_MAP:
            raise HTTPException(
                status_code=400,
                detail=f"Timeframe inválido: {tf}. Valores aceitos: {list(TIMEFRAME_MAP.keys())}",
            )

    result = scan_market(symbols=requested, htf=htf, ltf=ltf, include_out_of_zone=include_out_of_zone)
    return result.to_dict()


@app.get("/health", summary="Verifica se a API está no ar", response_model=HealthResponse)
def health() -> dict:
    """Endpoint simples de health check, útil para o provedor de deploy."""
    return {"status": "ok"}
