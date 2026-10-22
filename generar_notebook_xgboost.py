import nbformat as nbf

nb = nbf.v4.new_notebook()
cells = []

cells.append(nbf.v4.new_markdown_cell(
"""# Check adicional: ¿puede el modelo predecir una temporada futura?

Este notebook responde una pregunta adicional pedida por el profesor,
separada del entregable principal (notebook 01): ¿un modelo entrenado con
estadisticas de equipo de temporadas pasadas de la Champions League puede
predecir razonablemente los resultados de una temporada siguiente?

**Version 2 (2026-10-22):** la primera version de este check solo tenia
una temporada de entrenamiento disponible (2023-24, por el limite del plan
gratuito de football-data.org a ~2 temporadas). Despues de que el equipo
extendio el historico manual a 5 temporadas completas (2021-22 a 2025-26,
ver `data/raw/README_historico_manual.md`), esta version usa una
**validacion "hacia adelante" (walk-forward)**: para predecir cada
temporada se entrena SOLO con las temporadas anteriores a ella (nunca con
datos del futuro), y el numero de temporadas de entrenamiento crece de 1 a
4 conforme avanzamos en el tiempo. Esto permite contestar directamente la
pregunta de cuantas temporadas de entrenamiento hacen falta."""
))

cells.append(nbf.v4.new_code_cell(
"""import sys
from pathlib import Path

ROOT = Path.cwd() if (Path.cwd() / "data").exists() else Path.cwd().parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import accuracy_score
from xgboost import XGBClassifier

matches = pd.read_csv(ROOT / "data/interim/champions_league/matches_clean.csv")
matches = matches[matches["status"] == "FINISHED"].copy()
matches["season"] = matches["season"].astype(int)

SEASONS = [2021, 2022, 2023, 2024, 2025]
matches = matches[matches["season"].isin(SEASONS)]
print("Partidos FINISHED en temporadas completas (2021-2025):", len(matches))
print(matches["season"].value_counts().sort_index())"""
))

cells.append(nbf.v4.new_code_cell(
'''def calcular_stats_equipo(df):
    """Promedio de goles a favor/en contra y win rate, por equipo, sobre los partidos de df."""
    filas = []
    for _, row in df.iterrows():
        filas.append({"team": row["home_team"], "goles_favor": row["home_score"], "goles_contra": row["away_score"],
                       "resultado": "W" if row["home_score"] > row["away_score"] else ("D" if row["home_score"] == row["away_score"] else "L")})
        filas.append({"team": row["away_team"], "goles_favor": row["away_score"], "goles_contra": row["home_score"],
                       "resultado": "W" if row["away_score"] > row["home_score"] else ("D" if row["away_score"] == row["home_score"] else "L")})
    largo = pd.DataFrame(filas)
    stats = largo.groupby("team").agg(
        partidos=("resultado", "count"),
        prom_goles_favor=("goles_favor", "mean"),
        prom_goles_contra=("goles_contra", "mean"),
    )
    stats["win_rate"] = largo.groupby("team")["resultado"].apply(lambda s: (s == "W").mean())
    return stats


def construir_features(df, stats_tabla, promedio_liga):
    """Features de cada partido usando una tabla de stats congelada (de temporadas anteriores)."""
    def stats_de(equipo):
        if equipo in stats_tabla.index:
            s = stats_tabla.loc[equipo]
            return s["prom_goles_favor"], s["prom_goles_contra"], s["win_rate"], 1
        return promedio_liga["prom_goles_favor"], promedio_liga["prom_goles_contra"], promedio_liga["win_rate"], 0

    filas = []
    for _, row in df.iterrows():
        gf_h, gc_h, wr_h, hist_h = stats_de(row["home_team"])
        gf_a, gc_a, wr_a, hist_a = stats_de(row["away_team"])
        if row["home_score"] > row["away_score"]:
            resultado = "H"
        elif row["home_score"] == row["away_score"]:
            resultado = "D"
        else:
            resultado = "A"
        filas.append({
            "home_prom_gf": gf_h, "home_prom_gc": gc_h, "home_win_rate": wr_h, "home_tiene_historial": hist_h,
            "away_prom_gf": gf_a, "away_prom_gc": gc_a, "away_win_rate": wr_a, "away_tiene_historial": hist_a,
            "resultado": resultado,
        })
    return pd.DataFrame(filas)

print("Funciones definidas")'''
))

cells.append(nbf.v4.new_code_cell(
'''features = ["home_prom_gf", "home_prom_gc", "home_win_rate", "home_tiene_historial",
            "away_prom_gf", "away_prom_gc", "away_win_rate", "away_tiene_historial"]
le = LabelEncoder()
le.fit(["A", "D", "H"])  # Away / Draw / Home, fijo entre folds para que las clases no cambien

resultados = []
for season_test in SEASONS[1:]:
    seasons_train = [s for s in SEASONS if s < season_test]
    df_train_raw = matches[matches["season"].isin(seasons_train)]
    df_test_raw = matches[matches["season"] == season_test]

    stats_train = calcular_stats_equipo(df_train_raw)
    promedio_liga = stats_train[["prom_goles_favor", "prom_goles_contra", "win_rate"]].mean()

    train_df = construir_features(df_train_raw, stats_train, promedio_liga)
    test_df = construir_features(df_test_raw, stats_train, promedio_liga)

    X_train, y_train = train_df[features], le.transform(train_df["resultado"])
    X_test, y_test = test_df[features], le.transform(test_df["resultado"])

    modelo = XGBClassifier(n_estimators=100, max_depth=3, learning_rate=0.1, random_state=42, eval_metric="mlogloss")
    modelo.fit(X_train, y_train)
    y_pred = modelo.predict(X_test)
    acc_modelo = accuracy_score(y_test, y_pred)

    clase_mas_comun = pd.Series(y_train).mode()[0]
    acc_baseline = accuracy_score(y_test, [clase_mas_comun] * len(y_test))

    partidos_sin_historial = int(((test_df["home_tiene_historial"] == 0) | (test_df["away_tiene_historial"] == 0)).sum())

    resultados.append({
        "temporada_prueba": season_test,
        "n_temporadas_entrenamiento": len(seasons_train),
        "partidos_entrenamiento": len(train_df),
        "partidos_prueba": len(test_df),
        "partidos_prueba_con_equipo_sin_historial": partidos_sin_historial,
        "accuracy_modelo": round(acc_modelo, 3),
        "accuracy_baseline": round(acc_baseline, 3),
    })

resumen = pd.DataFrame(resultados)
print(resumen.to_string(index=False))
print()
print("Promedio accuracy_modelo:", round(resumen["accuracy_modelo"].mean(), 3))
print("Promedio accuracy_baseline:", round(resumen["accuracy_baseline"].mean(), 3))'''
))

cells.append(nbf.v4.new_code_cell(
'''plt.figure(figsize=(7, 5))
plt.plot(resumen["n_temporadas_entrenamiento"], resumen["accuracy_modelo"], marker="o", color="#eb6834", linewidth=2, label="Modelo XGBoost")
plt.plot(resumen["n_temporadas_entrenamiento"], resumen["accuracy_baseline"], marker="o", color="#898781", linewidth=2, linestyle="--", label="Baseline (clase mas comun)")
for _, fila in resumen.iterrows():
    temporada = fila["temporada_prueba"]
    etiqueta = f"{temporada}-{temporada + 1}"
    plt.annotate(etiqueta, (fila["n_temporadas_entrenamiento"], fila["accuracy_modelo"]),
                 textcoords="offset points", xytext=(0, 10), ha="center", fontsize=8, color="#52514e")
plt.xlabel("Numero de temporadas de entrenamiento")
plt.ylabel("Exactitud (accuracy)")
plt.title("Exactitud vs. numero de temporadas de entrenamiento (validacion hacia adelante)")
plt.xticks(resumen["n_temporadas_entrenamiento"])
plt.ylim(0.30, 0.60)
plt.legend()
plt.grid(axis="y", color="#e1e0d9")
plt.tight_layout()
plt.savefig("../docs/xgboost_walkforward_accuracy.png", dpi=150)
plt.show()'''
))

cells.append(nbf.v4.new_markdown_cell(
"""## Conclusion del check adicional (version 2: validacion hacia adelante, 4 temporadas)

**Diseno:** en vez de una sola division entrenamiento/prueba, se hizo una
**validacion hacia adelante (walk-forward)** usando las 5 temporadas
completas que el equipo agrego al historico manual (2021-22 a 2025-26).
Para predecir cada temporada se entreno SOLO con las temporadas
estrictamente anteriores (nunca con datos del futuro), asi que el numero
de temporadas de entrenamiento crece de 1 (para predecir 2022-23) a 4
(para predecir 2025-26).

**Resultado principal:** el modelo le gano al baseline en 2 de los 4
cortes (con 2 y 3 temporadas de entrenamiento: 52.8% vs 48.8%, y 52.4% vs
50.8%) y quedo por debajo o empatado en los otros 2 (con 1 y 4 temporadas:
41.6% vs 42.4%, y 49.2% vs 49.7%). En promedio, el modelo queda apenas
arriba del baseline (49.0% vs 47.9%), pero **sin una tendencia clara y
consistente de "a mas temporadas de entrenamiento, mejor exactitud"**.

**Por que probablemente pasa esto:** en cada corte, una proporcion grande
de los partidos de prueba involucra al menos un equipo sin historial en
las temporadas de entrenamiento (entre 38% y 70% de los partidos, segun el
corte) — la Champions League tiene mucha rotacion de equipos clasificados
de un año a otro, y el cambio de formato de 2024-25 (32 a 36 equipos)
agrega aun mas equipos nuevos. Esto limita cuanto puede ayudar,
en la practica, simplemente agregar mas temporadas historicas sin tambien
mejorar como se maneja a los equipos sin historial reciente.

**Conclusion honesta:** con los datos disponibles, el modelo es
aproximadamente comparable al baseline mas simple, ni claramente mejor ni
claramente peor, y agregar temporadas de entrenamiento (de 1 a 4) no
produjo una mejora consistente. Esto es un hallazgo valido en si mismo:
sugiere que el cuello de botella no es la cantidad de temporadas
historicas, sino la alta rotacion de equipos participantes temporada a
temporada, algo que haria falta resolver con otro tipo de variables (por
ejemplo, fuerza del equipo basada en su liga local o en transferencias de
jugadores) en vez de solamente mas historial de la propia Champions
League."""
))

nb["cells"] = cells
nb["metadata"] = {
    "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
    "language_info": {"name": "python", "version": "3"},
}

with open("notebooks/02_modelo_prediccion_xgboost.ipynb", "w") as f:
    nbf.write(nb, f)

print("Notebook regenerado: notebooks/02_modelo_prediccion_xgboost.ipynb")
