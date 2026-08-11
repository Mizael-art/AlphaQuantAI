"""
analysis/score.py
===================

Cálculo do score final da análise (0-100), combinando os principais
sinais gerados pelo restante do pipeline:

- Tendência (EMA + estrutura)
- Momentum (RSI, MACD)
- Estrutura de mercado (BOS/CHOCH)
- Volume (acima/abaixo da média)

O score representa o grau de confluência entre os sinais na direção
da tendência identificada: quanto mais sinais concordam com a
tendência, mais alto o score. Valores próximos de 50 indicam mercado
neutro/indeciso.
"""

from __future__ import annotations


def _trend_direction(trend: str) -> int:
    """Converte a tendência textual em um multiplicador direcional (+1/-1/0)."""
    if trend == "Bullish":
        return 1
    if trend == "Bearish":
        return -1
    return 0


def calculate_score(
    trend: str,
    rsi: float,
    macd_histogram: float,
    bos: bool,
    choch: bool,
    volume_above_average: bool,
) -> int:
    """
    Calcula o score final (0-100) da análise.

    A pontuação parte de uma base neutra de 50 e soma/subtrai pontos
    conforme cada sinal confirma ou contraria a tendência vigente:

    - RSI alinhado com a tendência (>50 em alta, <50 em baixa): +10
    - MACD histogram alinhado com a tendência (positivo em alta,
      negativo em baixa): +15
    - BOS confirmado (continuação de tendência): +15
    - CHOCH detectado (possível reversão): -20 (penaliza a confiança
      na tendência atual, independentemente da direção)
    - Volume acima da média: +10 (mais convicção no movimento atual)

    Args:
        trend: "Bullish", "Bearish" ou "Ranging".
        rsi: valor atual do RSI (0-100).
        macd_histogram: valor atual do histograma do MACD.
        bos: se houve Break of Structure na direção da tendência.
        choch: se houve Change of Character (sinal de reversão).
        volume_above_average: se o volume atual está acima da média.

    Returns:
        Score inteiro, limitado ao intervalo [0, 100].
    """
    direction = _trend_direction(trend)
    score = 50.0

    if direction != 0:
        # RSI: em tendência de alta, RSI > 50 reforça o movimento;
        # em tendência de baixa, RSI < 50 reforça o movimento.
        rsi_aligned = (direction == 1 and rsi > 50) or (direction == -1 and rsi < 50)
        score += 10 if rsi_aligned else -10

        # MACD histogram: positivo reforça alta, negativo reforça baixa.
        macd_aligned = (direction == 1 and macd_histogram > 0) or (
            direction == -1 and macd_histogram < 0
        )
        score += 15 if macd_aligned else -15

        if bos:
            score += 15

        if volume_above_average:
            score += 10

    if choch:
        # CHOCH é penalizado independentemente da tendência, pois
        # representa uma ameaça à validade da tendência vigente.
        score -= 20

    return int(max(0, min(100, round(score))))
