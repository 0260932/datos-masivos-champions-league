"""
Tracker en vivo de moneyline (1X2) para partidos de Champions League.

Cada N segundos (30 por default):
1. Pide a football-data.org los partidos de Champions de hoy que esten
   en juego (marcador y minuto).
2. Calcula con src.live.modelo la probabilidad de local / empate /
   visita dado el marcador y el tiempo restante.
3. Busca el mercado moneyline del partido en Polymarket (API publica,
   sin credenciales) y compara contra el precio de compra (best ask).
4. Avisa si es buen momento para entrar: incluso el escenario bajo del
   modelo (ritmo de goles -20%) queda arriba del precio que cobra el
   mercado. La ventaja minima la fija el modelo, no el usuario.

Cada evaluacion se guarda en data/interim/live/senales_<fecha>.csv para
poder revisar despues que tan buenas fueron las senales.

Ejecucion:
    python -m src.live.tracker                  # loop cada 30 s
    python -m src.live.tracker --intervalo 60   # loop cada 60 s
    python -m src.live.tracker --una-vez        # una sola pasada
    python -m src.live.tracker --demo           # partido simulado (sin API)

Detener con Ctrl+C.
"""

import argparse
import csv
import json
import logging
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from src.ingestion.extract_api_football_data import API_BASE_URL, get_api_token
from src.models.xgboost_resultado import PrediccionResultado
from src.processing.polymarket import _parse_lista, _pedir_historial, normalizar_nombre
from src.live.modelo import (
    StrengthModel,
    fit_lambdas,
    kelly_fraction,
    live_probabilities,
    live_probability_range,
    name_similarity,
    to_american,
)

ROOT_DIR = Path(__file__).resolve().parents[2]
LIVE_DIR = ROOT_DIR / "data" / "interim" / "live"
LOGS_DIR = ROOT_DIR / "logs"
LIVE_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
_SESION = requests.Session()
# codigo de football-data.org -> (nombre, tag de Polymarket, liga en ESPN)
COMPETICIONES = {
    "CL": ("Champions League", "ucl", "uefa.champions"),
    "PD": ("La Liga", "la-liga", "esp.1"),
    "PL": ("Premier League", "premier-league", "eng.1"),
}
# Marcador en vivo: el plan gratuito de football-data.org no actualiza los
# partidos en juego a tiempo (se queda en TIMED), asi que el marcador y el
# minuto se toman del scoreboard publico de ESPN (sin llave).
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{liga}/scoreboard"
VENTANA_EN_JUEGO = timedelta(hours=2, minutes=30)  # despues del inicio, un partido puede seguir en juego

ESTADOS_EN_JUEGO = {"IN_PLAY", "PAUSED", "LIVE"}
SIN_DATOS = "SIN_DATOS"  # ya empezo pero ninguna fuente trae el marcador
ESTADOS_PROGRAMADOS = {"TIMED", "SCHEDULED"}
REFRESCO_EVENTOS_SEG = 600  # cada cuanto se recarga la lista de eventos de Polymarket

logger = logging.getLogger("src.live.tracker")


# ---------------------------------------------------------------------------
# football-data.org: partidos en vivo
# ---------------------------------------------------------------------------

def _parse_minute(valor):
    if valor is None:
        return None
    try:
        return int(str(valor).split("+")[0])
    except ValueError:
        return None


def estimate_minute(utc_date: datetime, status: str, ahora: datetime) -> int:
    """Minuto aproximado cuando la API no lo trae (plan gratuito)."""
    if status == "PAUSED":
        return 45
    transcurrido = (ahora - utc_date).total_seconds() / 60
    if transcurrido <= 47:
        return max(0, int(transcurrido))
    if transcurrido <= 62:  # medio tiempo
        return 45
    return min(95, int(transcurrido - 17))


def fetch_espn_scores(competicion: str, fechas):
    """Partidos del scoreboard de ESPN: hora, equipos, estado ('pre'/'in'/'post'), minuto y goles."""
    liga = COMPETICIONES[competicion][2]
    eventos = []
    for fecha in fechas:
        r = requests.get(
            ESPN_SCOREBOARD_URL.format(liga=liga), params={"dates": fecha.strftime("%Y%m%d")}, timeout=15
        )
        r.raise_for_status()
        for e in r.json().get("events", []):
            comp = e["competitions"][0]
            equipos = {c["homeAway"]: c for c in comp["competitors"]}
            estado = e["status"]["type"]
            eventos.append({
                "utc_date": datetime.fromisoformat(e["date"].replace("Z", "+00:00")),
                "home_names": [equipos["home"]["team"].get(k) for k in ("displayName", "shortDisplayName", "name")],
                "away_names": [equipos["away"]["team"].get(k) for k in ("displayName", "shortDisplayName", "name")],
                "state": estado.get("state"),
                "medio_tiempo": estado.get("name") == "STATUS_HALFTIME",
                "minute": _parse_minute(str(e["status"].get("displayClock", "")).replace("'", "")),
                "home_goals": int(equipos["home"].get("score") or 0),
                "away_goals": int(equipos["away"].get("score") or 0),
            })
    return eventos


def _parecido_equipo(nombre, candidatos):
    """Como name_similarity, pero acepta que un nombre este contenido en el otro
    ("Alaves" en "Deportivo Alaves"); es seguro porque antes se filtra por hora de inicio."""
    mejor = 0.0
    claves = set(normalizar_nombre(nombre).split())
    for c in filter(None, candidatos):
        mejor = max(mejor, name_similarity(nombre, c))
        otras = set(normalizar_nombre(c).split())
        if claves and otras and (claves <= otras or otras <= claves):
            mejor = max(mejor, 0.85)
    return mejor


def apply_espn(partidos, eventos):
    """Actualiza estado, minuto y marcador con ESPN (mismo inicio +-20 min y mismos equipos)."""
    for p in partidos:
        candidatos = [e for e in eventos if abs((e["utc_date"] - p["utc_date"]).total_seconds()) <= 20 * 60]
        mejor, mejor_score = None, 0.0
        for e in candidatos:
            sh, sa = _parecido_equipo(p["home"], e["home_names"]), _parecido_equipo(p["away"], e["away_names"])
            if min(sh, sa) >= 0.6 and sh + sa > mejor_score:
                mejor, mejor_score = e, sh + sa
        if mejor is None or mejor["state"] == "pre":
            continue
        p["home_goals"], p["away_goals"] = mejor["home_goals"], mejor["away_goals"]
        p["fuente_marcador"] = "ESPN"
        if mejor["state"] == "post":
            p["status"], p["minute"] = "FINISHED", 95
        else:
            p["status"] = "PAUSED" if mejor["medio_tiempo"] else "IN_PLAY"
            p["minute"] = 45 if mejor["medio_tiempo"] else (mejor["minute"] or estimate_minute(p["utc_date"], "IN_PLAY", datetime.now(timezone.utc)))
    return partidos


def fetch_live_matches(minutos_previa: int, competicion: str = "CL"):
    """Partidos de la competicion en juego (y los que empiezan en <= minutos_previa).

    Calendario, nombres y escudos: football-data.org. Marcador y minuto en
    vivo: ESPN. Un partido que ya empezo y del que ninguna fuente trae
    marcador queda como SIN_DATOS (no se supone 0-0).
    """
    ahora = datetime.now(timezone.utc)
    params = {
        "dateFrom": (ahora - timedelta(days=1)).date().isoformat(),
        "dateTo": (ahora + timedelta(days=1, minutes=minutos_previa)).date().isoformat(),
    }
    url = f"{API_BASE_URL}/competitions/{competicion}/matches"
    response = requests.get(
        url, headers={"X-Auth-Token": get_api_token()}, params=params, timeout=20
    )
    response.raise_for_status()

    partidos = []
    for m in response.json().get("matches", []):
        status = m.get("status")
        utc_date = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        ya_empezo = utc_date <= ahora <= utc_date + VENTANA_EN_JUEGO
        if status in ESTADOS_EN_JUEGO:
            minuto = _parse_minute(m.get("minute"))
            if minuto is None:
                minuto = estimate_minute(utc_date, status, ahora)
        elif status in ESTADOS_PROGRAMADOS and ya_empezo:
            status, minuto = SIN_DATOS, estimate_minute(utc_date, "IN_PLAY", ahora)
        elif status in ESTADOS_PROGRAMADOS and ahora <= utc_date <= ahora + timedelta(minutes=minutos_previa):
            minuto = 0
        else:
            continue

        score = m.get("score", {}).get("fullTime", {}) or {}
        partidos.append({
            "match_id": m.get("id"),
            "home": m.get("homeTeam", {}).get("name"),
            "away": m.get("awayTeam", {}).get("name"),
            "home_short": m.get("homeTeam", {}).get("shortName"),
            "away_short": m.get("awayTeam", {}).get("shortName"),
            "home_crest": m.get("homeTeam", {}).get("crest"),
            "away_crest": m.get("awayTeam", {}).get("crest"),
            "utc_date": utc_date,
            "status": status,
            "minute": minuto,
            "home_goals": score.get("home") or 0,
            "away_goals": score.get("away") or 0,
            "fuente_marcador": "football-data.org",
        })

    if any(p["status"] != "TIMED" or p["utc_date"] <= ahora for p in partidos):
        try:
            fechas = {(ahora - timedelta(hours=12)).date(), ahora.date()}
            apply_espn(partidos, fetch_espn_scores(competicion, sorted(fechas)))
        except (requests.RequestException, KeyError, ValueError):
            logger.exception("No se pudo leer el marcador en vivo de ESPN")
    return partidos


# ---------------------------------------------------------------------------
# Polymarket: mercados moneyline por partido
# ---------------------------------------------------------------------------

def _to_float(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


class PolymarketMoneyline:
    """Encuentra el evento de Polymarket de cada partido y lee sus precios."""

    def __init__(self, competicion="CL"):
        self.tag = COMPETICIONES[competicion][1]
        self.eventos = []
        self.cargado_en = 0.0
        self.slug_por_partido = {}

    def _load_events(self):
        eventos, offset = [], 0
        while True:
            r = requests.get(
                GAMMA_EVENTS_URL,
                params={"tag_slug": self.tag, "closed": "false", "limit": 100, "offset": offset},
                timeout=20,
            )
            r.raise_for_status()
            pagina = r.json()
            eventos.extend(pagina)
            if len(pagina) < 100:  # la API regresa maximo 100 por pagina
                break
            offset += 100
        # solo los eventos que traen mercado moneyline (el principal del partido)
        self.eventos = [
            e for e in eventos
            if any(m.get("sportsMarketType") == "moneyline" for m in e.get("markets", []))
        ]
        self.cargado_en = time.time()
        logger.info("Polymarket: %d eventos con moneyline cargados", len(self.eventos))

    def _find_slug(self, partido):
        if partido["match_id"] in self.slug_por_partido:
            return self.slug_por_partido[partido["match_id"]]
        if time.time() - self.cargado_en > REFRESCO_EVENTOS_SEG:
            self._load_events()

        mejor, mejor_score = None, 0.0
        for e in self.eventos:
            inicio = e.get("startTime") or e.get("endDate")
            if inicio:
                inicio = datetime.fromisoformat(inicio.replace("Z", "+00:00"))
                if abs((inicio - partido["utc_date"]).total_seconds()) > 36 * 3600:
                    continue
            partes = (e.get("title") or "").split(" vs. ")
            if len(partes) != 2:
                continue
            score = min(
                name_similarity(partido["home"], partes[0]),
                name_similarity(partido["away"], partes[1]),
            )
            if score > mejor_score:
                mejor, mejor_score = e.get("slug"), score

        if mejor_score < 0.75:
            logger.warning("Sin mercado en Polymarket para %s vs %s", partido["home"], partido["away"])
            return None
        self.slug_por_partido[partido["match_id"]] = mejor
        return mejor

    def prices(self, partido):
        """{"home"|"draw"|"away": {"ask", "bid", "last", "liquidity"}} o {} si no hay mercado."""
        slug = self._find_slug(partido)
        if slug is None:
            return {}
        r = requests.get(GAMMA_EVENTS_URL, params={"slug": slug}, timeout=20)
        r.raise_for_status()
        eventos = r.json()
        if not eventos:
            return {}

        precios = {}
        for m in eventos[0].get("markets", []):
            if m.get("sportsMarketType") != "moneyline" or m.get("closed"):
                continue
            titulo = m.get("groupItemTitle") or ""
            if titulo.lower().startswith("draw"):
                lado = "draw"
            elif name_similarity(titulo, partido["home"]) >= name_similarity(titulo, partido["away"]):
                lado = "home"
            else:
                lado = "away"
            try:
                token_id = (_parse_lista(m.get("clobTokenIds")) or [None])[0]  # token del "Yes"
            except IndexError:
                token_id = None
            precios[lado] = {
                "token_id": token_id,
                "ask": _to_float(m.get("bestAsk")),
                "bid": _to_float(m.get("bestBid")),
                "last": _to_float(m.get("lastTradePrice")),
                "liquidity": _to_float(m.get("liquidityNum")),
            }
        return precios


def price_history(token_id, start_ts=None, fidelity=1):
    """Serie [(datetime UTC, precio)] del token en Polymarket (desde start_ts o todo el historial)."""
    params = {"fidelity": fidelity}
    if start_ts is None:
        params["interval"] = "max"
    else:
        params["startTs"] = int(start_ts)
    # misma peticion (con reintentos) que el pipeline de Polymarket del proyecto
    historia = _pedir_historial(_SESION, token_id, intentos=2, params=params)
    if historia is None:
        raise requests.RequestException(f"Sin historial de Polymarket para el token {token_id[:12]}...")
    return [
        (datetime.fromtimestamp(punto["t"], tz=timezone.utc), float(punto["p"]))
        for punto in historia
    ]


# ---------------------------------------------------------------------------
# Anclaje del modelo al mercado
# ---------------------------------------------------------------------------

_ANCLAS = {}  # match_id -> lambdas fijadas con el precio de justo antes del inicio


def _precio_medio(info):
    bid, ask, last = info.get("bid"), info.get("ask"), info.get("last")
    if bid and ask:
        return (bid + ask) / 2
    return last or ask


def market_lambdas(partido, precios):
    """Lambdas ancladas al 1X2 de Polymarket antes del partido, o None si no se puede.

    Antes del inicio se usa el precio actual (se recalcula cada vuelta).
    Ya iniciado se usa el ultimo precio antes del silbatazo, sacado del
    historial, y se fija para el resto del partido.
    """
    if partido["match_id"] in _ANCLAS:
        return _ANCLAS[partido["match_id"]]
    lados = ("home", "draw", "away")
    if any(lado not in precios for lado in lados):
        return None

    if partido["status"] in ESTADOS_PROGRAMADOS:
        probs = [_precio_medio(precios[lado]) for lado in lados]
        return None if None in probs else fit_lambdas(*probs)

    probs = []
    for lado in lados:
        token = precios[lado].get("token_id")
        if not token:
            return None
        inicio = partido["utc_date"]
        historial = price_history(token, (inicio - timedelta(hours=3)).timestamp(), fidelity=1)
        antes = [p for t, p in historial if t <= inicio]
        if not antes:
            return None
        probs.append(antes[-1])
    _ANCLAS[partido["match_id"]] = fit_lambdas(*probs)
    logger.info("Modelo anclado al mercado para %s vs %s: %s", partido["home"], partido["away"], probs)
    return _ANCLAS[partido["match_id"]]


def match_lambdas(modelo, partido, precios):
    """(lambdas, fuente): ancladas al mercado si hay precios; si no, con las estadisticas."""
    try:
        anclado = market_lambdas(partido, precios)
    except requests.RequestException:
        logger.exception("No se pudo anclar al mercado %s vs %s", partido["home"], partido["away"])
        anclado = None
    if anclado is not None:
        return anclado, "mercado"
    return modelo.expected_goals(partido["home"], partido["away"]), "estadisticas"


# ---------------------------------------------------------------------------
# Evaluacion y avisos
# ---------------------------------------------------------------------------

# Ventaja que se exige ademas de la incertidumbre del modelo: el precio puede
# moverse un poco entre que sale la senal y que se compra (deslizamiento).
COLCHON_EJECUCION = 0.02
# Tope de la apuesta sugerida (fraccion de la banca): el modelo no ve rojas,
# lesiones ni penales, asi que nunca se sugiere mas que esto en un solo resultado.
TOPE_APUESTA = 0.05


def evaluate(partido, lambdas, precios, xgb=None):
    """Una fila por resultado (local / empate / visita) con la senal y su justificacion.

    La ventaja minima la pone el modelo: es lo que separa su probabilidad
    central de su escenario mas bajo (ritmo de goles +-20%), mas un
    colchon de ejecucion. BUENO = incluso el escenario bajo del modelo
    queda arriba del precio de compra por al menos el colchon.

    xgb: prediccion del XGBoost del proyecto antes del partido. Se reporta
    como segunda opinion y no cambia la senal (ver xgboost_metricas.json).
    """
    minuto, gl, gv = partido["minute"], partido["home_goals"], partido["away_goals"]
    probs = live_probabilities(*lambdas, minuto, gl, gv)
    bajos, altos = live_probability_range(*lambdas, minuto, gl, gv)
    previas = live_probabilities(*lambdas, 0, 0, 0)
    nombres = {"home": partido["home"], "draw": "Empate", "away": partido["away"]}
    filas = []
    for i, lado in enumerate(("home", "draw", "away")):
        p, p_bajo, ask = probs[i], bajos[i], precios.get(lado, {}).get("ask")
        umbral = (p - p_bajo) + COLCHON_EJECUCION
        if partido.get("status") == "FINISHED":
            senal, edge = "CERRADO", None  # ya no se puede entrar
        elif partido.get("status") == SIN_DATOS:
            senal, edge = "SIN MARCADOR", None  # sin marcador no se puede calcular nada en vivo
        elif ask is None or not 0.01 <= ask <= 0.99:
            senal, edge = "SIN MERCADO", None
        else:
            edge = p - ask
            if edge >= umbral:
                senal = "BUENO"
            elif edge > 0:
                senal = "ESPERAR"
            else:
                senal = "NO"
        con_precio = edge is not None
        filas.append({
            "lado": lado,
            "equipo": nombres[lado],
            "p_modelo": p,
            "p_bajo": p_bajo,
            "p_alto": altos[i],
            "p_previa": previas[i],
            "p_xgb": None if xgb is None else xgb[lado],
            "ask": ask,
            "edge": edge,
            "umbral": umbral,
            "senal": senal,
            # ganancia esperada por cada $1 apostado comprando al precio de compra
            "ev": p / ask - 1 if con_precio else None,
            "ev_bajo": p_bajo / ask - 1 if con_precio else None,
            "ml_justo": to_american(p),
            "ml_mercado": to_american(ask),
            # Kelly con el escenario bajo: si el modelo esta de mas optimista, no se sobreapuesta
            "kelly_25": min(TOPE_APUESTA, 0.25 * kelly_fraction(p_bajo, ask)) if senal == "BUENO" else 0.0,
        })
    for f in filas:
        f["porque"] = explain(partido, f)
    return filas


def explain(partido, f):
    """Texto corto con el sustento de la senal, en terminos del partido."""
    equipo = "el empate" if f["lado"] == "draw" else f["equipo"]
    if f["senal"] == "CERRADO":
        return "El partido terminó; ya no se puede entrar."
    if f["senal"] == "SIN MARCADOR":
        return (
            "El partido ya empezó, pero ninguna fuente trae todavía el marcador en vivo. Sin marcador "
            "no se puede calcular la probabilidad, así que no hay señal."
        )
    if partido["status"] in ESTADOS_PROGRAMADOS:
        return (
            "Antes del partido el modelo toma el precio de Polymarket como punto de partida, así que no "
            "hay ventaja. Las señales aparecen en vivo, cuando un gol o el reloj cambian la probabilidad "
            "y el mercado tarda en ajustarse."
        )
    contexto = (
        f"Antes del partido {equipo} tenía {f['p_previa']:.0%}. Con {partido['home_goals']}-"
        f"{partido['away_goals']} al {partido['minute']}' y ~{max(0, 95 - partido['minute'])} min por jugar, "
        f"el modelo calcula {f['p_modelo']:.0%} (entre {f['p_bajo']:.0%} y {f['p_alto']:.0%} según el ritmo de goles)."
    )
    if f["senal"] == "SIN MERCADO":
        return contexto + " No hay precio en Polymarket para comparar."
    precio = f" Polymarket lo vende en {f['ask']:.0%}."
    if f["senal"] == "BUENO":
        return contexto + precio + (
            f" Aun en el escenario bajo ({f['p_bajo']:.0%}) el precio queda abajo: ventaja de "
            f"{f['edge'] * 100:+.1f} pts contra {f['umbral'] * 100:.1f} pts exigidos. Ganancia esperada "
            f"{f['ev'] * 100:+.0f}¢ por cada $1 ({f['ev_bajo'] * 100:+.0f}¢ en el escenario bajo)."
        )
    if f["senal"] == "ESPERAR":
        return contexto + precio + (
            f" El modelo está arriba del precio por {f['edge'] * 100:.1f} pts, pero se exigen "
            f"{f['umbral'] * 100:.1f} pts: en el escenario bajo ({f['p_bajo']:.0%}) no conviene."
        )
    return contexto + precio + f" Pagarías {-f['edge'] * 100:.1f} pts más de lo que el modelo cree que vale."


def _fmt_ml(v):
    return "  -  " if v is None else f"{v:+d}"


def print_match(partido, filas, lambdas, fuente="estadisticas", metricas_xgb=None):
    print(
        f"\n{partido['home']} {partido['home_goals']}-{partido['away_goals']} {partido['away']}"
        f"   min {partido['minute']}'  [{partido['status']}]"
        f"   goles esperados: {lambdas[0]:.2f}-{lambdas[1]:.2f} ({fuente})"
    )
    if filas and filas[0]["p_xgb"] is not None:
        validacion = ""
        if metricas_xgb:
            validacion = (
                f"; exactitud validada {metricas_xgb['exactitud']:.0%} vs "
                f"{metricas_xgb['exactitud_linea_base']:.0%} de {metricas_xgb['linea_base']}"
            )
        print(
            f"  XGBoost del proyecto (antes del partido): L {filas[0]['p_xgb']:.0%} · "
            f"E {filas[1]['p_xgb']:.0%} · V {filas[2]['p_xgb']:.0%}{validacion}; no cambia la senal"
        )
    print(
        f"  {'Resultado':<26}{'Modelo':>8}{'Rango':>13}{'Ask':>7}{'Edge':>8}{'Exige':>7}"
        f"{'EV/$1':>8}{'ML merc':>9}{'1/4 Kelly':>11}  Senal"
    )
    for f in filas:
        ask = "   -" if f["ask"] is None else f"{f['ask']:.3f}"
        edge = "   -" if f["edge"] is None else f"{f['edge']:+.3f}"
        ev = "   -" if f["ev"] is None else f"{f['ev']:+.2f}"
        rango = f"{f['p_bajo']:.2f}-{f['p_alto']:.2f}"
        print(
            f"  {f['equipo'][:25]:<26}{f['p_modelo']:>8.3f}{rango:>13}"
            f"{ask:>7}{edge:>8}{f['umbral']:>7.3f}{ev:>8}{_fmt_ml(f['ml_mercado']):>9}"
            f"{f['kelly_25']:>10.1%}  {f['senal']}"
        )


def alert(partido, fila, sonido):
    msg = (
        f">>> AVISO: buen momento para moneyline de {fila['equipo']} "
        f"({partido['home']} vs {partido['away']}, ML {_fmt_ml(fila['ml_mercado'])}). {fila['porque']}"
    )
    print(msg)
    logger.info(msg)
    if sonido:
        try:
            import winsound
            winsound.Beep(1200, 400)
        except Exception:
            print("\a", end="", flush=True)


def save_rows(partido, filas):
    ahora = datetime.now(timezone.utc)
    path = LIVE_DIR / f"senales_{ahora.date().isoformat()}.csv"
    nuevo = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if nuevo:
            writer.writerow([
                "timestamp_utc", "match_id", "home", "away", "minute", "home_goals",
                "away_goals", "lado", "equipo", "p_modelo", "p_bajo", "p_alto", "ask", "edge",
                "umbral", "ev", "senal",
            ])
        for f in filas:
            writer.writerow([
                ahora.isoformat(), partido["match_id"], partido["home"], partido["away"],
                partido["minute"], partido["home_goals"], partido["away_goals"],
                f["lado"], f["equipo"], round(f["p_modelo"], 4), round(f["p_bajo"], 4),
                round(f["p_alto"], 4), f["ask"], None if f["edge"] is None else round(f["edge"], 4),
                round(f["umbral"], 4), None if f["ev"] is None else round(f["ev"], 4), f["senal"],
            ])


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------

class Tracker:
    def __init__(self, sonido, minutos_previa, competicion="CL"):
        self.modelo = StrengthModel()
        self.competicion = competicion
        self.polymarket = PolymarketMoneyline(competicion)
        self.xgb = PrediccionResultado()
        self.sonido = sonido
        self.minutos_previa = minutos_previa
        self.ultima_senal = {}  # (match_id, lado) -> senal, para avisar solo en cambios

    def analyze(self, partido, precios):
        """(lambdas, fuente, filas) del partido; no imprime ni guarda nada."""
        lambdas, fuente = match_lambdas(self.modelo, partido, precios)
        xgb = self.xgb.predict(partido["home"], partido["away"])
        return lambdas, fuente, evaluate(partido, lambdas, precios, xgb)

    def process(self, partido, precios):
        lambdas, fuente, filas = self.analyze(partido, precios)
        print_match(partido, filas, lambdas, fuente, self.xgb.metricas)
        for f in filas:
            clave = (partido["match_id"], f["lado"])
            if f["senal"] == "BUENO" and self.ultima_senal.get(clave) != "BUENO":
                alert(partido, f, self.sonido)
            self.ultima_senal[clave] = f["senal"]
        save_rows(partido, filas)

    def tick(self):
        print(f"\n=== {datetime.now().strftime('%H:%M:%S')} ===")
        partidos = fetch_live_matches(self.minutos_previa, self.competicion)
        if not partidos:
            print(f"No hay partidos de {COMPETICIONES[self.competicion][0]} en juego en este momento.")
            return
        for partido in partidos:
            try:
                precios = self.polymarket.prices(partido)
            except requests.RequestException:
                logger.exception("Error leyendo Polymarket para %s vs %s", partido["home"], partido["away"])
                precios = {}
            self.process(partido, precios)


class DemoTracker(Tracker):
    """Partido simulado: avanza 5 minutos por vuelta y el mercado reacciona con retraso."""

    def __init__(self, sonido, home, away):
        super().__init__(sonido, minutos_previa=0)
        self.partido = {
            "match_id": "demo", "home": home, "away": away, "utc_date": datetime.now(timezone.utc),
            "status": "IN_PLAY", "minute": 0, "home_goals": 0, "away_goals": 0,
        }
        self.precio_mercado = None

    def step(self):
        """Avanza el partido simulado y regresa (partido, precios)."""
        p = self.partido
        lam_h, lam_a = self.modelo.expected_goals(p["home"], p["away"])
        p["minute"] = min(95, p["minute"] + 5)
        tramo = 5 / 95
        if random.random() < lam_h * tramo:
            p["home_goals"] += 1
        if random.random() < lam_a * tramo:
            p["away_goals"] += 1
        p["status"] = "FINISHED" if p["minute"] >= 95 else "IN_PLAY"

        # El "mercado" sigue al modelo con retraso y ruido, para que aparezcan oportunidades
        justo = live_probabilities(lam_h, lam_a, p["minute"], p["home_goals"], p["away_goals"])
        if self.precio_mercado is None:
            self.precio_mercado = list(justo)
        self.precio_mercado = [
            min(0.99, max(0.01, 0.6 * viejo + 0.4 * nuevo + random.uniform(-0.03, 0.03)))
            for viejo, nuevo in zip(self.precio_mercado, justo)
        ]
        precios = {
            lado: {"ask": round(precio + 0.01, 3)}
            for lado, precio in zip(("home", "draw", "away"), self.precio_mercado)
        }
        return p, precios

    def tick(self):
        print(f"\n=== {datetime.now().strftime('%H:%M:%S')} (DEMO) ===")
        partido, precios = self.step()
        self.process(partido, precios)
        return partido["status"] == "FINISHED"


def main():
    parser = argparse.ArgumentParser(description="Tracker en vivo de moneyline (Champions, La Liga, Premier League)")
    parser.add_argument("--competicion", choices=list(COMPETICIONES), default="CL",
                        help="CL = Champions League, PD = La Liga, PL = Premier League (default CL)")
    parser.add_argument("--intervalo", type=int, default=30, help="Segundos entre actualizaciones (default 30)")
    parser.add_argument("--previa", type=int, default=30,
                        help="Incluir partidos que empiezan en los proximos N minutos (default 30)")
    parser.add_argument("--una-vez", action="store_true", help="Hacer una sola pasada y salir")
    parser.add_argument("--sin-sonido", action="store_true", help="No hacer beep en los avisos")
    parser.add_argument("--demo", action="store_true", help="Simular un partido (no usa APIs)")
    parser.add_argument("--demo-local", default="Arsenal FC")
    parser.add_argument("--demo-visita", default="FC Bayern München")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler(LOGS_DIR / "live_tracker.log", encoding="utf-8")],
    )
    # warnings y errores tambien en consola
    consola = logging.StreamHandler(sys.stdout)
    consola.setLevel(logging.WARNING)
    logging.getLogger().addHandler(consola)

    sonido = not args.sin_sonido
    if args.demo:
        tracker = DemoTracker(sonido, args.demo_local, args.demo_visita)
    else:
        tracker = Tracker(sonido, args.previa, args.competicion)

    print(
        f"Tracker de moneyline iniciado (cada {args.intervalo} s; la ventaja minima la fija el modelo). "
        "Ctrl+C para detener."
    )
    logger.info("Tracker iniciado: %s", json.dumps(vars(args)))
    try:
        while True:
            inicio = time.time()
            try:
                terminado = tracker.tick()
            except Exception:
                logger.exception("Error en esta vuelta; se reintenta en la siguiente")
                terminado = False
            if args.una_vez or terminado:
                break
            time.sleep(max(0.0, args.intervalo - (time.time() - inicio)))
    except KeyboardInterrupt:
        print("\nTracker detenido.")
    logger.info("Tracker detenido")


if __name__ == "__main__":
    main()
