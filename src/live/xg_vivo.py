"""
xG en vivo a partir del comentario jugada por jugada de ESPN.

ESPN no publica xG, pero para cada tiro del comentario da la posicion en la
cancha y si fue cabezazo o penal. Con eso se calcula el xG del tiro con el
modelo propio de src/models/xg_modelo.py (regresion logistica sobre
distancia y angulo, entrenada con miles de tiros de ESPN). Si un tiro no
trae posicion, se usa la tabla por zona del texto (XG_POR_ZONA).

Momentum: presion reciente de cada equipo, como la grafica de momentum de
las apps de futbol. Cada tiro suma su xG sin penales y cada corner
PESO_CORNER; el peso se desvanece con TAU_MOMENTUM minutos
(exp(-minutos_transcurridos / TAU)). momentum = presion local - presion visita.

Para que sirve: si un equipo acumula ocasiones claras sin anotar, el
modelo sube su ritmo de goles esperado antes de que llegue el gol (ver
src/live/modelo.py: ritmo_en_vivo), y la plataforma puede adelantarse al
mercado en lugar de solo reaccionar al marcador.

Fuente: https://site.api.espn.com/apis/site/v2/sports/soccer/{liga}/summary?event={id}
(publica, sin llave).
"""

import math
import re
from datetime import datetime

import requests

ESPN_SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{liga}/summary"

TIPOS_TIRO = {
    "goal", "shot-on-target", "shot-off-target", "shot-blocked", "shot-hit-woodwork",
    "penalty---scored", "penalty---saved", "penalty---missed",
}

# (patron en el texto, xG con el pie, xG de cabeza). Se usa el primero que
# coincide, del mas especifico al mas general. Valores de referencia de
# modelos publicos de xG por zona de tiro (documentados en docs/decisions.md).
XG_POR_ZONA = [
    (r"penalty", 0.76, 0.76),
    (r"very close range", 0.45, 0.30),
    (r"six yard box", 0.30, 0.20),
    (r"centre of the box", 0.12, 0.08),
    (r"(left|right) side of the box", 0.06, 0.05),
    (r"difficult angle", 0.04, 0.03),
    (r"outside the box|long range|more than \d+ yards", 0.03, 0.02),
]
XG_SIN_ZONA = 0.06  # tiro sin ubicacion en el texto
TAU_MOMENTUM = 8.0  # minutos; vida media ~5.5 min
PESO_CORNER = 0.03


def xg_tiro_zona(texto: str) -> float:
    """xG aproximado de un tiro a partir de la zona que describe el comentario de ESPN."""
    t = (texto or "").lower()
    cabeza = "header" in t
    for patron, pie, cabezazo in XG_POR_ZONA:
        if re.search(patron, t):
            return cabezazo if cabeza else pie
    return XG_SIN_ZONA


def xg_tiro(texto: str, x=None, y=None) -> float:
    """xG del tiro: modelo por posicion si hay coordenadas y modelo entrenado; si no, por zona."""
    if x is not None and y is not None:
        from src.models.xg_modelo import xg_posicion

        t = (texto or "").lower()
        valor = xg_posicion(x, y, cabezazo="header" in t, penal="penalty" in t)
        if valor is not None:
            return valor
    return xg_tiro_zona(texto)


def _instante(jugada):
    texto = jugada.get("wallclock")
    return datetime.fromisoformat(texto.replace("Z", "+00:00")) if texto else None


def parse_summary(summary: dict) -> dict:
    """Tiros (con xG) y rojas de un partido a partir del JSON de summary de ESPN."""
    competidores = summary["header"]["competitions"][0]["competitors"]
    lado_por_nombre = {c["team"]["displayName"]: c["homeAway"] for c in competidores}
    lado_por_id = {str(c["team"]["id"]): c["homeAway"] for c in competidores}

    tiros, corners = [], []
    for entrada in summary.get("commentary") or []:
        jugada = entrada.get("play") or {}
        tipo = (jugada.get("type") or {}).get("type", "")
        if tipo == "corner-awarded":
            lado = lado_por_nombre.get((jugada.get("team") or {}).get("displayName"))
            if lado:
                corners.append({"lado": lado, "minuto": int((jugada.get("clock") or {}).get("value", 0) // 60),
                                "instante": _instante(jugada)})
            continue
        if tipo not in TIPOS_TIRO and not tipo.startswith("goal"):
            continue
        texto = entrada.get("text") or jugada.get("text") or ""
        if "own goal" in texto.lower():
            continue  # un autogol no es una ocasion del equipo que anota
        lado = lado_por_nombre.get((jugada.get("team") or {}).get("displayName"))
        if lado is None:
            continue
        tiros.append({
            "lado": "home" if lado == "home" else "away",
            "minuto": int((jugada.get("clock") or {}).get("value", 0) // 60),
            "instante": _instante(jugada),
            "xg": xg_tiro(texto, jugada.get("fieldPositionX"), jugada.get("fieldPositionY")),
            "x": jugada.get("fieldPositionX"),
            "y": jugada.get("fieldPositionY"),
            "penal": "penalty" in texto.lower() or "penalty" in tipo,
            "gol": tipo.startswith("goal") or tipo == "penalty---scored",
            "a_puerta": tipo in ("shot-on-target", "goal", "penalty---scored", "penalty---saved")
            or tipo.startswith("goal"),
            "texto": texto,
        })

    rojas = []
    for evento in summary.get("keyEvents") or []:
        if (evento.get("type") or {}).get("type") != "red-card":
            continue
        lado = lado_por_id.get(str((evento.get("team") or {}).get("id")))
        if lado:
            rojas.append({
                "lado": lado,
                "minuto": int((evento.get("clock") or {}).get("value", 0) // 60),
                "instante": _instante(evento),
            })
    estadisticas = {}
    for equipo in (summary.get("boxscore") or {}).get("teams", []):
        lado = lado_por_id.get(str((equipo.get("team") or {}).get("id")))
        valores = {x.get("name"): x.get("displayValue") for x in equipo.get("statistics", [])}
        if lado:
            estadisticas[lado] = {
                "posesion": _numero(valores.get("possessionPct")),
                "tiros": _numero(valores.get("totalShots")),
                "a_puerta": _numero(valores.get("shotsOnTarget")),
                "corners": _numero(valores.get("wonCorners")),
            }
    return {"tiros": tiros, "corners": corners, "rojas": rojas, "estadisticas": estadisticas}


def _numero(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def presion(datos: dict, lado: str, referencia: float, antes=lambda e: True) -> float:
    """Presion reciente del equipo en el minuto `referencia` (xG sin penales + corners, con decaimiento)."""
    total = 0.0
    for t in datos["tiros"]:
        if t["lado"] == lado and not t["penal"] and t["minuto"] <= referencia and antes(t):
            total += t["xg"] * math.exp(-(referencia - t["minuto"]) / TAU_MOMENTUM)
    for c in datos.get("corners", []):
        if c["lado"] == lado and c["minuto"] <= referencia and antes(c):
            total += PESO_CORNER * math.exp(-(referencia - c["minuto"]) / TAU_MOMENTUM)
    return total


def momentum_serie(datos: dict, hasta_minuto: int):
    """[(minuto, presion local - presion visita)] para graficar el momentum del partido."""
    return [(m, presion(datos, "home", m) - presion(datos, "away", m)) for m in range(0, int(hasta_minuto) + 1)]


def resumen_hasta(datos: dict, minuto: int = None, instante: datetime = None, referencia: float = None) -> dict:
    """xG, tiros, tiros a puerta, corners, rojas y presion por equipo hasta un minuto (o instante).

    referencia: minuto de juego en el que se mide la presion (momentum); por default el
    minuto pedido o el del ultimo evento registrado.
    """
    def antes(e):
        if instante is not None and e["instante"] is not None:
            return e["instante"] <= instante
        return minuto is None or e["minuto"] <= minuto

    vistos = [e for e in datos["tiros"] + datos.get("corners", []) if antes(e)]
    if referencia is None:
        referencia = minuto if minuto is not None else max((e["minuto"] for e in vistos), default=0)
    salida = {}
    for lado in ("home", "away"):
        tiros = [t for t in datos["tiros"] if t["lado"] == lado and antes(t)]
        salida[lado] = {
            "xg": round(sum(t["xg"] for t in tiros), 2),
            # sin penales: es lo que predice goles futuros (un penal no dice cuantas ocasiones vendran)
            "npxg": round(sum(t["xg"] for t in tiros if not t["penal"]), 2),
            "tiros": len(tiros),
            "a_puerta": sum(1 for t in tiros if t["a_puerta"]),
            "corners": sum(1 for c in datos.get("corners", []) if c["lado"] == lado and antes(c)),
            "rojas": sum(1 for r in datos["rojas"] if r["lado"] == lado and antes(r)),
            "presion": round(presion(datos, lado, referencia, antes), 3),
        }
    return salida


def fetch_summary(liga_espn: str, espn_id: str) -> dict:
    r = requests.get(ESPN_SUMMARY_URL.format(liga=liga_espn), params={"event": espn_id}, timeout=15)
    r.raise_for_status()
    return r.json()


def xg_en_vivo(liga_espn: str, espn_id: str, minuto: float = None) -> dict:
    """Resumen actual de un partido en juego: xG, tiros, rojas, presion, momentum y estadisticas de ESPN."""
    datos = parse_summary(fetch_summary(liga_espn, espn_id))
    hasta = minuto if minuto is not None else max((t["minuto"] for t in datos["tiros"]), default=0)
    resumen = resumen_hasta(datos, referencia=hasta)
    resumen["momentum"] = momentum_serie(datos, hasta)
    resumen["estadisticas"] = datos["estadisticas"]
    return resumen
