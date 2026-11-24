"""
Volatilidad del precio de Polymarket con un GARCH(1,1).

El precio de un resultado se mueve aunque no pase nada en la cancha (ordenes
grandes, libro de ordenes delgado). Si la diferencia entre el modelo y el
precio es del tamano de ese ruido, no es una ventaja: el mercado la puede
borrar solo en un par de minutos. Este modulo estima ese ruido.

1. Historial 1-min del precio del resultado (Polymarket CLOB) de las
   ultimas VENTANA_HORAS, llevado a una malla regular de PASO_MIN minutos
   (ultimo precio conocido) y en logit (los cambios de 0.50 a 0.55 y de 0.95
   a 0.99 no son comparables en probabilidad, en logit si). La malla de 5
   min evita que los muchos minutos sin operaciones (cambio = 0) colapsen la
   varianza estimada.
2. GARCH(1,1) con errores t de Student sobre esos cambios
   (libreria arch): la volatilidad de hoy depende de la de ayer y del
   ultimo movimiento, que es lo que pasa en vivo (tras un gol el precio se
   vuelve nervioso un rato).
3. Pronostico de la varianza acumulada a HORIZONTE_MIN minutos (lo que
   tarda en ejecutarse una compra) y paso a puntos de probabilidad:
   sigma_p = sigma_logit * p * (1 - p).
4. ruido = Z_RUIDO * sigma_p (un lado, 95%). La plataforma exige que la
   ventaja supere al menos ese ruido.
5. "agitado": el ultimo movimiento supera UMBRAL_AGITADO desviaciones de su
   volatilidad condicional (mismo criterio de "movimiento significativo"
   que src/processing/polymarket.py) y es de al menos MOVIMIENTO_MIN en
   probabilidad. Con el mercado agitado no se da BUENO en esa vuelta.

Los saltos de noticia (un gol mueve el precio de 0.44 a 0.04 en minutos)
no son ruido: son informacion. Los cambios mayores a SALTO_NOTICIA en
logit se excluyen del ajuste (si no, tras cada gol el GARCH pronosticaria
volatilidades absurdas), pero si el ultimo cambio es un salto, el mercado
cuenta como agitado. El ruido tiene un tope de RUIDO_MAX.

Con menos de MIN_PUNTOS cambios, o si el ajuste falla, se usa una media
movil exponencial (EWMA, lambda 0.94) como respaldo.
"""

import logging
import math
import time
from datetime import timedelta

import numpy as np

logger = logging.getLogger(__name__)

MIN_PUNTOS = 100
PASO_MIN = 5
HORIZONTE_MIN = 2
MOVIMIENTO_MIN = 0.02
SALTO_NOTICIA = 1.0  # en logit: p. ej. de 0.50 a 0.73
RUIDO_MAX = 0.10
Z_RUIDO = 1.65
UMBRAL_AGITADO = 3.0
LAMBDA_EWMA = 0.94
REAJUSTE_SEG = 300
VENTANA_HORAS = 12

_CACHE = {}  # token -> (cuando, resultado)


def _logit(p):
    p = min(max(float(p), 0.005), 0.995)
    return math.log(p / (1 - p))


def _ewma(r):
    var = float(np.var(r)) if len(r) > 1 else 1e-6
    serie = []
    for x in r:
        serie.append(var)
        var = LAMBDA_EWMA * var + (1 - LAMBDA_EWMA) * x * x
    return var, serie


def malla_regular(puntos, paso_min=PASO_MIN):
    """[(datetime, precio)] -> precios cada paso_min minutos (ultimo precio conocido)."""
    if not puntos:
        return []
    puntos = sorted(puntos)
    t, fin = puntos[0][0], puntos[-1][0]
    salida, i, ultimo = [], 0, puntos[0][1]
    while t <= fin:
        while i < len(puntos) and puntos[i][0] <= t:
            ultimo = puntos[i][1]
            i += 1
        salida.append(ultimo)
        t = t + timedelta(minutes=paso_min)
    salida.append(puntos[-1][1])
    return salida


def volatilidad(precios, paso_min=PASO_MIN) -> dict:
    """Ruido esperado del precio (en puntos de probabilidad) a HORIZONTE_MIN minutos.

    precios: precios 0-1 en orden cronologico, uno cada paso_min minutos (malla_regular).
    """
    if len(precios) < 3:
        return None
    r_todo = np.diff([_logit(p) for p in precios])
    r = r_todo[np.abs(r_todo) <= SALTO_NOTICIA]  # sin saltos de noticia (goles)
    if len(r) < 2:
        r = np.zeros(2)
    p_actual = float(precios[-1])
    escala = HORIZONTE_MIN / paso_min  # varianza de un paso -> varianza a HORIZONTE_MIN minutos
    metodo = "garch"
    try:
        if len(r) < MIN_PUNTOS or np.allclose(r, 0):
            raise ValueError("pocos datos para GARCH")
        from arch import arch_model

        ajuste = arch_model(r * 100, mean="Zero", vol="GARCH", p=1, q=1, dist="t", rescale=False).fit(
            disp="off", show_warning=False
        )
        var_h = float(ajuste.forecast(horizon=1, reindex=False).variance.values[-1][0]) / 1e4 * escala
        sigma_ultimo = float(ajuste.conditional_volatility[-1]) / 100
        if not math.isfinite(var_h) or var_h <= 0:
            raise ValueError("pronostico invalido")
    except Exception as error:  # el respaldo cubre cualquier falla de ajuste
        logger.debug("GARCH no disponible (%s); se usa EWMA", error)
        metodo = "ewma"
        var, serie = _ewma(r)
        var_h = var * escala
        sigma_ultimo = math.sqrt(serie[-1]) if serie else math.sqrt(var)

    sigma_p = min(math.sqrt(var_h) * p_actual * (1 - p_actual), RUIDO_MAX / Z_RUIDO)
    salto = abs(float(precios[-1]) - float(precios[-2]))
    ultimo = float(r_todo[-1])
    return {
        "metodo": metodo,
        "n": int(len(r)),
        "sigma_p": sigma_p,
        "ruido": Z_RUIDO * sigma_p,
        "agitado": bool(
            salto >= MOVIMIENTO_MIN
            and (abs(ultimo) > SALTO_NOTICIA or (sigma_ultimo > 0 and abs(ultimo) > UMBRAL_AGITADO * sigma_ultimo))
        ),
    }


def volatilidad_token(token_id, price_history_fn) -> dict:
    """volatilidad() del token con su historial de las ultimas VENTANA_HORAS; reajuste cada 5 min."""
    guardado = _CACHE.get(token_id)
    if guardado and time.time() - guardado[0] < REAJUSTE_SEG:
        return guardado[1]
    inicio = time.time() - VENTANA_HORAS * 3600
    resultado = volatilidad(malla_regular(price_history_fn(token_id, inicio, 1)))
    _CACHE[token_id] = (time.time(), resultado)
    return resultado
