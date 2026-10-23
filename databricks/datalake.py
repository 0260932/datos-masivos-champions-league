# Databricks notebook source
# MAGIC %md
# MAGIC # Data lake del proyecto Champions League & Polymarket en Databricks
# MAGIC
# MAGIC Reusa el código real del repo (sin reescribirlo): `src/processing/champions_league.py`
# MAGIC y `src/processing/polymarket.py`. Este notebook solo cambia DÓNDE se guarda el
# MAGIC resultado final (tablas Delta en vez de CSV), siguiendo las mismas 3 capas que ya
# MAGIC usa el proyecto (raw -> interim -> processed), aquí llamadas bronze -> silver -> gold.
# MAGIC
# MAGIC **Antes de correr este notebook:**
# MAGIC 1. En Databricks: Repos -> Add Repo -> pega la URL de GitHub del proyecto
# MAGIC    (`https://github.com/0260932/datos-masivos-champions-league`).
# MAGIC 2. Sube tus archivos de `data/raw/` (los que tienes localmente: `champions_league_matches_api.csv`,
# MAGIC    `UCL_AllTime_Performance_Table.csv`, `UCL_Finals_1955-2023.csv`, `polymarket_events_raw.json`,
# MAGIC    `polymarket_markets.csv`, `polymarket_prices_history.csv`) a un Volume de Unity Catalog
# MAGIC    (Catalog Explorer -> tu catálogo -> Create Volume -> Upload). `champions_league_matches_historico_manual.csv`
# MAGIC    ya está en el repo, no hace falta subirlo.
# MAGIC 3. Edita las 2 variables de la celda siguiente con tus rutas reales.

# COMMAND ----------

REPO_ROOT = "/Workspace/Repos/TU_USUARIO/datos-masivos-champions-league"  # <-- cámbialo (breadcrumb de Repos)
VOLUME_RAW = "/Volumes/workspace/default/champions_league_raw"  # <-- cámbialo (donde subiste los CSV crudos)
ESQUEMA = "champions_league"  # esquema donde se crean las tablas bronze/silver/gold

# COMMAND ----------

# MAGIC %pip install pandas numpy requests -q

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import sys
import shutil
from pathlib import Path

REPO_ROOT = "/Workspace/Repos/TU_USUARIO/datos-masivos-champions-league"  # se repite: restartPython limpia variables
VOLUME_RAW = "/Volumes/workspace/default/champions_league_raw"
ESQUEMA = "champions_league"

sys.path.insert(0, REPO_ROOT)

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {ESQUEMA}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Bronze: copiar los archivos crudos al repo y registrarlos como tablas Delta tal cual llegaron

# COMMAND ----------

RAW_DIR = Path(REPO_ROOT) / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

archivos_crudos = [
    "champions_league_matches_api.csv",
    "UCL_AllTime_Performance_Table.csv",
    "UCL_Finals_1955-2023.csv",
    "polymarket_markets.csv",
    "polymarket_prices_history.csv",
]
for nombre in archivos_crudos:
    origen = Path(VOLUME_RAW) / nombre
    destino = RAW_DIR / nombre
    if origen.exists():
        shutil.copy(origen, destino)
        print(f"Copiado: {nombre}")
    else:
        print(f"AVISO: no se encontró {origen} — súbelo al Volume antes de continuar")

# el JSON de eventos de Polymarket se copia aparte (no es tabular)
origen_json = Path(VOLUME_RAW) / "polymarket_events_raw.json"
if origen_json.exists():
    shutil.copy(origen_json, RAW_DIR / "polymarket_events_raw.json")
    print("Copiado: polymarket_events_raw.json")

# COMMAND ----------

import pandas as pd

def csv_a_delta_bronze(nombre_csv: str, nombre_tabla: str):
    df = pd.read_csv(RAW_DIR / nombre_csv)
    spark.createDataFrame(df.astype(str)).write.format("delta").mode("overwrite") \
        .saveAsTable(f"{ESQUEMA}.bronze_{nombre_tabla}")
    print(f"{ESQUEMA}.bronze_{nombre_tabla}: {len(df)} filas")

csv_a_delta_bronze("champions_league_matches_api.csv", "cl_matches_api")
csv_a_delta_bronze("UCL_AllTime_Performance_Table.csv", "cl_historical_performance")
csv_a_delta_bronze("UCL_Finals_1955-2023.csv", "cl_finals")
csv_a_delta_bronze("champions_league_matches_historico_manual.csv", "cl_matches_historico_manual")
csv_a_delta_bronze("polymarket_markets.csv", "polymarket_markets")
csv_a_delta_bronze("polymarket_prices_history.csv", "polymarket_prices_history")

# el JSON de eventos se guarda como texto crudo (bronze = tal cual llegó, sin parsear)
if (RAW_DIR / "polymarket_events_raw.json").exists():
    texto_json = (RAW_DIR / "polymarket_events_raw.json").read_text(encoding="utf-8")
    spark.createDataFrame([(texto_json,)], ["json_crudo"]).write.format("delta").mode("overwrite") \
        .saveAsTable(f"{ESQUEMA}.bronze_polymarket_events_raw")
    print(f"{ESQUEMA}.bronze_polymarket_events_raw: 1 fila (JSON completo)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Silver + Gold: correr la limpieza real del proyecto (sin modificarla) y registrar los resultados como Delta

# COMMAND ----------

from src.processing import champions_league, polymarket

resultado_cl = champions_league.main()       # corre la limpieza real -> escribe data/interim y data/processed (CSV, dentro del repo)
polymarket.main([])                           # corre la limpieza real de Polymarket -> misma lógica

# COMMAND ----------

def csv_a_delta(path_csv: Path, nombre_tabla: str, columna_extra: dict | None = None):
    """columna_extra: {nombre_columna: valor} para agregar una columna fija (ej. season, tipo_mercado)
    cuando el CSV viene de una carpeta particionada (season=XXXX/, tipo_mercado=XXXX/)."""
    df = pd.read_csv(path_csv)
    if columna_extra:
        for col, val in columna_extra.items():
            df[col] = val
    sdf = spark.createDataFrame(df.astype(str))
    (sdf.write.format("delta").mode("append").saveAsTable(f"{ESQUEMA}.{nombre_tabla}"))

INTERIM_CL = Path(REPO_ROOT) / "data" / "interim" / "champions_league"
INTERIM_PM = Path(REPO_ROOT) / "data" / "interim" / "polymarket"
PROCESSED_CL = Path(REPO_ROOT) / "data" / "processed" / "champions_league"
PROCESSED_PM = Path(REPO_ROOT) / "data" / "processed" / "polymarket"

# --- Silver (interim: limpio, sin agregaciones) ---
for esquema_tabla, carpeta in [
    ("silver_cl_matches_clean", INTERIM_CL / "matches_clean.csv"),
    ("silver_cl_historical_performance_clean", INTERIM_CL / "historical_performance_clean.csv"),
    ("silver_cl_finals_clean", INTERIM_CL / "finals_clean.csv"),
    ("silver_polymarket_mercados_limpios", INTERIM_PM / "mercados_limpios.csv"),
    ("silver_polymarket_historial_precios", INTERIM_PM / "historial_precios.csv"),
]:
    spark.sql(f"DROP TABLE IF EXISTS {ESQUEMA}.{esquema_tabla}")
    csv_a_delta(carpeta, esquema_tabla)
    print(f"{ESQUEMA}.{esquema_tabla} listo")

# --- Gold (processed: listo para análisis, particionado) ---
spark.sql(f"DROP TABLE IF EXISTS {ESQUEMA}.gold_cl_team_stats")
for carpeta_season in sorted(PROCESSED_CL.glob("season=*")):
    season = carpeta_season.name.split("=", 1)[1]
    csv_a_delta(carpeta_season / "team_stats.csv", "gold_cl_team_stats", columna_extra={"season": season})
print(f"{ESQUEMA}.gold_cl_team_stats listo (todas las temporadas)")

spark.sql(f"DROP TABLE IF EXISTS {ESQUEMA}.gold_polymarket_mercados")
for carpeta_tipo in sorted(PROCESSED_PM.glob("tipo_mercado=*")):
    tipo = carpeta_tipo.name.split("=", 1)[1]
    csv_a_delta(carpeta_tipo / "mercados.csv", "gold_polymarket_mercados", columna_extra={"tipo_mercado": tipo})
print(f"{ESQUEMA}.gold_polymarket_mercados listo (todos los tipos de mercado)")

for esquema_tabla, archivo in [
    ("gold_cl_historical_performance", PROCESSED_CL / "historical_performance.csv"),
    ("gold_cl_finals", PROCESSED_CL / "finals.csv"),
    ("gold_polymarket_resumen_eventos", PROCESSED_PM / "resumen_eventos.csv"),
    ("gold_polymarket_movimientos_significativos", PROCESSED_PM / "movimientos_significativos.csv"),
    ("gold_polymarket_referencia_equipos", PROCESSED_PM / "referencia_equipos.csv"),
]:
    spark.sql(f"DROP TABLE IF EXISTS {ESQUEMA}.{esquema_tabla}")
    csv_a_delta(archivo, esquema_tabla)
    print(f"{ESQUEMA}.{esquema_tabla} listo")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Verificación: listar todas las tablas del data lake

# COMMAND ----------

display(spark.sql(f"SHOW TABLES IN {ESQUEMA}"))

# COMMAND ----------

# MAGIC %md
# MAGIC Capas resultantes:
# MAGIC - **bronze**: datos crudos tal cual se descargaron (7 tablas)
# MAGIC - **silver**: datos limpios, sin agregar (5 tablas) — misma lógica de `src/processing/`, sin tocarla
# MAGIC - **gold**: datos listos para análisis, particionados por temporada o tipo de mercado (7 tablas)
