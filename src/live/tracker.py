"""
Tracker en vivo de moneyline (1X2) para partidos de Champions League.

Cada N segundos (30 por default):
1. Pide a football-data.org los partidos de Champions de hoy que esten
   en juego (marcador y minuto).
2. Calcula con src.live.modelo la probabilidad de local / empate /
   visita dado el marcador y el tiempo restante.
3. Busca el mercado moneyline del partido en Polymarket (API publica,
   sin credenciales) y compara contra el precio de compra (best ask).
4. Avisa si es buen momento para entrar: el modelo da mas probabilidad
   que la que el mercado esta cobrando por al menos --edge-min puntos.

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

from src.ingestion.extract_api_football_data import API_BASE_URL, COMPETITION_CODE, get_api_token
from src.live.modelo import (
    StrengthModel,
    kelly_fraction,
    live_probabilities,
    name_similarity,
    to_american,
)

ROOT_DIR = Path(__file__).resolve().parents[2]
LIVE_DIR = ROOT_DIR / "data" / "interim" / "live"
LOGS_DIR = ROOT_DIR / "logs"
LIVE_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
POLYMARKET_TAG = "ucl"

ESTADOS_EN_JUEGO = {"IN_PLAY", "PAUSED", "LIVE"}
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


def fetch_live_matches(minutos_previa: int):
    """Partidos de Champions en juego (y los que empiezan en <= minutos_previa)."""
    ahora = datetime.now(timezone.utc)
    params = {
        "dateFrom": (ahora - timedelta(days=1)).date().isoformat(),
        "dateTo": (ahora + timedelta(days=1, minutes=minutos_previa)).date().isoformat(),
    }
    url = f"{API_BASE_URL}/competitions/{COMPETITION_CODE}/matches"
    response = requests.get(
        url, headers={"X-Auth-Token": get_api_token()}, params=params, timeout=20
    )
    response.raise_for_status()

    partidos = []
    for m in response.json().get("matches", []):
        status = m.get("status")
        utc_date = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        if status in ESTADOS_EN_JUEGO:
            minuto = _parse_minute(m.get("minute"))
            if minuto is None:
                minuto = estimate_minute(utc_date, status, ahora)
        elif status in ESTADOS_PROGRAMADOS and ahora <= utc_date <= ahora + timedelta(minutes=minutos_previa):
            minuto = 0
        else:
            continue

        score = m.get("score", {}).get("fullTime", {}) or {}
        partidos.append({
            "match_id": m.get("id"),
            "home": m.get("homeTeam", {}).get("name"),
            "away": m.get("awayTeam", {}).get("name"),
            "utc_date": utc_date,
            "status": status,
            "minute": minuto,
            "home_goals": score.get("home") or 0,
            "away_goals": score.get("away") or 0,
        })
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

    def __init__(self):
        self.eventos = []
        self.cargado_en = 0.0
        self.slug_por_partido = {}

    def _load_events(self):
        eventos, offset = [], 0
        while True:
            r = requests.get(
                GAMMA_EVENTS_URL,
                params={"tag_slug": POLYMARKET_TAG, "closed": "false", "limit": 100, "offset": offset},
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
            precios[lado] = {
                "ask": _to_float(m.get("bestAsk")),
                "bid": _to_float(m.get("bestBid")),
                "last": _to_float(m.get("lastTradePrice")),
                "liquidity": _to_float(m.get("liquidityNum")),
            }
        return precios


# ---------------------------------------------------------------------------
# Evaluacion y avisos
# ---------------------------------------------------------------------------

def evaluate(partido, probs, precios, edge_min):
    """Una fila por resultado (local / empate / visita) con la senal."""
    nombres = {"home": partido["home"], "draw": "Empate", "away": partido["away"]}
    filas = []
    for lado, p in zip(("home", "draw", "away"), probs):
        ask = precios.get(lado, {}).get("ask")
        if ask is None or not 0.01 <= ask <= 0.99:
            senal, edge = "SIN MERCADO", None
        else:
            edge = p - ask
            if edge >= edge_min:
                senal = "BUENO"
            elif edge > 0:
                senal = "ESPERAR"
            else:
                senal = "NO"
        filas.append({
            "lado": lado,
            "equipo": nombres[lado],
            "p_modelo": p,
            "ask": ask,
            "edge": edge,
            "senal": senal,
            "ml_justo": to_american(p),
            "ml_mercado": to_american(ask),
            "kelly_25": 0.25 * kelly_fraction(p, ask) if edge is not None else 0.0,
        })
    return filas


def _fmt_ml(v):
    return "  -  " if v is None else f"{v:+d}"


def print_match(partido, filas, lambdas):
    print(
        f"\n{partido['home']} {partido['home_goals']}-{partido['away_goals']} {partido['away']}"
        f"   min {partido['minute']}'  [{partido['status']}]"
        f"   xG pre: {lambdas[0]:.2f}-{lambdas[1]:.2f}"
    )
    print(f"  {'Resultado':<28}{'Modelo':>8}{'Ask':>8}{'Edge':>8}{'ML justo':>10}{'ML merc':>10}{'1/4 Kelly':>11}  Senal")
    for f in filas:
        ask = "   -" if f["ask"] is None else f"{f['ask']:.3f}"
        edge = "   -" if f["edge"] is None else f"{f['edge']:+.3f}"
        print(
            f"  {f['equipo'][:27]:<28}{f['p_modelo']:>8.3f}{ask:>8}{edge:>8}"
            f"{_fmt_ml(f['ml_justo']):>10}{_fmt_ml(f['ml_mercado']):>10}"
            f"{f['kelly_25']:>10.1%}  {f['senal']}"
        )


def alert(partido, fila, sonido):
    msg = (
        f">>> AVISO: buen momento para moneyline de {fila['equipo']} "
        f"({partido['home']} vs {partido['away']}, min {partido['minute']}'): "
        f"modelo {fila['p_modelo']:.1%} vs mercado {fila['ask']:.1%} "
        f"(edge {fila['edge']:+.1%}, ML {_fmt_ml(fila['ml_mercado'])})"
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
                "away_goals", "lado", "equipo", "p_modelo", "ask", "edge", "senal",
            ])
        for f in filas:
            writer.writerow([
                ahora.isoformat(), partido["match_id"], partido["home"], partido["away"],
                partido["minute"], partido["home_goals"], partido["away_goals"],
                f["lado"], f["equipo"], round(f["p_modelo"], 4), f["ask"],
                None if f["edge"] is None else round(f["edge"], 4), f["senal"],
            ])


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------

class Tracker:
    def __init__(self, edge_min, sonido, minutos_previa):
        self.modelo = StrengthModel()
        self.polymarket = PolymarketMoneyline()
        self.edge_min = edge_min
        self.sonido = sonido
        self.minutos_previa = minutos_previa
        self.ultima_senal = {}  # (match_id, lado) -> senal, para avisar solo en cambios

    def analyze(self, partido, precios):
        """(lambdas, filas) del partido; no imprime ni guarda nada."""
        lambdas = self.modelo.expected_goals(partido["home"], partido["away"])
        probs = live_probabilities(*lambdas, partido["minute"], partido["home_goals"], partido["away_goals"])
        return lambdas, evaluate(partido, probs, precios, self.edge_min)

    def process(self, partido, precios):
        lambdas, filas = self.analyze(partido, precios)
        print_match(partido, filas, lambdas)
        for f in filas:
            clave = (partido["match_id"], f["lado"])
            if f["senal"] == "BUENO" and self.ultima_senal.get(clave) != "BUENO":
                alert(partido, f, self.sonido)
            self.ultima_senal[clave] = f["senal"]
        save_rows(partido, filas)

    def tick(self):
        print(f"\n=== {datetime.now().strftime('%H:%M:%S')} ===")
        partidos = fetch_live_matches(self.minutos_previa)
        if not partidos:
            print("No hay partidos de Champions en juego en este momento.")
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

    def __init__(self, edge_min, sonido, home, away):
        super().__init__(edge_min, sonido, minutos_previa=0)
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
    parser = argparse.ArgumentParser(description="Tracker en vivo de moneyline (Champions League)")
    parser.add_argument("--intervalo", type=int, default=30, help="Segundos entre actualizaciones (default 30)")
    parser.add_argument("--edge-min", type=float, default=0.05,
                        help="Ventaja minima modelo - precio para marcar BUENO (default 0.05 = 5 puntos)")
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
        tracker = DemoTracker(args.edge_min, sonido, args.demo_local, args.demo_visita)
    else:
        tracker = Tracker(args.edge_min, sonido, args.previa)

    print(
        f"Tracker de moneyline iniciado (cada {args.intervalo} s, edge minimo {args.edge_min:.0%}). "
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
