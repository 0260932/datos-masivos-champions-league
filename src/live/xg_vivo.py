"""
xG en vivo a partir del comentario jugada por jugada de ESPN.

ESPN no publica xG, pero su comentario describe cada tiro: desde donde se
patea ("from the centre of the box", "outside the box", "six yard box"),
si es cabezazo y si es penal. Con eso se asigna a cada tiro la
probabilidad media de gol de los tiros parecidos (tabla XG_POR_ZONA) y se
suma por equipo. Es un xG aproximado: no conoce la posicion exacta, la
presion del defensa ni el tipo de pase.

Para que sirve: si un equipo acumula ocasiones claras sin anotar, el
modelo sube su ritmo de goles esperado antes de que llegue el gol (ver
src/live/modelo.py: ritmo_en_vivo), y la plataforma puede adelantarse al
mercado en lugar de solo reaccionar al marcador.

Fuente: https://site.api.espn.com/apis/site/v2/sports/soccer/{liga}/summary?event={id}
(publica, sin llave).
"""

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


def xg_tiro(texto: str) -> float:
    """xG aproximado de un tiro a partir de su descripcion en el comentario de ESPN."""
    t = (texto or "").lower()
    cabeza = "header" in t
    for patron, pie, cabezazo in XG_POR_ZONA:
        if re.search(patron, t):
            return cabezazo if cabeza else pie
    return XG_SIN_ZONA


def _instante(jugada):
    texto = jugada.get("wallclock")
    return datetime.fromisoformat(texto.replace("Z", "+00:00")) if texto else None


def parse_summary(summary: dict) -> dict:
    """Tiros (con xG) y rojas de un partido a partir del JSON de summary de ESPN."""
    competidores = summary["header"]["competitions"][0]["competitors"]
    lado_por_nombre = {c["team"]["displayName"]: c["homeAway"] for c in competidores}
    lado_por_id = {str(c["team"]["id"]): c["homeAway"] for c in competidores}

    tiros = []
    for entrada in summary.get("commentary") or []:
        jugada = entrada.get("play") or {}
        tipo = (jugada.get("type") or {}).get("type", "")
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
            "xg": xg_tiro(texto),
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
    return {"tiros": tiros, "rojas": rojas}


def fetch_summary(liga_espn: str, espn_id: str) -> dict:
    r = requests.get(ESPN_SUMMARY_URL.format(liga=liga_espn), params={"event": espn_id}, timeout=15)
    r.raise_for_status()
    return r.json()


def resumen_hasta(datos: dict, minuto: int = None, instante: datetime = None) -> dict:
    """xG, tiros, tiros a puerta y rojas por equipo hasta un minuto (o instante) del partido."""
    def antes(e):
        if instante is not None and e["instante"] is not None:
            return e["instante"] <= instante
        return minuto is None or e["minuto"] <= minuto

    salida = {}
    for lado in ("home", "away"):
        tiros = [t for t in datos["tiros"] if t["lado"] == lado and antes(t)]
        salida[lado] = {
            "xg": round(sum(t["xg"] for t in tiros), 2),
            # sin penales: es lo que predice goles futuros (un penal no dice cuantas ocasiones vendran)
            "npxg": round(sum(t["xg"] for t in tiros if not t["penal"]), 2),
            "tiros": len(tiros),
            "a_puerta": sum(1 for t in tiros if t["a_puerta"]),
            "rojas": sum(1 for r in datos["rojas"] if r["lado"] == lado and antes(r)),
        }
    return salida


def xg_en_vivo(liga_espn: str, espn_id: str) -> dict:
    """Resumen actual (xG, tiros, a puerta, rojas) de un partido en juego."""
    return resumen_hasta(parse_summary(fetch_summary(liga_espn, espn_id)))
