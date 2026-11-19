"""
Modelo de probabilidades en vivo (1X2) para el tracker de moneyline.

Idea:
1. Antes del partido, cada equipo tiene una tasa esperada de goles
   (lambda) calculada con las estadisticas procesadas en
   data/processed/champions_league/ (temporadas recientes + tabla
   historica como "prior"), con ajuste por localia.
2. Durante el partido, los goles que faltan por anotarse se modelan como
   Poisson con lambda * (tiempo restante / tiempo total). Sumando el
   marcador actual se obtienen P(local gana), P(empate), P(visita gana).

Es un modelo simple (Poisson independiente, sin tarjetas rojas ni
alineaciones); sirve como referencia contra el precio del mercado, no
como verdad absoluta.
"""

import difflib
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.processing.polymarket import ALIAS, normalizar_nombre

ROOT_DIR = Path(__file__).resolve().parents[2]
PROCESSED_DIR = ROOT_DIR / "data" / "processed" / "champions_league"
# Copia chica de las estadisticas que si se sube a git, para correr el
# dashboard en la nube (data/processed/ esta en .gitignore).
SNAPSHOT_DIR = ROOT_DIR / "data" / "live" / "modelo"

MINUTOS_PARTIDO = 95  # 90 + reposicion promedio
FACTOR_LOCALIA = 1.12  # se multiplica al local y se divide a la visita
PESO_PRIOR = 6  # partidos "virtuales" que pesa la tabla historica
PESO_TEMPORADA_ANTERIOR = 0.5
MAX_GOLES_RESTANTES = 10
# Cuanto puede variar el ritmo de goles de cada equipo respecto al esperado
# (segundos tiempos con mas goles, el que pierde arriesga): +-20%.
VARIACION_RITMO = 0.20

# Los nombres se normalizan con las mismas reglas y alias que el pipeline de
# Polymarket del proyecto (src/processing/polymarket.py), para que un equipo
# se reconozca igual en todas las fuentes.
_GRUPOS_ALIAS = {}
for _canonico, _alias in ALIAS.items():
    _grupo = {normalizar_nombre(n) for n in [_canonico, *_alias]} - {""}
    for _clave in _grupo:
        _GRUPOS_ALIAS.setdefault(_clave, set()).update(_grupo)


def team_keys(name: str) -> set:
    """Formas normalizadas del nombre, incluidas las de sus alias conocidos."""
    clave = normalizar_nombre(name)
    return _GRUPOS_ALIAS.get(clave, {clave}) - {""} if clave else set()


def _similitud_claves(na: str, nb: str) -> float:
    if na == nb:
        return 1.0
    # "bayern" ~ "bayern munchen", pero "milan" NO ~ "inter milan"
    corto, largo = sorted((na, nb), key=len)
    if largo.startswith(corto + " "):
        return 0.95
    parecido = difflib.SequenceMatcher(None, na, nb).ratio()
    # "manchester united" vs "manchester city": cada uno tiene una palabra que el otro
    # no, asi que son equipos distintos aunque se parezcan
    ta, tb = set(na.split()), set(nb.split())
    if ta - tb and tb - ta:
        return min(parecido, 0.7)
    return parecido


def name_similarity(a: str, b: str) -> float:
    claves_a, claves_b = team_keys(a), team_keys(b)
    if not claves_a or not claves_b:
        return 0.0
    return max(_similitud_claves(x, y) for x in claves_a for y in claves_b)


def find_team(name: str, candidates, umbral=0.8):
    """Devuelve el candidato mas parecido a `name`, o None si ninguno pasa el umbral."""
    mejor, mejor_score = None, 0.0
    for c in candidates:
        s = name_similarity(name, c)
        if s > mejor_score:
            mejor, mejor_score = c, s
    return mejor if mejor_score >= umbral else None


@dataclass
class TeamRating:
    team: str
    goals_for: float  # goles a favor esperados por partido
    goals_against: float  # goles en contra esperados por partido
    fuente: str


class StrengthModel:
    """Fuerza ofensiva/defensiva por equipo a partir de los datos procesados."""

    def __init__(self, processed_dir: Path = PROCESSED_DIR):
        temporadas = sorted(
            p for p in processed_dir.glob("season=*")
            if p.name.split("=", 1)[1].isdigit()
        )
        if temporadas:
            actual = pd.read_csv(temporadas[-1] / "team_stats.csv")
            actual["peso"] = 1.0
            partes = [actual]
            if len(temporadas) > 1:
                anterior = pd.read_csv(temporadas[-2] / "team_stats.csv")
                anterior["peso"] = PESO_TEMPORADA_ANTERIOR
                partes.append(anterior)
            self.recent = pd.concat(partes, ignore_index=True)
            hist_path = processed_dir / "historical_performance.csv"
            self.historical = pd.read_csv(hist_path) if hist_path.exists() else pd.DataFrame()
        elif (SNAPSHOT_DIR / "recent.csv").exists():
            self.recent = pd.read_csv(SNAPSHOT_DIR / "recent.csv")
            self.historical = pd.read_csv(SNAPSHOT_DIR / "historical_performance.csv")
        else:
            raise RuntimeError(
                f"No hay temporadas procesadas en {processed_dir} ni snapshot en {SNAPSHOT_DIR}. "
                "Corre primero: python -m src.processing.champions_league"
            )

        mp = self.recent["matches_played"] * self.recent["peso"]
        self.league_avg = float((self.recent["goals_for_avg"] * mp).sum() / mp.sum())

    def export_snapshot(self, out_dir: Path = SNAPSHOT_DIR):
        out_dir.mkdir(parents=True, exist_ok=True)
        self.recent.to_csv(out_dir / "recent.csv", index=False)
        self.historical.to_csv(out_dir / "historical_performance.csv", index=False)
        return out_dir

    def rating(self, team: str) -> TeamRating:
        prior_gf = prior_ga = self.league_avg
        fuente = "promedio liga"

        if not self.historical.empty:
            h = find_team(team, self.historical["team"])
            if h is not None:
                fila = self.historical[self.historical["team"] == h].iloc[0]
                if fila["matches_played"] > 0:
                    prior_gf = fila["goals_for"] / fila["matches_played"]
                    prior_ga = fila["goals_against"] / fila["matches_played"]
                    fuente = "historico"

        r = find_team(team, self.recent["team"].unique())
        if r is None:
            return TeamRating(team, prior_gf, prior_ga, fuente)

        filas = self.recent[self.recent["team"] == r]
        n = filas["matches_played"] * filas["peso"]
        n_total = float(n.sum())
        gf = (float((filas["goals_for_avg"] * n).sum()) + PESO_PRIOR * prior_gf) / (n_total + PESO_PRIOR)
        ga = (float((filas["goals_against_avg"] * n).sum()) + PESO_PRIOR * prior_ga) / (n_total + PESO_PRIOR)
        return TeamRating(team, gf, ga, f"{fuente} + reciente ({n_total:.1f} pj)")

    def expected_goals(self, home: str, away: str):
        """Lambdas de goles para el partido completo (local, visita)."""
        h, a = self.rating(home), self.rating(away)
        avg = self.league_avg
        lam_home = avg * (h.goals_for / avg) * (a.goals_against / avg) * FACTOR_LOCALIA
        lam_away = avg * (a.goals_for / avg) * (h.goals_against / avg) / FACTOR_LOCALIA
        return lam_home, lam_away


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * lam ** k / math.factorial(k)


def live_probabilities(lam_home, lam_away, minute, home_goals, away_goals):
    """P(local), P(empate), P(visita) dado el marcador y el minuto actual."""
    restante = max(0.0, MINUTOS_PARTIDO - float(minute)) / MINUTOS_PARTIDO
    lh, la = lam_home * restante, lam_away * restante
    ph = [_poisson_pmf(k, lh) for k in range(MAX_GOLES_RESTANTES + 1)]
    pa = [_poisson_pmf(k, la) for k in range(MAX_GOLES_RESTANTES + 1)]

    p_home = p_draw = p_away = 0.0
    for i, pi in enumerate(ph):
        for j, pj in enumerate(pa):
            diff = (home_goals + i) - (away_goals + j)
            if diff > 0:
                p_home += pi * pj
            elif diff == 0:
                p_draw += pi * pj
            else:
                p_away += pi * pj
    total = p_home + p_draw + p_away
    return p_home / total, p_draw / total, p_away / total


def live_probability_range(lam_home, lam_away, minute, home_goals, away_goals, variacion=VARIACION_RITMO):
    """(minimos, maximos) de P(local), P(empate), P(visita) moviendo el ritmo de goles.

    El ritmo de goles no es constante durante un partido (los segundos
    tiempos tienen mas goles, el que va perdiendo arriesga), asi que se
    recalcula con el ritmo de cada equipo entre (1 - variacion) y
    (1 + variacion), en todas las combinaciones. El ancho del rango es
    la incertidumbre del modelo en ese momento.
    """
    factores = (1 - variacion, 1.0, 1 + variacion)
    escenarios = [
        live_probabilities(lam_home * fh, lam_away * fa, minute, home_goals, away_goals)
        for fh in factores for fa in factores
    ]
    minimos = tuple(min(e[i] for e in escenarios) for i in range(3))
    maximos = tuple(max(e[i] for e in escenarios) for i in range(3))
    return minimos, maximos


def fit_lambdas(p_home: float, p_draw: float, p_away: float):
    """Lambdas (local, visita) cuyo 1X2 al minuto 0 se parece mas al dado.

    Sirve para "anclar" el modelo al precio del mercado antes del partido:
    el mercado sabe mas que nuestras estadisticas sobre la fuerza de cada
    equipo, y el modelo solo agrega como cambian las probabilidades con
    los goles y el tiempo. Busqueda en malla gruesa y luego fina.
    """
    total = p_home + p_draw + p_away
    objetivo = (p_home / total, p_draw / total, p_away / total)

    def error(lh, la):
        probs = live_probabilities(lh, la, 0, 0, 0)
        return sum((a - b) ** 2 for a, b in zip(probs, objetivo))

    mejor = min(
        ((lh / 10, la / 10) for lh in range(1, 51) for la in range(1, 51)),
        key=lambda par: error(*par),
    )
    finos = [
        (mejor[0] + i / 100, mejor[1] + j / 100)
        for i in range(-10, 11) for j in range(-10, 11)
        if mejor[0] + i / 100 > 0 and mejor[1] + j / 100 > 0
    ]
    return min(finos, key=lambda par: error(*par))


def to_american(p: float):
    """Probabilidad -> momio americano (moneyline). None si p no es valida."""
    if p is None or p <= 0 or p >= 1:
        return None
    if p >= 0.5:
        return -round(100 * p / (1 - p))
    return round(100 * (1 - p) / p)


def kelly_fraction(p_model: float, price: float) -> float:
    """Fraccion de Kelly para comprar a `price` (contrato que paga 1)."""
    if price is None or price <= 0 or price >= 1:
        return 0.0
    return max(0.0, (p_model - price) / (1 - price))


if __name__ == "__main__":
    # python -m src.live.modelo  ->  actualiza el snapshot que usa el dashboard
    destino = StrengthModel().export_snapshot()
    print(f"Snapshot del modelo guardado en {destino}")
