# Data lake en Databricks

Este documento es la evidencia de que `databricks/datalake.py` corrió exitosamente en
un workspace real de Databricks (Free Edition), construyendo el data lake del proyecto
en capas bronze/silver/gold sobre Delta Lake.

## Qué hace el notebook

`databricks/datalake.py` reutiliza sin modificar el código real del proyecto
(`src/processing/champions_league.py` y `src/processing/polymarket.py`). No reescribe
la lógica de limpieza: solo cambia DÓNDE se guarda el resultado final (tablas Delta en
vez de CSV), siguiendo las mismas 3 capas que ya usa el proyecto (raw -> interim ->
processed), aquí llamadas bronze -> silver -> gold.

Pasos para reproducirlo:
1. En Databricks: Repos (Git folders) -> Add Repo -> pegar la URL de GitHub del
   proyecto (https://github.com/0260932/datos-masivos-champions-league).
2. Subir los archivos de data/raw/ que no están en el repo
   (champions_league_matches_api.csv, UCL_AllTime_Performance_Table.csv,
   UCL_Finals_1955-2023.csv, polymarket_events_raw.json, polymarket_markets.csv,
   polymarket_prices_history.csv) a un Volume de Unity Catalog.
3. Editar REPO_ROOT y VOLUME_RAW en la celda de configuración con las rutas reales
   del workspace.
4. Ejecutar el notebook celda por celda.

## Resultado real de la ejecución

Esquema creado: champions_league. El notebook generó 19 tablas Delta:

- bronze (7 tablas): datos crudos tal cual se descargaron
  - bronze_cl_matches_api
  - bronze_cl_historical_performance
  - bronze_cl_finals
  - bronze_cl_matches_historico_manual
  - bronze_polymarket_markets
  - bronze_polymarket_prices_history
  - bronze_polymarket_events_raw
- silver (5 tablas): datos limpios, misma lógica de src/processing/, sin tocarla
  - silver_cl_matches_clean
  - silver_cl_historical_performance_clean
  - silver_cl_finals_clean
  - silver_polymarket_mercados_limpios
  - silver_polymarket_historial_precios
- gold (7 tablas): datos listos para análisis, particionados por temporada o tipo
  de mercado
  - gold_cl_team_stats
  - gold_cl_historical_performance
  - gold_cl_finals
  - gold_polymarket_mercados
  - gold_polymarket_movimientos_significativos
  - gold_polymarket_referencia_equipos
  - gold_polymarket_resumen_eventos

Salida real de display(spark.sql("SHOW TABLES IN champions_league")):

database          tableName                                      isTemporary
champions_league  bronze_cl_finals                                false
champions_league  bronze_cl_historical_performance                false
champions_league  bronze_cl_matches_api                           false
champions_league  bronze_cl_matches_historico_manual              false
champions_league  bronze_polymarket_events_raw                    false
champions_league  bronze_polymarket_markets                       false
champions_league  bronze_polymarket_prices_history                false
champions_league  gold_cl_finals                                  false
champions_league  gold_cl_historical_performance                  false
champions_league  gold_cl_team_stats                              false
champions_league  gold_polymarket_mercados                        false
champions_league  gold_polymarket_movimientos_significativos      false
champions_league  gold_polymarket_referencia_equipos              false
champions_league  gold_polymarket_resumen_eventos                 false
champions_league  silver_cl_finals_clean                          false
champions_league  silver_cl_historical_performance_clean          false
champions_league  silver_cl_matches_clean                         false
champions_league  silver_polymarket_historial_precios             false
champions_league  silver_polymarket_mercados_limpios              false

## Chequeo de conteo de filas (sanidad bronze -> silver -> gold)

bronze_cl_matches_api:    144 filas
silver_cl_matches_clean:  897 filas
gold_cl_team_stats:       505 filas
gold_polymarket_mercados: 180 filas

Estos números coinciden con los que arroja el pipeline local (ver docs/validation.md
y la salida de python -m src.processing.champions_league /
python -m src.processing.polymarket), confirmando que la ejecución en Databricks
reproduce exactamente los mismos resultados que el pipeline original, solo que
guardados como tablas Delta en vez de CSV.

## Nota sobre acceso

Este workspace de Databricks es una cuenta individual (Free Edition), no un workspace
compartido con el equipo ni con el profesor. Por eso la evidencia de ejecución se deja
documentada aquí, dentro del repo, en vez de depender de un link al workspace.
