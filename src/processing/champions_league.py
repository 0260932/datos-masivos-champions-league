"""
Pipeline de procesamiento de la fuente Champions League (Entrega 2,
Integrante 2 de 4).

Toma los archivos crudos de data/raw/ (API de football-data.org y
dataset historico de Kaggle), los limpia y transforma, y guarda:
- version intermedia (post-limpieza, pre-agregaciones) en data/interim/
- version final (lista para analisis) en data/processed/, particionada
  por season.

Ejecucion:
    python -m src.processing.champions_league
"""

import logging
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd
from src.processing.polymarket import nombre_canonico

ROOT_DIR = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT_DIR / "data" / "raw"
INTERIM_DIR = ROOT_DIR / "data" / "interim" / "champions_league"
PROCESSED_DIR = ROOT_DIR / "data" / "processed" / "champions_league"
LOGS_DIR = ROOT_DIR / "logs"

INTERIM_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "processing_champions_league.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def clean_api_matches(df: pd.DataFrame) -> pd.DataFrame:
    """Limpia champions_league_matches_api.csv: tipos, columnas relevantes.

    Nota: home_score/away_score vienen NaN para partidos con status=TIMED
    (programados, aun no jugados). Es esperado, no se imputan ni se
    eliminan esas filas; quien agregue estadisticas debe filtrar por
    status == "FINISHED".
    """
    cols = [
        "match_id", "season", "stage", "matchday", "utc_date",
        "status", "home_team", "away_team", "home_score", "away_score",
    ]
    df = df[cols].copy()

    df["utc_date"] = pd.to_datetime(df["utc_date"], errors="coerce")
    df["home_score"] = pd.to_numeric(df["home_score"], errors="coerce")
    df["away_score"] = pd.to_numeric(df["away_score"], errors="coerce")

    antes = len(df)
    df = df.drop_duplicates(subset=["match_id"])
    logger.info("clean_api_matches: %s filas -> %s tras quitar duplicados", antes, len(df))

    faltantes_inesperados = df[df["home_score"].isna() & (df["status"] != "TIMED")]
    if len(faltantes_inesperados) > 0:
        logger.warning(
            "clean_api_matches: %s filas con score faltante y status != TIMED (revisar)",
            len(faltantes_inesperados),
        )

    logger.info(
        "clean_api_matches: %s partidos FINISHED, %s TIMED (sin marcador, esperado)",
        (df["status"] == "FINISHED").sum(),
        (df["status"] == "TIMED").sum(),
    )

    return df


def clean_historical_performance(df: pd.DataFrame) -> pd.DataFrame:
    """Limpia UCL_AllTime_Performance_Table.csv.

    La columna "goals" viene corrupta: Excel la interpreto como un valor
    de tiempo [h]:mm:ss en vez de "goles_a_favor:goles_en_contra". Cuando
    los goles en contra originales pasaban de 60, se "acarrearon" como
    horas. Se reconstruye resolviendo:
        k = (H - MM - Dif) / 61
        goals_against = 60*k + MM
        goals_for = H - k
    Verificado contra los 354 equipos: k siempre sale entero no negativo
    y goals_for - goals_against == Dif en el 100% de los casos.

    La columna "Pt." se descarta: es identica a "Dif" en el 100% de las
    filas (dato duplicado/mal etiquetado en la fuente, no son puntos
    reales de competencia).
    """
    df = df.copy()

    partes = df["goals"].str.split(":", expand=True)
    H = partes[0].astype(int)
    MM = partes[1].astype(int)

    k = ((H - MM - df["Dif"]) / 61).round().astype(int)
    df["goals_against"] = 60 * k + MM
    df["goals_for"] = H - k

    assert (df["goals_for"] - df["goals_against"] == df["Dif"]).all(), (
        "Reconstruccion de goals_for/goals_against no cuadra con Dif"
    )
    assert (df["goals_against"] >= 0).all() and (df["goals_for"] >= 0).all(), (
        "Se encontraron goles negativos tras reconstruir goals"
    )

    df = df.rename(columns={
        "Team": "team", "M.": "matches_played", "W": "wins",
        "D": "draws", "L": "losses", "Dif": "goal_difference",
    })
    df = df[[
        "team", "matches_played", "wins", "draws", "losses",
        "goals_for", "goals_against", "goal_difference",
    ]]

    antes = len(df)
    df = df.drop_duplicates(subset=["team"])
    logger.info("clean_historical_performance: %s filas -> %s tras quitar duplicados", antes, len(df))

    return df


def clean_finals(df: pd.DataFrame) -> pd.DataFrame:
    """Limpia UCL_Finals_1955-2023.csv.

    La fuente trae basura de encoding real dentro de los datos (no es
    un problema de como se lee el archivo): guiones suaves invisibles
    (\\xad) en nombres de columna, y el caracter "û" en vez de un guion
    normal en la columna Season (ej. "1955û56" -> "1955-56").
    """
    df = df.copy()
    df.columns = [
        unicodedata.normalize("NFKD", c).replace("\xad", "") for c in df.columns
    ]
    df = df.rename(columns={"Attendance": "attendance", "Season": "season"})

    df["season"] = df["season"].str.replace("û", "-", regex=False)
    df["attendance"] = (
        df["attendance"].astype(str).str.replace(",", "", regex=False)
    )
    df["attendance"] = pd.to_numeric(df["attendance"], errors="coerce")

    cols = ["season", "Winners", "Score", "Runners-up", "Venue", "attendance"]
    df = df[[c for c in cols if c in df.columns]]
    df.columns = [c.lower().replace("-", "_") for c in df.columns]

    antes = len(df)
    df = df.drop_duplicates(subset=["season"], keep="last")
    logger.info("clean_finals: %s filas -> %s tras quitar duplicados (por season, se queda el desempate si hubo)", antes, len(df))

    return df


TEAM_NAME_MAP = {
    "FC Barcelona": "Barcelona", "Manchester United FC": "Manchester United",
    "Liverpool FC": "Liverpool", "FC Porto": "Porto", "SL Benfica": "Benfica",
    "Chelsea FC": "Chelsea", "AFC Ajax": "Ajax", "Arsenal FC": "Arsenal",
    "Atletico Madrid": "Atletico Madrid",
}

def standardize_team_name(name: str) -> str:
    """Unifica nombres de equipo. Primero el mapa manual de esta fuente (TEAM_NAME_MAP);
    si no hay coincidencia, se reusa el catalogo de alias de Polymarket (nombre_canonico),
    que ya logra 100% de coincidencia entre Champions League y Polymarket."""
    if not isinstance(name, str):
        return name
    if name in TEAM_NAME_MAP:
        return TEAM_NAME_MAP[name]
    return nombre_canonico(name)


def build_team_season_stats(api_clean: pd.DataFrame) -> pd.DataFrame:
    """Agregaciones por equipo y temporada, solo partidos FINISHED."""
    finished = api_clean[api_clean["status"] == "FINISHED"].copy()

    home = finished.rename(columns={
        "home_team": "team", "home_score": "goals_for", "away_score": "goals_against",
    })[["season", "team", "goals_for", "goals_against"]]
    away = finished.rename(columns={
        "away_team": "team", "away_score": "goals_for", "home_score": "goals_against",
    })[["season", "team", "goals_for", "goals_against"]]

    all_matches = pd.concat([home, away], ignore_index=True)
    all_matches["result"] = "draw"
    all_matches.loc[all_matches["goals_for"] > all_matches["goals_against"], "result"] = "win"
    all_matches.loc[all_matches["goals_for"] < all_matches["goals_against"], "result"] = "loss"

    stats = all_matches.groupby(["season", "team"]).agg(
        matches_played=("team", "count"),
        goals_for_avg=("goals_for", "mean"),
        goals_against_avg=("goals_against", "mean"),
        wins=("result", lambda s: (s == "win").sum()),
    ).reset_index()
    stats["win_pct"] = stats["wins"] / stats["matches_played"]

    return stats


def add_new_team_feature(stats: pd.DataFrame, historical: pd.DataFrame) -> pd.DataFrame:
    """Variable para prediccion de equipos nuevos: tiene_historial_reciente (1/0).

    Si el equipo no tiene historial reciente (no aparece en stats de la
    temporada actual), se usa como respaldo el promedio general de todos
    los equipos en la tabla historica.
    """
    stats = stats.copy()
    stats["tiene_historial_reciente"] = 1

    fallback_goals_for = historical["goals_for"].sum() / historical["matches_played"].sum()
    fallback_goals_against = historical["goals_against"].sum() / historical["matches_played"].sum()

    equipos_historicos = set(historical["team"])
    equipos_con_historial_reciente = set(stats["team"])
    equipos_sin_reciente = equipos_historicos - equipos_con_historial_reciente

    filas_respaldo = pd.DataFrame({
        "season": "sin_historial_reciente",
        "team": list(equipos_sin_reciente),
        "matches_played": 0,
        "goals_for_avg": fallback_goals_for,
        "goals_against_avg": fallback_goals_against,
        "wins": 0,
        "win_pct": None,
        "tiene_historial_reciente": 0,
    })

    return pd.concat([stats, filas_respaldo], ignore_index=True)


def main():
    logger.info("=== Inicio de procesamiento (Champions League) ===")

    api_raw = pd.read_csv(RAW_DIR / "champions_league_matches_api.csv")
    hist_raw = pd.read_csv(RAW_DIR / "UCL_AllTime_Performance_Table.csv")
    finals_raw = pd.read_csv(RAW_DIR / "UCL_Finals_1955-2023.csv")

    api_clean = clean_api_matches(api_raw)
    hist_manual_raw = pd.read_csv(RAW_DIR / "champions_league_matches_historico_manual.csv")
    hist_manual_clean = clean_api_matches(hist_manual_raw)
    api_clean = pd.concat([api_clean, hist_manual_clean], ignore_index=True)
    logger.info("Agregados %s partidos historicos manuales a api_clean", len(hist_manual_clean))
    hist_clean = clean_historical_performance(hist_raw)
    finals_clean = clean_finals(finals_raw)

    for df in (api_clean, hist_clean):
        for col in ("home_team", "away_team", "team"):
            if col in df.columns:
                df[col] = df[col].apply(standardize_team_name)

    api_clean.to_csv(INTERIM_DIR / "matches_clean.csv", index=False)
    hist_clean.to_csv(INTERIM_DIR / "historical_performance_clean.csv", index=False)
    finals_clean.to_csv(INTERIM_DIR / "finals_clean.csv", index=False)
    logger.info("Guardado en data/interim/champions_league/")

    team_season_stats = build_team_season_stats(api_clean)
    team_season_stats = add_new_team_feature(team_season_stats, hist_clean)

    for season, grupo in team_season_stats.groupby("season"):
        season_dir = PROCESSED_DIR / f"season={season}"
        season_dir.mkdir(parents=True, exist_ok=True)
        grupo.to_csv(season_dir / "team_stats.csv", index=False)
        logger.info("Guardado: %s (%s filas)", season_dir / "team_stats.csv", len(grupo))

    hist_clean.to_csv(PROCESSED_DIR / "historical_performance.csv", index=False)
    finals_clean.to_csv(PROCESSED_DIR / "finals.csv", index=False)

    logger.info("=== Fin de procesamiento ===")
    return team_season_stats


if __name__ == "__main__":
    resultado = main()
    print(resultado.head(20))
    print(resultado.shape)