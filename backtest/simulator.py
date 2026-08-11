"""
backtest/simulator.py
========================

Motor de simulação bar-a-bar (candle a candle), sem lookahead.

Regras de execução (documentadas porque mudam o resultado do
backtest — não são triviais):

1. `Strategy.generate_signal()` recebe candles só até e incluindo o
   candle `i` (fechado). Se gerar um `Signal`, a ORDEM É EXECUTADA NA
   ABERTURA do candle `i+1` — nunca no fechamento do candle `i` (isso
   seria assumir que dava pra entrar num preço que só existiu depois
   do sinal ter sido confirmado).

2. Dentro de um candle onde o trade está aberto, se tanto o stop
   quanto o TP estariam tecnicamente dentro do range [low, high]
   daquele candle, o simulador assume que o STOP FOI ATINGIDO PRIMEIRO
   (convenção conservadora-padrão em backtest sem dado de tick/book —
   ver Documento 2, "evitar lookahead bias"). Isso SUBESTIMA
   resultados otimistas de propósito — é a escolha mais defensável sem
   acesso a dado intrabar real.

3. Se o fim dos dados históricos chegar com um trade ainda aberto, ele
   é fechado ao preço de fechamento do último candle disponível, com
   `exit_reason="end_of_data"` — nunca é descartado silenciosamente
   (isso inflaria o Profit Factor ao remover perdas "em aberto").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import pandas as pd

from backtest.costs import ZERO_COST, CostModel
from backtest.strategy import Signal, Strategy
from models.candle import Candle

ExitReason = Literal["take_profit", "stop_loss", "end_of_data"]


@dataclass(frozen=True, slots=True)
class Trade:
    """Um trade simulado, do sinal ao fechamento."""

    strategy_name: str
    direction: str
    signal_time: datetime
    entry_time: datetime
    entry_price_raw: float
    entry_price_effective: float
    stop_price: float
    take_profit_price: float
    exit_time: datetime
    exit_price_raw: float
    exit_price_effective: float
    exit_reason: ExitReason
    bars_held: int
    r_multiple: float
    mae_r: float
    mfe_r: float
    reason: str

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy_name,
            "direction": self.direction,
            "signal_time": self.signal_time.isoformat(),
            "entry_time": self.entry_time.isoformat(),
            "entry_price": self.entry_price_effective,
            "stop_price": self.stop_price,
            "take_profit_price": self.take_profit_price,
            "exit_time": self.exit_time.isoformat(),
            "exit_price": self.exit_price_effective,
            "exit_reason": self.exit_reason,
            "bars_held": self.bars_held,
            "r_multiple": round(self.r_multiple, 3),
            "mae_r": round(self.mae_r, 3),
            "mfe_r": round(self.mfe_r, 3),
            "reason": self.reason,
        }


@dataclass
class _OpenTrade:
    direction: str
    signal_time: datetime
    entry_time: datetime
    entry_price_raw: float
    entry_price_effective: float
    stop_price: float
    take_profit_price: float
    risk_distance: float
    reason: str
    bars_held: int = 0
    worst_price: float = 0.0  # menor preço (long) / maior preço (short) já visto
    best_price: float = 0.0   # maior preço (long) / menor preço (short) já visto


class BacktestSimulator:
    """
    Roda uma `Strategy` sobre uma série de candles, produzindo uma
    lista de `Trade` simulados. Uma posição por vez (sem pirâmide,
    sem hedge) — mantém o modelo simples e auditável.
    """

    def __init__(self, strategy: Strategy, cost_model: CostModel | None = None) -> None:
        self.strategy = strategy
        self.cost_model = cost_model or ZERO_COST
        #: sinais que a Strategy gerou mas que falharam na validação
        #: estrutural (`Signal.validate`) -- não viram trade, mas ficam
        #: registrados para diagnóstico (nunca descartados em silêncio).
        self.rejected_signals: list[tuple[datetime, str]] = []

    def run(self, candles: list[Candle]) -> list[Trade]:
        if len(candles) < self.strategy.min_candles_required() + 2:
            raise ValueError(
                f"Candles insuficientes para rodar '{self.strategy.name}': "
                f"{len(candles)} disponíveis, mínimo "
                f"{self.strategy.min_candles_required() + 2} "
                f"(min_candles_required + 1 para executar + 1 para checar saída)."
            )

        df = pd.DataFrame([c.to_dict() for c in candles])
        df["open_time"] = pd.to_datetime(df["open_time"])
        df = df.set_index("open_time", drop=False).sort_index()

        trades: list[Trade] = []
        open_trade: _OpenTrade | None = None
        min_required = self.strategy.min_candles_required()

        j = min_required
        n = len(df)
        while j < n:
            candle = df.iloc[j]

            if open_trade is None:
                if j + 1 >= n:
                    break  # não há candle seguinte pra executar a entrada -- fim dos dados.

                signal = self.strategy.generate_signal(df.iloc[: j + 1])
                if signal is None:
                    j += 1
                    continue

                entry_candle = df.iloc[j + 1]
                entry_raw = float(entry_candle["open"])

                try:
                    signal.validate(entry_raw)
                except ValueError as exc:
                    self.rejected_signals.append((entry_candle["open_time"], str(exc)))
                    j += 1
                    continue

                entry_effective = self.cost_model.apply_to_entry(signal.direction, entry_raw)
                risk_distance = abs(entry_effective - signal.stop_price)
                if risk_distance <= 0:
                    self.rejected_signals.append(
                        (entry_candle["open_time"], "risk_distance <= 0 (stop igual à entrada)")
                    )
                    j += 1
                    continue

                open_trade = _OpenTrade(
                    direction=signal.direction,
                    signal_time=candle["open_time"],
                    entry_time=entry_candle["open_time"],
                    entry_price_raw=entry_raw,
                    entry_price_effective=entry_effective,
                    stop_price=signal.stop_price,
                    take_profit_price=signal.take_profit_price,
                    risk_distance=risk_distance,
                    reason=signal.reason,
                    worst_price=entry_effective,
                    best_price=entry_effective,
                )
                j += 1  # avança pro candle de entrada -- ele já é checado como candle de exit abaixo.
                continue

            # há um trade aberto: checa esse candle para stop/TP, atualiza MAE/MFE.
            open_trade.bars_held += 1
            exit_info = self._check_exit(open_trade, candle)

            if open_trade.direction == "long":
                open_trade.worst_price = min(open_trade.worst_price, float(candle["low"]))
                open_trade.best_price = max(open_trade.best_price, float(candle["high"]))
            else:
                open_trade.worst_price = max(open_trade.worst_price, float(candle["high"]))
                open_trade.best_price = min(open_trade.best_price, float(candle["low"]))

            is_last_candle = j == n - 1
            if exit_info is not None or is_last_candle:
                exit_price_raw, exit_reason = exit_info or (float(candle["close"]), "end_of_data")
                trades.append(self._close_trade(open_trade, candle["open_time"], exit_price_raw, exit_reason))
                open_trade = None

            j += 1

        return trades

    def _check_exit(self, trade: _OpenTrade, candle: pd.Series) -> tuple[float, ExitReason] | None:
        """Convenção conservadora: stop checado antes do TP quando ambos cabem no candle (ver docstring do módulo)."""
        high, low = float(candle["high"]), float(candle["low"])

        if trade.direction == "long":
            if low <= trade.stop_price:
                return trade.stop_price, "stop_loss"
            if high >= trade.take_profit_price:
                return trade.take_profit_price, "take_profit"
        else:
            if high >= trade.stop_price:
                return trade.stop_price, "stop_loss"
            if low <= trade.take_profit_price:
                return trade.take_profit_price, "take_profit"
        return None

    def _close_trade(
        self, trade: _OpenTrade, exit_time: datetime, exit_price_raw: float, exit_reason: ExitReason
    ) -> Trade:
        exit_effective = self.cost_model.apply_to_exit(trade.direction, exit_price_raw)

        if trade.direction == "long":
            r_multiple = (exit_effective - trade.entry_price_effective) / trade.risk_distance
            mae_r = (trade.worst_price - trade.entry_price_effective) / trade.risk_distance
            mfe_r = (trade.best_price - trade.entry_price_effective) / trade.risk_distance
        else:
            r_multiple = (trade.entry_price_effective - exit_effective) / trade.risk_distance
            mae_r = (trade.entry_price_effective - trade.worst_price) / trade.risk_distance
            mfe_r = (trade.entry_price_effective - trade.best_price) / trade.risk_distance

        return Trade(
            strategy_name=self.strategy.name,
            direction=trade.direction,
            signal_time=trade.signal_time,
            entry_time=trade.entry_time,
            entry_price_raw=trade.entry_price_raw,
            entry_price_effective=trade.entry_price_effective,
            stop_price=trade.stop_price,
            take_profit_price=trade.take_profit_price,
            exit_time=exit_time,
            exit_price_raw=exit_price_raw,
            exit_price_effective=exit_effective,
            exit_reason=exit_reason,
            bars_held=trade.bars_held,
            r_multiple=r_multiple,
            mae_r=min(mae_r, 0.0),  # MAE é sempre <= 0 por definição (excursão adversa)
            mfe_r=max(mfe_r, 0.0),  # MFE é sempre >= 0 por definição (excursão favorável)
            reason=trade.reason,
        )
