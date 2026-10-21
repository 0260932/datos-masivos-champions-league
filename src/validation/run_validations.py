"""Corre todas las validaciones de calidad sobre data/interim/ y data/processed/.

Uso:
    python -m src.validation.run_validations

Imprime un reporte por dataset y un resumen final. Termina con código de
salida 1 si alguna validación falló, para que pueda usarse en automatizaciones.
Los parámetros (columnas, rangos, volúmenes) salen de la exploración de los
datos reales del 21 de octubre de 2026 (ver docs/validation.md).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from src.processing.polymarket import claves_equipo, normalizar_nombre
from src.validation import validators as v

ROOT = Path(__file__).resolve().parents[2]
CL_INTERIM = ROOT / "data" / "interim" / "champions_league"
CL_PROCESSED = ROOT / "data" / "processed" / "champions_league"
PM_INTERIM = ROOT / "data" / "interim" / "polymarket"
PM_PROCESSED = ROOT / "data" / "processed" / "polymarket"

HOY = pd.Timestamp.now(tz="UTC").normalize()


def _leer_particiones(carpeta: Path, archivo: str) -> pd.DataFrame:
    """Une los CSV de todas las particiones (p. ej. season=2023/, season=2026/...)."""
    rutas = sorted(carpeta.glob(f"*=*/{archivo}"))
    if not rutas:
        raise FileNotFoundError(f"No hay particiones {archivo} en {carpeta}")
    return pd.concat([pd.read_csv(r) for r in rutas], ignore_index=True)


# --------------------------------------------------------------------------------------
# Champions League
# --------------------------------------------------------------------------------------
def validar_partidos(df: pd.DataFrame) -> list[v.ResultadoValidacion]:
    fechas = pd.to_datetime(df["utc_date"], utc=True, format="mixed")
    anio_inicio = fechas.groupby(df["season"]).transform("min").dt.year
    return [
        v.validate_required_columns(df, ["match_id", "season", "stage", "matchday", "utc_date",
                                         "status", "home_team", "away_team", "home_score", "away_score"]),
        v.validate_dtypes(df, {"match_id": "int", "season": "int", "matchday": "int",
                               "home_score": "numeric", "away_score": "numeric", "home_team": "str"}),
        v.validate_expected_volume(df, min_rows=100, max_rows=1000),
        v.validate_no_duplicates(df, subset=["match_id"]),
        v.validate_no_missing(df, ["match_id", "season", "utc_date", "status", "home_team", "away_team"]),
        v.validate_allowed_values(df, "status", ["FINISHED", "TIMED", "SCHEDULED", "IN_PLAY", "PAUSED", "POSTPONED"]),
        v.validate_value_ranges(df, "home_score", 0, 15),
        v.validate_value_ranges(df, "away_score", 0, 15),
        v.validate_row_rule(df, "marcador vacío solo en partidos no jugados",
                            lambda d: d["home_score"].notna() | (d["status"] != "FINISHED"),
                            columnas_ejemplo=["match_id", "status"]),
        v.validate_row_rule(df, "un equipo no juega contra sí mismo",
                            lambda d: d["home_team"] != d["away_team"]),
        v.validate_temporal_consistency(df, "utc_date", min_date="1955-01-01",
                                        max_date=HOY + pd.Timedelta(days=400)),
        v.validate_row_rule(df, "partidos FINISHED no tienen fecha futura",
                            lambda d: (d["status"] != "FINISHED") | (fechas <= pd.Timestamp.now(tz="UTC"))),
        v.validate_row_rule(df, "season = año de inicio de la temporada (convención de la API)",
                            lambda d: d["season"] == anio_inicio,
                            columnas_ejemplo=["season", "utc_date"]),
    ]


def validar_historico(df: pd.DataFrame) -> list[v.ResultadoValidacion]:
    numericas = ["matches_played", "wins", "draws", "losses", "goals_for", "goals_against"]
    return [
        v.validate_required_columns(df, ["team", *numericas, "goal_difference"]),
        v.validate_dtypes(df, {c: "int" for c in numericas}),
        v.validate_expected_volume(df, min_rows=300, max_rows=450),
        v.validate_no_duplicates(df, subset=["team"]),
        v.validate_no_missing(df, df.columns),
        *[v.validate_value_ranges(df, c, 0) for c in numericas],
        v.validate_row_rule(df, "wins + draws + losses = matches_played",
                            lambda d: d["wins"] + d["draws"] + d["losses"] == d["matches_played"],
                            columnas_ejemplo=["team", "matches_played", "wins", "draws", "losses"]),
        v.validate_row_rule(df, "goals_for - goals_against = goal_difference",
                            lambda d: d["goals_for"] - d["goals_against"] == d["goal_difference"]),
        # En toda la historia, cada gol anotado por un equipo es recibido por otro.
        v.validate_row_rule(df.head(1), "total goles a favor = total goles en contra",
                            lambda d: pd.Series([df["goals_for"].sum() == df["goals_against"].sum()])),
        v.validate_row_rule(df.head(1), "total victorias = total derrotas",
                            lambda d: pd.Series([df["wins"].sum() == df["losses"].sum()])),
    ]


def validar_finales(df: pd.DataFrame) -> list[v.ResultadoValidacion]:
    return [
        v.validate_required_columns(df, ["season", "winners", "score", "runners_up", "venue", "attendance"]),
        v.validate_expected_volume(df, min_rows=60, max_rows=80),
        v.validate_no_missing(df, ["season", "winners", "runners_up"]),
        v.validate_no_duplicates(df, subset=["season"]),
        v.validate_value_ranges(df, "attendance", 0, 150_000),
        v.validate_row_rule(df, "campeón distinto del subcampeón",
                            lambda d: d["winners"] != d["runners_up"], columnas_ejemplo=["season"]),
    ]


def validar_team_stats(df: pd.DataFrame) -> list[v.ResultadoValidacion]:
    return [
        v.validate_required_columns(df, ["season", "team", "matches_played", "goals_for_avg",
                                         "goals_against_avg", "wins", "win_pct", "tiene_historial_reciente"]),
        v.validate_no_duplicates(df, subset=["season", "team"]),
        v.validate_no_missing(df, ["season", "team", "goals_for_avg", "goals_against_avg"]),
        v.validate_value_ranges(df, "win_pct", 0, 1),
        v.validate_value_ranges(df, "goals_for_avg", 0, 10),
        v.validate_value_ranges(df, "goals_against_avg", 0, 10),
        v.validate_allowed_values(df, "tiene_historial_reciente", [0, 1]),
        v.validate_row_rule(df, "wins <= matches_played", lambda d: d["wins"] <= d["matches_played"]),
        v.validate_row_rule(df, "win_pct vacío solo si no hay historial reciente",
                            lambda d: d["win_pct"].notna() == (d["tiene_historial_reciente"] == 1)),
    ]


# --------------------------------------------------------------------------------------
# Polymarket
# --------------------------------------------------------------------------------------
def validar_mercados(df: pd.DataFrame) -> list[v.ResultadoValidacion]:
    return [
        v.validate_required_columns(df, ["event_id", "market_id", "question", "outcome_prices", "best_bid",
                                         "best_ask", "volume", "liquidity", "prob_implicita",
                                         "equipo", "tipo_mercado", "spread"]),
        v.validate_dtypes(df, {"market_id": "int", "prob_implicita": "float", "volume": "numeric",
                               "active": "bool", "sin_ofertas_compra": "bool"}),
        v.validate_expected_volume(df, min_rows=100, max_rows=500),
        v.validate_no_duplicates(df, subset=["market_id"]),
        v.validate_no_missing(df, ["market_id", "question", "prob_implicita", "equipo", "tipo_mercado"]),
        v.validate_value_ranges(df, "prob_implicita", 0, 1),
        v.validate_value_ranges(df, "best_bid", 0, 1),
        v.validate_value_ranges(df, "best_ask", 0, 1),
        v.validate_value_ranges(df, "volume", 0),
        v.validate_value_ranges(df, "spread", 0, 1),
        v.validate_outcome_prices_sum(df, tolerance=0.01),
        v.validate_row_rule(df, "best_bid vacío solo si está marcado sin_ofertas_compra",
                            lambda d: d["best_bid"].isna() == d["sin_ofertas_compra"]),
        v.validate_row_rule(df, "solo mercados activos y abiertos",
                            lambda d: d["active"] & ~d["closed"]),
    ]


def validar_resumen_eventos(df: pd.DataFrame) -> list[v.ResultadoValidacion]:
    return [
        v.validate_no_duplicates(df, subset=["event_id"]),
        v.validate_group_sum(df, "tipo_mercado", "suma_prob_implicita", "ganadores_esperados",
                             relative_tolerance=0.10),
    ]


def validar_historial(df: pd.DataFrame) -> list[v.ResultadoValidacion]:
    n_mercados = df["market_id"].nunique()
    return [
        v.validate_required_columns(df, ["market_id", "t", "p", "fecha", "cambio_pp", "umbral_pp",
                                         "es_movimiento_significativo"]),
        v.validate_expected_volume(df, min_rows=1000),
        v.validate_no_duplicates(df, subset=["market_id", "fecha"]),
        v.validate_value_ranges(df, "p", 0, 1),
        v.validate_temporal_consistency(df, "t", min_date="2026-01-01"),
        v.validate_row_rule(df.head(1), "cambio_pp vacío solo en el primer día de cada mercado",
                            lambda d: pd.Series([df["cambio_pp"].isna().sum() == n_mercados])),
        v.validate_row_rule(df, "umbral de movimiento >= 0.02 (piso definido)",
                            lambda d: d["umbral_pp"] >= 0.02 - 1e-9),
    ]


def validar_movimientos(df: pd.DataFrame) -> list[v.ResultadoValidacion]:
    return [
        v.validate_row_rule(df, "|cambio_pp| >= umbral_pp en todos los movimientos",
                            lambda d: d["cambio_pp"].abs() >= d["umbral_pp"] - 1e-9),
        v.validate_row_rule(df, "ningún movimiento en el periodo de apertura",
                            lambda d: ~d["es_periodo_apertura"]),
    ]


def validar_referencia(df: pd.DataFrame) -> list[v.ResultadoValidacion]:
    return [
        v.validate_no_duplicates(df, subset=["equipo"]),
        v.validate_value_ranges(df, "prob_referencia_campeon", 0, 1),
        v.validate_value_ranges(df, "winrate_historico", 0, 1),
        v.validate_row_rule(df.head(1), "prob_referencia_campeon suma 1",
                            lambda d: pd.Series([abs(df["prob_referencia_campeon"].sum() - 1) < 1e-3])),
        v.validate_no_missing(df, ["winrate_historico"]),
    ]


# --------------------------------------------------------------------------------------
# Consistencia entre fuentes
# --------------------------------------------------------------------------------------
def validar_cruces(team_stats, historico, mercados, historial, movimientos) -> list[v.ResultadoValidacion]:
    temporadas = pd.to_numeric(team_stats["season"], errors="coerce")  # ignora "sin_historial_reciente"
    actuales = team_stats[temporadas == temporadas.max()].copy()
    actuales["clave"] = actuales["team"].map(normalizar_nombre)

    historico = historico.assign(clave=historico["team"].map(normalizar_nombre))

    # Polymarket: cada equipo puede tener varios nombres alternativos (tabla ALIAS de polymarket.py)
    equipos_pm = mercados[["equipo"]].drop_duplicates()
    claves_pm = equipos_pm.assign(clave=equipos_pm["equipo"].map(claves_equipo)).explode("clave")

    resultados = [
        ("CL temporada actual vs Polymarket",
         v.validate_cross_table_consistency(actuales, claves_pm, "clave", min_coverage=0.9)),
        ("CL temporada actual vs tabla histórica",
         v.validate_cross_table_consistency(actuales, historico, "clave", min_coverage=0.8)),
        ("historial vs mercados (market_id)",
         v.validate_cross_table_consistency(historial, mercados, "market_id")),
        ("movimientos vs historial (market_id)",
         v.validate_cross_table_consistency(movimientos, historial, "market_id")),
    ]
    for descripcion, r in resultados:
        r.nombre = f"{descripcion}: {r.nombre}"
    return [r for _, r in resultados]


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------
def main() -> int:
    team_stats = _leer_particiones(CL_PROCESSED, "team_stats.csv")
    historico = pd.read_csv(CL_PROCESSED / "historical_performance.csv")
    mercados = _leer_particiones(PM_PROCESSED, "mercados.csv")
    historial = pd.read_csv(PM_INTERIM / "historial_precios.csv")
    movimientos = pd.read_csv(PM_PROCESSED / "movimientos_significativos.csv")

    bloques = {
        "CL | interim/matches_clean.csv": validar_partidos(pd.read_csv(CL_INTERIM / "matches_clean.csv")),
        "CL | processed/historical_performance.csv": validar_historico(historico),
        "CL | processed/finals.csv": validar_finales(pd.read_csv(CL_PROCESSED / "finals.csv")),
        "CL | processed/season=*/team_stats.csv": validar_team_stats(team_stats),
        "PM | processed/tipo_mercado=*/mercados.csv": validar_mercados(mercados),
        "PM | processed/resumen_eventos.csv": validar_resumen_eventos(pd.read_csv(PM_PROCESSED / "resumen_eventos.csv")),
        "PM | interim/historial_precios.csv": validar_historial(historial),
        "PM | processed/movimientos_significativos.csv": validar_movimientos(movimientos),
        "PM | processed/referencia_equipos.csv": validar_referencia(pd.read_csv(PM_PROCESSED / "referencia_equipos.csv")),
        "Cruce entre fuentes": validar_cruces(team_stats, historico, mercados, historial, movimientos),
    }

    total, fallas = 0, []
    for dataset, resultados in bloques.items():
        print(f"\n=== {dataset} ===")
        for r in resultados:
            print(r)
            total += 1
            if not r.ok:
                fallas.append(f"{dataset} -> {r.nombre}")

    print("\n" + "=" * 80)
    print(f"RESUMEN: {total - len(fallas)} de {total} validaciones pasaron, {len(fallas)} fallaron.")
    for f in fallas:
        print(f"  - FALLA: {f}")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
