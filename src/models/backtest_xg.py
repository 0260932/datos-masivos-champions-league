"""
Prueba historica: ¿el modelo con xG en vivo se adelanta al mercado de Polymarket?

Para partidos ya jugados de las ligas de la plataforma:
1. Polymarket: eventos cerrados con mercado moneyline y su historial de
   precios minuto a minuto (CLOB prices-history).
2. ESPN: el mismo partido (fecha y equipos) con su comentario jugada por
   jugada: tiros con xG (src/live/xg_vivo.py), goles y rojas, cada uno con
   su hora exacta (wallclock).
3. Minuto a minuto de juego se reconstruye lo que la plataforma habria visto
   en vivo: marcador, xG sin penales, rojas, precio de cada resultado y la
   senal de src/live/tracker.evaluate (con el ruido GARCH calculado solo con
   precios anteriores a ese minuto). Para no darle ventaja injusta al
   modelo, los datos de ESPN se usan con RETRASO_DATOS de atraso (lo que
   tarda en publicarse una jugada) y las compras se hacen al precio de
   EJECUCION despues de la senal.

Calibracion del xG y el momentum (fuera de muestra): los pesos del ritmo previo
(W) y del momentum (beta) se eligen con la mitad mas antigua de los partidos
(Brier del resultado en los minutos 15-75) y todas las metricas de abajo se
reportan tambien para la mitad mas reciente, que no se uso para elegirlos.

Metricas (todas fuera de muestra: en cada minuto solo se usa lo anterior):
a) Adelanto: correlacion entre (modelo - mercado) en t y el cambio del
   precio de t a t+5 / t+10 min; tasa de acierto de la direccion cuando la
   diferencia es de 3 pts o mas. Con y sin xG.
b) Calibracion: Brier del resultado final en los minutos 15, 30, 45, 60 y
   75 para el modelo con xG, sin xG y el precio del mercado.
c) Senales BUENO simuladas: cada vez que un resultado pasa a BUENO se compra
   $1 al precio de ese minuto + 1 pt (ask aproximado) y se liquida con el
   resultado real. Con y sin xG.
d) GARCH vs varianza constante: perdida QLIKE de la varianza pronosticada a
   un paso (5 min) en la ultima parte de cada serie de precios.

Descargas crudas: data/raw/backtest/ (cache, no se sube a git).
Resultados: data/live/modelo/backtest_xg.json (los muestra el dashboard).

Ejecucion:
    python -m src.models.backtest_xg               # ultimos 21 dias, hasta 80 partidos
    python -m src.models.backtest_xg --dias 35 --max-partidos 150
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

from src.live import modelo as modelo_vivo
from src.live.modelo import SNAPSHOT_DIR, fit_lambdas, live_probabilities, ritmo_en_vivo
from src.live.tracker import (
    COMPETICIONES,
    GAMMA_EVENTS_URL,
    _parecido_equipo,
    evaluate,
    fetch_espn_scores,
    price_history,
)
from src.live.xg_vivo import fetch_summary, parse_summary, resumen_hasta
from src.models.garch_mercado import _logit, malla_regular, volatilidad
from src.processing.polymarket import _parse_lista

ROOT_DIR = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT_DIR / "data" / "raw" / "backtest"
RESULTADOS_PATH = SNAPSHOT_DIR / "backtest_xg.json"

LADOS = ("home", "draw", "away")
HORIZONTES_MIN = (5, 10)
DIFERENCIA_MIN = 0.03
CORTES_BRIER = (15, 30, 45, 60, 75)
GRILLA_PESO_PREVIO = (30, 60, 120, 240, 480, 960)
GRILLA_MOMENTUM = (0.0, 0.5, 1.0, 2.0)
SPREAD = 0.01  # ask aproximado = precio + 1 pt
RETRASO_DATOS = timedelta(seconds=60)  # ESPN publica la jugada con atraso
EJECUCION = timedelta(minutes=1)  # tiempo entre la senal y la compra
PISO_VARIANZA = 0.005 ** 2  # varianza minima (logit, 5 min) para comparar GARCH y constante

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Descarga (con cache en disco)
# ---------------------------------------------------------------------------

def _cache_json(nombre, obtener):
    path = CACHE_DIR / f"{nombre}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    datos = obtener()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(datos, default=str), encoding="utf-8")
    return datos


def eventos_cerrados(competicion, desde):
    """Eventos de Polymarket cerrados con moneyline de la competicion, con inicio >= desde."""
    tag = COMPETICIONES[competicion][1]
    salida, offset = [], 0
    while offset < 1000:
        r = requests.get(
            GAMMA_EVENTS_URL,
            params={"tag_slug": tag, "closed": "true", "limit": 100, "offset": offset,
                    "order": "startTime", "ascending": "false"},
            timeout=30,
        )
        r.raise_for_status()
        pagina = r.json()
        for e in pagina:
            inicio = e.get("startTime")
            if not inicio or " vs. " not in (e.get("title") or ""):
                continue
            inicio = datetime.fromisoformat(inicio.replace("Z", "+00:00"))
            mercados = [m for m in e.get("markets", []) if m.get("sportsMarketType") == "moneyline"]
            if inicio >= desde and len(mercados) == 3:
                salida.append({"evento": e, "inicio": inicio, "mercados": mercados})
        viejos = [e for e in pagina if e.get("startTime") and
                  datetime.fromisoformat(e["startTime"].replace("Z", "+00:00")) < desde]
        if len(pagina) < 100 or len(viejos) == len(pagina):
            break
        offset += 100
    return salida


def _lado_mercado(mercado, home, away):
    titulo = mercado.get("groupItemTitle") or ""
    if titulo.lower().startswith("draw"):
        return "draw"
    return "home" if _parecido_equipo(titulo, [home]) >= _parecido_equipo(titulo, [away]) else "away"


def preparar_partido(competicion, item):
    """Junta Polymarket y ESPN de un partido cerrado; None si falta algo."""
    e, inicio = item["evento"], item["inicio"]
    home, away = e["title"].split(" vs. ")
    liga_espn = COMPETICIONES[competicion][2]
    fechas = sorted({inicio.date(), (inicio - timedelta(hours=8)).date()})
    eventos_espn = _cache_json(
        f"espn_{liga_espn}_{'_'.join(f.isoformat() for f in fechas)}",
        lambda: [dict(x, utc_date=x["utc_date"].isoformat()) for x in fetch_espn_scores(competicion, fechas)],
    )
    mejor, mejor_score = None, 0.0
    for x in eventos_espn:
        if abs((datetime.fromisoformat(x["utc_date"]) - inicio).total_seconds()) > 30 * 60:
            continue
        sh, sa = _parecido_equipo(home, x["home_names"]), _parecido_equipo(away, x["away_names"])
        if min(sh, sa) >= 0.6 and sh + sa > mejor_score:
            mejor, mejor_score = x, sh + sa
    if mejor is None or mejor["state"] != "post":
        return None

    summary = _cache_json(f"summary_{liga_espn}_{mejor['espn_id']}", lambda: fetch_summary(liga_espn, mejor["espn_id"]))
    datos = parse_summary(summary)
    if not datos["tiros"] or any(t["instante"] is None for t in datos["tiros"]):
        return None
    reloj = {}
    for ev in summary.get("keyEvents") or []:
        tipo, wc = (ev.get("type") or {}).get("type"), ev.get("wallclock")
        if wc and tipo in ("kickoff", "halftime", "start-2nd-half", "end-regular-time") and tipo not in reloj:
            reloj[tipo] = datetime.fromisoformat(wc.replace("Z", "+00:00"))
    if not {"kickoff", "halftime", "start-2nd-half", "end-regular-time"} <= set(reloj):
        return None

    final = {c["homeAway"]: int(c.get("score") or 0) for c in summary["header"]["competitions"][0]["competitors"]}
    resultado = "home" if final["home"] > final["away"] else ("away" if final["away"] > final["home"] else "draw")

    precios = {}
    for m in item["mercados"]:
        token = (_parse_lista(m.get("clobTokenIds")) or [None])[0]
        if not token:
            return None
        lado = _lado_mercado(m, home, away)
        serie = _cache_json(
            f"precios_{token[:16]}",
            lambda: [(t.isoformat(), p) for t, p in price_history(
                token, (reloj["kickoff"] - timedelta(hours=12)).timestamp(), 1)],
        )
        precios[lado] = [(datetime.fromisoformat(t), p) for t, p in serie]
    if set(precios) != set(LADOS) or any(len(v) < 30 for v in precios.values()):
        return None
    return {
        "competicion": competicion, "home": home, "away": away, "inicio": inicio,
        "datos": datos, "reloj": reloj, "resultado": resultado, "final": final, "precios": precios,
    }


# ---------------------------------------------------------------------------
# Reconstruccion minuto a minuto
# ---------------------------------------------------------------------------

def _precio_en(serie, t):
    """Ultimo precio con hora <= t (None si no hay)."""
    previo = None
    for hora, p in serie:
        if hora > t:
            break
        previo = p
    return previo


def _minuto_juego(reloj, t):
    if t < reloj["halftime"]:
        return max(0, (t - reloj["kickoff"]).total_seconds() / 60)
    if t < reloj["start-2nd-half"]:
        return 45
    return min(95, 45 + (t - reloj["start-2nd-half"]).total_seconds() / 60)


def _instantes(reloj):
    """Un instante por minuto de juego, de 1' al final del tiempo reglamentario."""
    t, salida = reloj["kickoff"] + timedelta(minutes=1), []
    while t < reloj["end-regular-time"]:
        if not (reloj["halftime"] <= t < reloj["start-2nd-half"]):
            salida.append(t)
        t += timedelta(minutes=1)
    return salida


def brier_partidos(partidos, peso_previo=None, peso_momentum=0.0):
    """Brier medio del resultado final en CORTES_BRIER; peso_previo None = modelo sin xG."""
    total, n = 0.0, 0
    for p in partidos:
        reloj = p["reloj"]
        previa = [_precio_en(p["precios"][l], reloj["kickoff"]) for l in LADOS]
        if None in previa:
            continue
        lambdas = fit_lambdas(*previa)
        goles = [t for t in p["datos"]["tiros"] if t["gol"]]
        for t in _instantes(reloj):
            minuto = _minuto_juego(reloj, t)
            if round(minuto) not in CORTES_BRIER or abs(minuto - round(minuto)) > 0.5:
                continue
            visto = t - RETRASO_DATOS
            gl = sum(1 for g in goles if g["lado"] == "home" and g["instante"] <= visto)
            gv = sum(1 for g in goles if g["lado"] == "away" and g["instante"] <= visto)
            if peso_previo is None:
                lam = lambdas
            else:
                xg = resumen_hasta(p["datos"], instante=visto, referencia=minuto)
                lam = ritmo_en_vivo(*lambdas, minuto, xg, peso_previo=peso_previo, peso_momentum=peso_momentum)
            probs = live_probabilities(*lam, minuto, gl, gv)
            total += sum((probs[i] - (p["resultado"] == l)) ** 2 for i, l in enumerate(LADOS))
            n += 1
    return total / n if n else None


def calibrar(partidos):
    """Elige W y beta con la mitad mas antigua; reporta la mitad mas reciente."""
    ordenados = sorted(partidos, key=lambda p: p["inicio"])
    mitad = len(ordenados) // 2
    ajuste, prueba = ordenados[:mitad], ordenados[mitad:]
    grilla = []
    for w in GRILLA_PESO_PREVIO:
        for b in GRILLA_MOMENTUM:
            grilla.append({"peso_previo": w, "peso_momentum": b, "brier_ajuste": brier_partidos(ajuste, w, b)})
    mejor = min(grilla, key=lambda g: g["brier_ajuste"])
    sin_xg_ajuste = brier_partidos(ajuste)
    return {
        "partidos_ajuste": len(ajuste),
        "partidos_prueba": len(prueba),
        "corte": ordenados[mitad]["inicio"].isoformat() if prueba else None,
        "grilla": grilla,
        "elegido": {"peso_previo": mejor["peso_previo"], "peso_momentum": mejor["peso_momentum"]},
        "brier_ajuste": {"con_xg_momentum": mejor["brier_ajuste"], "sin_xg": sin_xg_ajuste},
        "brier_prueba": {
            "con_xg_momentum": brier_partidos(prueba, mejor["peso_previo"], mejor["peso_momentum"]),
            "sin_xg": brier_partidos(prueba),
        },
        "mejora_en_prueba": None,
    }


def reconstruir(partido):
    """Filas (una por minuto y resultado) con modelo con/sin xG, precio y precio futuro.

    El modelo "con xG" usa los pesos vigentes en src/live/modelo.py
    (PESO_PREVIO_MIN y PESO_MOMENTUM); correr() los fija con calibrar().
    """
    reloj, datos, precios = partido["reloj"], partido["datos"], partido["precios"]
    previa = [_precio_en(precios[l], reloj["kickoff"]) for l in LADOS]
    if None in previa:
        return []
    lambdas = fit_lambdas(*previa)
    goles = [t for t in datos["tiros"] if t["gol"]]
    filas, vol, ultima_vol = [], {}, None
    for t in _instantes(reloj):
        minuto = _minuto_juego(reloj, t)
        visto = t - RETRASO_DATOS  # lo que ESPN ya habia publicado en t
        gl = sum(1 for g in goles if g["lado"] == "home" and g["instante"] <= visto)
        gv = sum(1 for g in goles if g["lado"] == "away" and g["instante"] <= visto)
        xg = resumen_hasta(datos, instante=visto, referencia=minuto)
        mercado = {l: _precio_en(precios[l], t) for l in LADOS}
        if None in mercado.values():
            continue
        if ultima_vol is None or (t - ultima_vol).total_seconds() >= 300:  # GARCH cada 5 min, solo pasado
            vol = {l: volatilidad(malla_regular([x for x in precios[l] if x[0] <= t])) for l in LADOS}
            ultima_vol = t
        partido_t = {"home": partido["home"], "away": partido["away"], "status": "IN_PLAY",
                     "minute": int(minuto), "home_goals": gl, "away_goals": gv}
        ofertas = {l: {"ask": min(0.99, mercado[l] + SPREAD)} for l in LADOS}
        con_xg = evaluate(partido_t, lambdas, ofertas, xg, vol, usar_xg=True)
        sin_xg = evaluate(partido_t, lambdas, ofertas, None, vol, usar_xg=False)
        futuros = {h: {l: _precio_en(precios[l], t + timedelta(minutes=h)) for l in LADOS} for h in HORIZONTES_MIN}
        compra = {l: _precio_en(precios[l], t + EJECUCION) for l in LADOS}
        for i, l in enumerate(LADOS):
            filas.append({
                "t": t, "minuto": minuto, "lado": l, "mercado": mercado[l],
                "p_xg": con_xg[i]["p_modelo"], "p_sin": sin_xg[i]["p_modelo"],
                "senal_xg": con_xg[i]["senal"], "senal_sin": sin_xg[i]["senal"],
                "ask": ofertas[l]["ask"], "gano": partido["resultado"] == l,
                "compra": None if compra[l] is None else min(0.99, compra[l] + SPREAD),
                **{f"futuro_{h}": futuros[h][l] for h in HORIZONTES_MIN},
            })
    return filas


# ---------------------------------------------------------------------------
# Metricas
# ---------------------------------------------------------------------------

def metricas_adelanto(filas, columna):
    salida = {}
    for h in HORIZONTES_MIN:
        pares = [(f[columna] - f["mercado"], f[f"futuro_{h}"] - f["mercado"])
                 for f in filas if f[f"futuro_{h}"] is not None]
        if len(pares) < 10:
            continue
        d, cambio = np.array(pares).T
        fuertes = [(a, b) for a, b in pares if abs(a) >= DIFERENCIA_MIN and b != 0]
        salida[f"{h}min"] = {
            "correlacion": float(np.corrcoef(d, cambio)[0, 1]) if d.std() > 0 and cambio.std() > 0 else None,
            "acierto_direccion": float(np.mean([np.sign(a) == np.sign(b) for a, b in fuertes])) if fuertes else None,
            "casos_direccion": len(fuertes),
        }
    return salida


def metricas_brier(por_partido):
    salida = {}
    for corte in CORTES_BRIER:
        sumas, n = {"modelo_xg": 0.0, "modelo_sin_xg": 0.0, "mercado": 0.0}, 0
        for filas in por_partido:
            en_corte = [f for f in filas if abs(f["minuto"] - corte) < 1]
            if len(en_corte) < 3:
                continue
            en_corte = en_corte[:3]
            total_mercado = sum(f["mercado"] for f in en_corte) or 1
            for f in en_corte:
                real = 1.0 if f["gano"] else 0.0
                sumas["modelo_xg"] += (f["p_xg"] - real) ** 2
                sumas["modelo_sin_xg"] += (f["p_sin"] - real) ** 2
                sumas["mercado"] += (f["mercado"] / total_mercado - real) ** 2
            n += 1
        if n:
            salida[f"min_{corte}"] = {k: v / n for k, v in sumas.items()} | {"partidos": n}
    return salida


def metricas_senales(filas_por_partido, columna):
    apuestas = []
    for filas in filas_por_partido:
        previo = {}
        for f in filas:
            if f[columna] == "BUENO" and previo.get(f["lado"]) != "BUENO" and f["compra"]:
                apuestas.append((1 / f["compra"] - 1) if f["gano"] else -1.0)
            previo[f["lado"]] = f[columna]
    if not apuestas:
        return {"senales": 0}
    return {
        "senales": len(apuestas),
        "aciertos": float(np.mean([a > 0 for a in apuestas])),
        "ganancia_por_dolar": float(np.mean(apuestas)),
        "ganancia_total": float(np.sum(apuestas)),
    }


def metricas_garch(partidos, fraccion_prueba=0.3):
    """QLIKE fuera de muestra (5 min): GARCH(1,1) vs varianza constante del entrenamiento."""
    from arch import arch_model

    qlike = {"garch": [], "constante": []}
    for partido in partidos:
        for lado in LADOS:
            r = np.diff([_logit(p) for p in malla_regular(partido["precios"][lado])])
            r = r[np.abs(r) <= 1.0]
            corte = int(len(r) * (1 - fraccion_prueba))
            if corte < 100 or len(r) - corte < 10 or np.allclose(r[:corte], 0):
                continue
            try:
                ajuste = arch_model(r * 100, mean="Zero", vol="GARCH", p=1, q=1, dist="t", rescale=False).fit(
                    last_obs=corte, disp="off", show_warning=False)
                var_garch = ajuste.forecast(start=corte, horizon=1, reindex=False).variance.values[:-1, 0] / 1e4
            except Exception:
                continue
            reales = r[corte + 1: corte + 1 + len(var_garch)]
            var_garch = var_garch[: len(reales)]
            var_cte = np.full(len(reales), np.var(r[:corte]))
            for nombre, var in (("garch", var_garch), ("constante", var_cte)):
                var = np.maximum(var, PISO_VARIANZA)
                qlike[nombre].extend(list(np.log(var) + reales ** 2 / var))
    if not qlike["garch"]:
        return {}
    return {
        "qlike_garch": float(np.mean(qlike["garch"])),
        "qlike_constante": float(np.mean(qlike["constante"])),
        "observaciones": len(qlike["garch"]),
    }


# ---------------------------------------------------------------------------
# Corrida completa
# ---------------------------------------------------------------------------

def correr(dias=21, max_partidos=80):
    desde = datetime.now(timezone.utc) - timedelta(days=dias)
    partidos = []
    for competicion in COMPETICIONES:
        try:
            items = eventos_cerrados(competicion, desde)
        except requests.RequestException:
            logger.exception("No se pudieron leer eventos cerrados de %s", competicion)
            continue
        logger.info("%s: %d eventos cerrados con moneyline", competicion, len(items))
        for item in items:
            if len(partidos) >= max_partidos:
                break
            try:
                partido = preparar_partido(competicion, item)
            except (requests.RequestException, KeyError, ValueError):
                logger.exception("Fallo al preparar %s", item["evento"].get("title"))
                continue
            if partido:
                partidos.append(partido)
    logger.info("Partidos con ESPN y Polymarket completos: %d", len(partidos))

    calibracion = calibrar(partidos)
    calibracion["mejora_en_prueba"] = (
        calibracion["brier_prueba"]["sin_xg"] - calibracion["brier_prueba"]["con_xg_momentum"]
    )
    logger.info("Calibracion: %s", {k: v for k, v in calibracion.items() if k != "grilla"})
    modelo_vivo.PESO_PREVIO_MIN = calibracion["elegido"]["peso_previo"]
    modelo_vivo.PESO_MOMENTUM = calibracion["elegido"]["peso_momentum"]

    por_partido = []
    for partido in partidos:
        filas = reconstruir(partido)
        if filas:
            por_partido.append(filas)
    todas = [f for filas in por_partido for f in filas]

    resultados = {
        "generado": datetime.now(timezone.utc).isoformat(),
        "ventana_dias": dias,
        "partidos": len(por_partido),
        "por_liga": {c: sum(1 for p in partidos if p["competicion"] == c) for c in COMPETICIONES},
        "minutos_evaluados": len(todas) // 3,
        "calibracion": calibracion,
        "adelanto": {"con_xg": metricas_adelanto(todas, "p_xg"), "sin_xg": metricas_adelanto(todas, "p_sin")},
        "brier": metricas_brier(por_partido),
        "senales": {"con_xg": metricas_senales(por_partido, "senal_xg"),
                    "sin_xg": metricas_senales(por_partido, "senal_sin")},
        "garch": metricas_garch(partidos),
    }
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    RESULTADOS_PATH.write_text(json.dumps(resultados, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Resultados guardados en %s", RESULTADOS_PATH)
    return resultados, por_partido


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prueba historica del xG en vivo contra Polymarket")
    parser.add_argument("--dias", type=int, default=21)
    parser.add_argument("--max-partidos", type=int, default=80)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    resultados, _ = correr(args.dias, args.max_partidos)
    print(json.dumps(resultados, indent=2, ensure_ascii=False))
