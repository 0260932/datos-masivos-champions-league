"""
Modelo de xG propio entrenado con tiros reales de ESPN.

ESPN publica, para cada tiro del comentario jugada por jugada, la posicion
en la cancha (fieldPositionX / fieldPositionY, de 0 a 100, con la porteria
rival siempre en X = 100 y el centro en Y = 50). Con eso se calcula lo que
usan los modelos de xG publicos:
- distancia al centro de la porteria (metros, cancha de 105 x 68),
- angulo de tiro: cuanto de la porteria (7.32 m) "ve" el tirador,
- si es cabezazo y si es penal.

Una regresion logistica (scikit-learn) sobre esas variables da la
probabilidad de gol de cada tiro. Se entrena con partidos anteriores al
periodo de la prueba historica (src/models/backtest_xg.py), para que esa
prueba nunca use tiros que el modelo ya vio.

Entrada: scoreboard y summary de ESPN de las 10 ligas (cache en data/raw/backtest/).
Salida:  data/live/modelo/xg_modelo.json (coeficientes y validacion).

Ejecucion:
    python -m src.models.xg_modelo                 # tiros de hace 22 a 150 dias
    python -m src.models.xg_modelo --desde 150 --hasta 22
"""

import argparse
import json
import logging
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

from src.live.modelo import SNAPSHOT_DIR

ROOT_DIR = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT_DIR / "data" / "raw" / "backtest"
MODELO_PATH = SNAPSHOT_DIR / "xg_modelo.json"

LARGO_M, ANCHO_M, PORTERIA_M = 105.0, 68.0, 7.32
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{liga}/{recurso}"
LIGAS_ESPN = ("uefa.champions", "eng.1", "esp.1", "ita.1", "ger.1", "fra.1", "ned.1", "por.1", "eng.2", "bra.1")
TIPOS_TIRO = ("goal", "shot-on-target", "shot-off-target", "shot-blocked", "shot-hit-woodwork")
VARIABLES = ("distancia", "angulo", "cabezazo", "distancia_x_cabezazo")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Geometria del tiro
# ---------------------------------------------------------------------------

def geometria(x: float, y: float):
    """(distancia en m, angulo en radianes) desde la posicion ESPN (0-100) a la porteria rival."""
    dx = (100.0 - float(x)) / 100.0 * LARGO_M
    dy = (float(y) - 50.0) / 100.0 * ANCHO_M
    distancia = math.hypot(dx, dy)
    mitad = PORTERIA_M / 2
    angulo = math.atan2(PORTERIA_M * dx, dx * dx + dy * dy - mitad * mitad)
    if angulo < 0:
        angulo += math.pi
    return distancia, angulo


def variables(x, y, cabezazo):
    distancia, angulo = geometria(x, y)
    return [distancia, angulo, float(cabezazo), distancia * float(cabezazo)]


def es_tiro(tipo: str) -> bool:
    return tipo in TIPOS_TIRO or tipo.startswith("goal") or tipo.startswith("penalty")


def tiro_de_jugada(entrada: dict):
    """Diccionario del tiro (posicion, cabezazo, penal, gol) o None si no es tiro con posicion."""
    jugada = entrada.get("play") or {}
    tipo = (jugada.get("type") or {}).get("type", "")
    texto = (entrada.get("text") or jugada.get("text") or "").lower()
    if not es_tiro(tipo) or "own goal" in texto:
        return None
    x, y = jugada.get("fieldPositionX"), jugada.get("fieldPositionY")
    if x is None or y is None:
        return None
    return {
        "x": float(x), "y": float(y),
        "cabezazo": "header" in texto,
        "penal": "penalty" in texto or tipo.startswith("penalty"),
        "gol": tipo.startswith("goal") or tipo == "penalty---scored",
        "texto": texto,
    }


# ---------------------------------------------------------------------------
# Modelo guardado
# ---------------------------------------------------------------------------

_MODELO = None


def cargar():
    global _MODELO
    if _MODELO is None and MODELO_PATH.exists():
        _MODELO = json.loads(MODELO_PATH.read_text(encoding="utf-8"))
    return _MODELO


def xg_posicion(x, y, cabezazo=False, penal=False):
    """xG del tiro con el modelo entrenado; None si no hay modelo guardado."""
    modelo = cargar()
    if modelo is None:
        return None
    if penal:
        return modelo["xg_penal"]
    z = modelo["intercepto"] + sum(c * v for c, v in zip(modelo["coeficientes"], variables(x, y, cabezazo)))
    return 1.0 / (1.0 + math.exp(-z))


# ---------------------------------------------------------------------------
# Datos y entrenamiento
# ---------------------------------------------------------------------------

def _cache_json(nombre, obtener):
    path = CACHE_DIR / f"{nombre}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    datos = obtener()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(datos), encoding="utf-8")
    return datos


def _get(liga, recurso, params):
    r = requests.get(ESPN_URL.format(liga=liga, recurso=recurso), params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def partidos_terminados(liga, inicio, fin):
    """ids de ESPN de partidos terminados de la liga entre dos fechas (ESPN acepta un mes por consulta)."""
    ids, mes = [], inicio.replace(day=1)
    while mes <= fin:
        clave = f"{mes:%Y%m}"
        datos = _cache_json(f"sb_{liga}_{clave}", lambda: _get(liga, "scoreboard", {"dates": clave, "limit": 500}))
        for e in datos.get("events", []):
            dia = datetime.fromisoformat(e["date"].replace("Z", "+00:00")).date()
            if inicio <= dia <= fin and e["status"]["type"].get("state") == "post":
                ids.append(e["id"])
        mes = (mes + timedelta(days=32)).replace(day=1)
    return ids


def juntar_tiros(desde_dias=150, hasta_dias=22):
    hoy = datetime.now(timezone.utc).date()
    inicio, fin = hoy - timedelta(days=desde_dias), hoy - timedelta(days=hasta_dias)
    tiros, partidos = [], 0
    for liga in LIGAS_ESPN:
        try:
            ids = partidos_terminados(liga, inicio, fin)
        except requests.RequestException:
            logger.exception("Sin calendario de %s", liga)
            continue
        logger.info("%s: %d partidos terminados", liga, len(ids))
        for espn_id in ids:
            try:
                resumen = _cache_json(f"summary_{liga}_{espn_id}",
                                      lambda: _get(liga, "summary", {"event": espn_id}))
            except requests.RequestException:
                continue
            nuevos = [t for t in map(tiro_de_jugada, resumen.get("commentary") or []) if t]
            if nuevos:
                partidos += 1
                tiros += [dict(t, liga=liga) for t in nuevos]
    logger.info("Tiros con posicion: %d de %d partidos", len(tiros), partidos)
    return tiros, partidos


def entrenar(desde_dias=150, hasta_dias=22):
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

    tiros, partidos = juntar_tiros(desde_dias, hasta_dias)
    sin_penal = [t for t in tiros if not t["penal"]]
    penales = [t for t in tiros if t["penal"]]
    X = np.array([variables(t["x"], t["y"], t["cabezazo"]) for t in sin_penal])
    y = np.array([t["gol"] for t in sin_penal], dtype=int)

    # validacion: 80% / 20% al azar (semilla fija), luego modelo final con todo
    rng = np.random.default_rng(42)
    orden = rng.permutation(len(y))
    corte = int(len(y) * 0.8)
    ent, pru = orden[:corte], orden[corte:]
    prueba = LogisticRegression(max_iter=1000).fit(X[ent], y[ent])
    p = prueba.predict_proba(X[pru])[:, 1]

    from src.live.xg_vivo import xg_tiro_zona  # xG por zona (version anterior), para comparar
    zona = np.clip([xg_tiro_zona(sin_penal[i]["texto"]) for i in pru], 0.01, 0.99)

    final = LogisticRegression(max_iter=1000).fit(X, y)
    modelo = {
        "variables": list(VARIABLES),
        "intercepto": float(final.intercept_[0]),
        "coeficientes": [float(c) for c in final.coef_[0]],
        "xg_penal": float(np.mean([t["gol"] for t in penales])) if len(penales) >= 30 else 0.76,
        "entrenamiento": {
            "desde_dias": desde_dias, "hasta_dias": hasta_dias, "partidos": partidos,
            "tiros": len(sin_penal), "goles": int(y.sum()), "penales": len(penales),
        },
        "validacion": {
            "tiros_prueba": len(pru),
            "log_loss": float(log_loss(y[pru], p)),
            "brier": float(brier_score_loss(y[pru], p)),
            "auc": float(roc_auc_score(y[pru], p)),
            "log_loss_base": float(log_loss(y[pru], np.full(len(pru), y[ent].mean()))),
            "log_loss_xg_por_zona": float(log_loss(y[pru], zona)),
            "auc_xg_por_zona": float(roc_auc_score(y[pru], zona)),
        },
    }
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    MODELO_PATH.write_text(json.dumps(modelo, indent=2), encoding="utf-8")
    global _MODELO
    _MODELO = modelo
    logger.info("Modelo de xG guardado en %s", MODELO_PATH)
    return modelo


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Entrena el modelo de xG con tiros de ESPN")
    parser.add_argument("--desde", type=int, default=150, help="dias hacia atras donde empieza (default 150)")
    parser.add_argument("--hasta", type=int, default=22, help="dias hacia atras donde termina (default 22)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    print(json.dumps(entrenar(args.desde, args.hasta), indent=2))
