"""
Modelo XGBoost de resultado (local / empate / visita) antes del partido.

Lleva a codigo el check del notebook 02_modelo_prediccion_xgboost.ipynb
para que la plataforma en vivo lo use como segunda opinion:
- Mismas variables: promedio de goles a favor y en contra y win rate de
  cada equipo, mas una bandera de si el equipo tiene historial.
- Mismos hiperparametros (100 arboles, profundidad 3, learning rate 0.1).
- Misma validacion hacia adelante (walk-forward) que el notebook: para
  predecir cada temporada completa se entrena solo con las anteriores, con
  estadisticas agregadas de esas temporadas, y se compara contra la linea
  base "la clase mas comun". Se guardan los resultados por corte y el
  promedio para mostrarlos junto a cada prediccion.

El modelo final se entrena con todos los partidos terminados (temporadas
completas mas lo jugado de la actual), con estadisticas agregadas de todos
ellos, igual que en cada corte de la validacion. Si un equipo no tiene
partidos se usa el promedio de la liga (bandera de historial = 0).

Entrada: data/interim/champions_league/matches_clean.csv
         (python -m src.processing.champions_league)
Salida:  data/live/modelo/xgboost_resultado.json   (modelo)
         data/live/modelo/xgboost_stats.csv        (estadisticas para predecir)
         data/live/modelo/xgboost_metricas.json    (validacion)

Ejecucion:
    python -m src.models.xgboost_resultado
"""

import json
import logging
import sys
from pathlib import Path

import pandas as pd
from sklearn.metrics import accuracy_score
from xgboost import XGBClassifier

from src.live.modelo import SNAPSHOT_DIR, find_team

ROOT_DIR = Path(__file__).resolve().parents[2]
MATCHES_CSV = ROOT_DIR / "data" / "interim" / "champions_league" / "matches_clean.csv"
MODELO_PATH = SNAPSHOT_DIR / "xgboost_resultado.json"
STATS_PATH = SNAPSHOT_DIR / "xgboost_stats.csv"
METRICAS_PATH = SNAPSHOT_DIR / "xgboost_metricas.json"

FEATURES = [
    "home_prom_gf", "home_prom_gc", "home_win_rate", "home_tiene_historial",
    "away_prom_gf", "away_prom_gc", "away_win_rate", "away_tiene_historial",
]
CLASES = ["A", "D", "H"]  # mismo orden que el LabelEncoder del notebook
# Temporadas completas del historico (las mismas que usa el notebook 02)
TEMPORADAS_COMPLETAS = [2021, 2022, 2023, 2024, 2025]
HIPERPARAMETROS = dict(n_estimators=100, max_depth=3, learning_rate=0.1, random_state=42, eval_metric="mlogloss")

logger = logging.getLogger(__name__)


def resultado(home_score, away_score):
    if home_score > away_score:
        return "H"
    return "D" if home_score == away_score else "A"


def calcular_stats_equipo(df: pd.DataFrame) -> pd.DataFrame:
    """Promedio de goles a favor, en contra y win rate por equipo (igual que el notebook)."""
    local = pd.DataFrame({
        "team": df["home_team"], "goles_favor": df["home_score"], "goles_contra": df["away_score"],
    })
    visita = pd.DataFrame({
        "team": df["away_team"], "goles_favor": df["away_score"], "goles_contra": df["home_score"],
    })
    largo = pd.concat([local, visita], ignore_index=True)
    largo["gano"] = largo["goles_favor"] > largo["goles_contra"]
    return largo.groupby("team").agg(
        partidos=("gano", "count"),
        prom_goles_favor=("goles_favor", "mean"),
        prom_goles_contra=("goles_contra", "mean"),
        win_rate=("gano", "mean"),
    )


def _fila_stats(equipo, stats, promedio):
    if equipo in stats.index:
        s = stats.loc[equipo]
        return s["prom_goles_favor"], s["prom_goles_contra"], s["win_rate"], 1
    return promedio["prom_goles_favor"], promedio["prom_goles_contra"], promedio["win_rate"], 0


def construir_features(df: pd.DataFrame, stats: pd.DataFrame, promedio: pd.Series) -> pd.DataFrame:
    filas = []
    for _, row in df.iterrows():
        gf_h, gc_h, wr_h, hist_h = _fila_stats(row["home_team"], stats, promedio)
        gf_a, gc_a, wr_a, hist_a = _fila_stats(row["away_team"], stats, promedio)
        filas.append({
            "home_prom_gf": gf_h, "home_prom_gc": gc_h, "home_win_rate": wr_h, "home_tiene_historial": hist_h,
            "away_prom_gf": gf_a, "away_prom_gc": gc_a, "away_win_rate": wr_a, "away_tiene_historial": hist_a,
            "resultado": resultado(row["home_score"], row["away_score"]),
        })
    return pd.DataFrame(filas)


def _codificar(serie):
    return serie.map({c: i for i, c in enumerate(CLASES)})


def unificar_nombres(partidos: pd.DataFrame) -> pd.DataFrame:
    """Un solo nombre por equipo en todas las temporadas.

    El historico manual (2023) y la API (2026) escriben distinto a los mismos
    equipos ("Bayern Munich" vs "FC Bayern München"); sin unificarlos el
    modelo cree que no tienen historial. Se usa el normalizador y los alias
    del pipeline de Polymarket (via find_team). Se conserva el nombre de la
    temporada mas reciente, que es como lo escribe la API en vivo.
    """
    canonicos, mapa = [], {}
    recientes_primero = partidos.sort_values("season", ascending=False)
    for nombre in pd.concat([recientes_primero["home_team"], recientes_primero["away_team"]]).drop_duplicates():
        igual = find_team(nombre, canonicos)
        if igual is None:
            canonicos.append(nombre)
            igual = nombre
        mapa[nombre] = igual
    unificados = sum(1 for k, v in mapa.items() if k != v)
    logger.info("Nombres de equipo unificados entre temporadas: %d", unificados)
    partidos = partidos.copy()
    partidos["home_team"] = partidos["home_team"].map(mapa)
    partidos["away_team"] = partidos["away_team"].map(mapa)
    return partidos


def cargar_partidos() -> pd.DataFrame:
    partidos = pd.read_csv(MATCHES_CSV)
    partidos = partidos[partidos["status"] == "FINISHED"].dropna(subset=["home_score", "away_score"])
    partidos["season"] = partidos["season"].astype(int)
    return unificar_nombres(partidos)


def _promedio(stats):
    return stats[["prom_goles_favor", "prom_goles_contra", "win_rate"]].mean()


def validar(partidos: pd.DataFrame) -> dict:
    """Validacion hacia adelante sobre las temporadas completas (como el notebook 02)."""
    completas = [t for t in TEMPORADAS_COMPLETAS if t in set(partidos["season"])]
    cortes = []
    for t_test in completas[1:]:
        train = partidos[partidos["season"].isin([t for t in completas if t < t_test])]
        test = partidos[partidos["season"] == t_test]
        stats = calcular_stats_equipo(train)
        x_train = construir_features(train, stats, _promedio(stats))
        x_test = construir_features(test, stats, _promedio(stats))
        modelo = XGBClassifier(**HIPERPARAMETROS)
        modelo.fit(x_train[FEATURES], _codificar(x_train["resultado"]))
        y_test = _codificar(x_test["resultado"])
        clase_comun = _codificar(x_train["resultado"]).mode()[0]
        cortes.append({
            "temporada_prueba": int(t_test),
            "temporadas_entrenamiento": int(len(train["season"].unique())),
            "partidos_prueba": int(len(test)),
            "exactitud": float(accuracy_score(y_test, modelo.predict(x_test[FEATURES]))),
            "exactitud_linea_base": float(accuracy_score(y_test, [clase_comun] * len(y_test))),
            "linea_base": f"siempre '{CLASES[clase_comun]}'",
        })
    if not cortes:
        return {}
    return {
        "diseno": "walk-forward (igual que notebooks/02_modelo_prediccion_xgboost.ipynb)",
        "cortes": cortes,
        "exactitud": sum(c["exactitud"] for c in cortes) / len(cortes),
        "exactitud_linea_base": sum(c["exactitud_linea_base"] for c in cortes) / len(cortes),
        "linea_base": "la clase más común",
        "cortes_que_superan_linea_base": sum(c["exactitud"] > c["exactitud_linea_base"] for c in cortes),
    }


def entrenar():
    partidos = cargar_partidos()
    metricas = validar(partidos)
    logger.info("Validacion: %s", metricas)

    # Modelo final: todos los partidos terminados con estadisticas agregadas (como cada corte)
    stats = calcular_stats_equipo(partidos)
    datos = construir_features(partidos, stats, _promedio(stats))
    modelo = XGBClassifier(**HIPERPARAMETROS)
    modelo.fit(datos[FEATURES], _codificar(datos["resultado"]))

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    modelo.save_model(MODELO_PATH)
    stats.reset_index().to_csv(STATS_PATH, index=False)
    metricas["partidos_modelo_final"] = int(len(datos))
    METRICAS_PATH.write_text(json.dumps(metricas, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Modelo guardado en %s (%d partidos)", MODELO_PATH, len(datos))
    return metricas


class PrediccionResultado:
    """Carga el modelo guardado y predice P(local), P(empate), P(visita) antes del partido."""

    def __init__(self):
        self.disponible = MODELO_PATH.exists() and STATS_PATH.exists()
        self.metricas = {}
        if not self.disponible:
            logger.warning("No hay modelo XGBoost guardado; corre python -m src.models.xgboost_resultado")
            return
        self.modelo = XGBClassifier()
        self.modelo.load_model(MODELO_PATH)
        self.stats = pd.read_csv(STATS_PATH).set_index("team")
        self.promedio = self.stats[["prom_goles_favor", "prom_goles_contra", "win_rate"]].mean()
        if METRICAS_PATH.exists():
            self.metricas = json.loads(METRICAS_PATH.read_text(encoding="utf-8"))
        self._cache = {}

    def predict(self, home: str, away: str):
        """{"home", "draw", "away", "historial": (local, visita)} o None si no hay modelo."""
        if not self.disponible:
            return None
        if (home, away) in self._cache:
            return self._cache[(home, away)]
        equipos = self.stats.index
        nombre_h, nombre_a = find_team(home, equipos), find_team(away, equipos)
        gf_h, gc_h, wr_h, hist_h = _fila_stats(nombre_h, self.stats, self.promedio)
        gf_a, gc_a, wr_a, hist_a = _fila_stats(nombre_a, self.stats, self.promedio)
        x = pd.DataFrame([[gf_h, gc_h, wr_h, hist_h, gf_a, gc_a, wr_a, hist_a]], columns=FEATURES)
        proba = dict(zip(CLASES, self.modelo.predict_proba(x)[0]))
        salida = {
            "home": float(proba["H"]), "draw": float(proba["D"]), "away": float(proba["A"]),
            "historial": (bool(hist_h), bool(hist_a)),
        }
        self._cache[(home, away)] = salida
        return salida


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    print(json.dumps(entrenar(), indent=2, ensure_ascii=False))
