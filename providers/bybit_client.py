"""
providers/bybit_client.py
==========================

Cliente HTTP de baixo nível para a API pública V5 da Bybit
(https://bybit-exchange.github.io/docs/v5/intro).

Cobre os endpoints de Market Data documentados oficialmente para
`category` em {spot, linear, inverse}:

- GET /v5/market/kline              -> candles OHLCV
- GET /v5/market/tickers             -> último preço / bid / ask
- GET /v5/market/instruments-info    -> validação de símbolo existente

Nenhum endpoint aqui foi inventado — todos batem com a documentação
oficial V5. Nenhuma API Key é necessária para dados de mercado.

IMPORTANTE (TradFi): a Bybit V5 documenta oficialmente apenas
category=spot/linear/inverse/option. Não existe documentação oficial
pública confirmando um `category=tradfi` (ou equivalente) para
XAUUSD/NAS100/etc. Este cliente permite passar qualquer `category`
literal — quem decide se um símbolo TradFi é "tentável" via `linear`
é o `providers/bybit_provider.py`, de forma explicitamente marcada
como experimental. Ver esse módulo para detalhes.
"""

from __future__ import annotations

import time
from typing import Any

import requests

BYBIT_BASE_URL = "https://api.bybit.com"

ENDPOINT_KLINE = "/v5/market/kline"
ENDPOINT_TICKERS = "/v5/market/tickers"
ENDPOINT_INSTRUMENTS_INFO = "/v5/market/instruments-info"

# Bybit V5 usa intervalos numéricos em minutos (ou D/W/M), diferente do
# formato "1h"/"4h" da Binance.
BYBIT_INTERVAL_MAP: dict[str, str] = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1H": "60", "2H": "120", "4H": "240", "6H": "360", "12H": "720",
    "1D": "D", "1W": "W", "1M": "M",
}

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1.5
REQUEST_TIMEOUT = 10


class BybitAPIError(Exception):
    """Erro genérico levantado quando a Bybit retorna uma resposta inválida ou de erro."""


class BybitClient:
    """
    Cliente HTTP para os endpoints públicos de Market Data (V5) da Bybit.

    Exemplo:
        client = BybitClient()
        candles = client.get_kline(category="linear", symbol="BTCUSDT", interval="240")
        ticker = client.get_tickers(category="linear", symbol="BTCUSDT")
    """

    def __init__(self, base_url: str = BYBIT_BASE_URL, timeout: int = REQUEST_TIMEOUT) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self._session = requests.Session()

    def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        last_error: Exception | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self._session.get(url, params=params, timeout=self.timeout)

                if response.status_code != 200:
                    raise BybitAPIError(
                        f"Bybit retornou status {response.status_code} para {endpoint}: {response.text}"
                    )

                data = response.json()

                # Formato de erro padrão V5: {"retCode": 0, "retMsg": "OK", "result": {...}}
                # retCode != 0 sempre indica falha de negócio (símbolo inválido, etc.).
                if data.get("retCode") != 0:
                    raise BybitAPIError(
                        f"Erro da Bybit em {endpoint}: "
                        f"retCode={data.get('retCode')} retMsg={data.get('retMsg')}"
                    )

                return data["result"]

            except (requests.RequestException, BybitAPIError) as exc:
                last_error = exc
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue

        raise BybitAPIError(
            f"Falha ao consultar {endpoint} após {MAX_RETRIES} tentativas: {last_error}"
        ) from last_error

    def get_kline(
        self,
        category: str,
        symbol: str,
        interval: str,
        limit: int = 500,
        start: int | None = None,
        end: int | None = None,
    ) -> list[list[str]]:
        """
        GET /v5/market/kline

        Returns:
            Lista de candles no formato bruto da Bybit V5:
            [start, open, high, low, close, volume, turnover]
            em ordem do mais recente para o mais antigo.
        """
        params: dict[str, Any] = {
            "category": category,
            "symbol": symbol,
            "interval": interval,
            "limit": min(limit, 1000),
        }
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end

        result = self._request(ENDPOINT_KLINE, params=params)
        return result.get("list", [])

    def get_tickers(self, category: str, symbol: str) -> dict[str, Any]:
        """GET /v5/market/tickers -> primeiro item da lista (ticker do símbolo pedido)."""
        result = self._request(ENDPOINT_TICKERS, params={"category": category, "symbol": symbol})
        items = result.get("list", [])
        if not items:
            raise BybitAPIError(f"Nenhum ticker retornado para {symbol} (category={category}).")
        return items[0]

    def get_instruments_info(self, category: str, symbol: str) -> dict[str, Any] | None:
        """
        GET /v5/market/instruments-info

        Returns:
            Dict com informações do instrumento, ou None se o símbolo
            não existir nessa category (útil para `supports()` sem
            precisar tratar exceção).
        """
        try:
            result = self._request(
                ENDPOINT_INSTRUMENTS_INFO, params={"category": category, "symbol": symbol}
            )
        except BybitAPIError:
            return None
        items = result.get("list", [])
        return items[0] if items else None

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "BybitClient":
        return self

    def __exit__(self, *_exc_info: Any) -> None:
        self.close()
