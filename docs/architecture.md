# Arquitectura del proyecto (estado al cierre de la Entrega 2)

Este documento describe la arquitectura **real** del repositorio al terminar la
Entrega 2 (issues #4 a #7), no la planeada. Para el porqué de cada decisión, ver
`docs/decisions.md`; para la calidad de los datos y la comparación de formatos,
ver `docs/validation.md`.

## Diagrama general

```
FUENTES                          INGESTIÓN (src/ingestion/)              CAPA RAW
─────────────────────────────    ─────────────────────────────────────   ──────────────────────────────────────────
API football-data.org  ───────▶  extract_api_football_data.py  ─┐
  (temporada actual, token)                                      ├─▶ download_data.py ─▶ data/raw/champions_league_matches_api.csv
Kaggle (finales e histórico ────▶  extract_kaggle_historical.py ─┘                       data/raw/UCL_Finals_1955-2023.csv
  1955-2023, usuario + key)                                                              data/raw/UCL_AllTime_Performance_Table.csv
RSSSF, búsqueda asistida ────────▶  scripts_build_historico.py (manual) ──────────────▶  data/raw/champions_league_matches_historico_manual.csv
  por IA (temporada 2022-23)                                                              (único archivo de raw versionado en git)
Polymarket Gamma API ────────────▶  download_polymarket.py  ──────────────────────────▶  data/raw/polymarket_events_raw.json
  (pública, sin credenciales)                                                            data/raw/polymarket_markets.csv
Polymarket CLOB /prices-history ─▶  (lo pide polymarket.py y lo guarda como caché) ───▶  data/raw/polymarket_prices_history.csv


PROCESAMIENTO (src/processing/)                CAPA INTERIM (limpia)                        CAPA PROCESSED (lista para análisis)
───────────────────────────────────────        ──────────────────────────────────────────   ─────────────────────────────────────────────────────
champions_league.py  (issue #5)  ───────────▶  data/interim/champions_league/           ─▶  data/processed/champions_league/
  limpieza, reconstrucción de goles,             matches_clean.csv                            season=2023/team_stats.csv
  estadísticas por equipo y temporada            historical_performance_clean.csv             season=2026/team_stats.csv
                                                 finals_clean.csv                             season=sin_historial_reciente/team_stats.csv
                                                                                              historical_performance.csv, finals.csv

polymarket.py  (issue #6)  ─────────────────▶  data/interim/polymarket/                 ─▶  data/processed/polymarket/
  limpieza de mercados e historial,              mercados_limpios.csv                         tipo_mercado=<tipo>/mercados.csv (5 particiones)
  movimientos significativos,                    historial_precios.csv                        resumen_eventos.csv
  referencia histórica (académica)                                                            movimientos_significativos.csv
                                                                                              referencia_equipos.csv


VALIDACIÓN Y PRUEBAS (issue #7)                                      CONSUMO
───────────────────────────────────────────────────────────────     ──────────────────────────────────────────────
data/interim/ + data/processed/ ─▶ src/validation/run_validations.py ─▶ reporte en terminal ─▶ docs/validation.md
src/ (ingestión, procesamiento, validación) ─▶ tests/ (pytest, datos de ejemplo fijos)
data/processed/ ─▶ notebooks/01_extraccion_y_diagnostico.ipynb, notebooks/02_modelo_prediccion_xgboost.ipynb
```

## Capas de datos

| Capa | Carpeta | Contenido | ¿En git? |
|---|---|---|---|
| Raw | `data/raw/` | Copias crudas tal como llegan de cada fuente, sin modificar | No (excepto el CSV histórico manual y su README) |
| Interim | `data/interim/<fuente>/` | Datos limpios: tipos corregidos, sin duplicados, faltantes marcados | No |
| Processed | `data/processed/<fuente>/` | Variables y agregaciones listas para análisis, particionadas | No |

Las carpetas de datos se regeneran ejecutando el pipeline (ver "Cómo correr todo").
Todos los archivos se guardan en **CSV**; la comparación contra Parquet está en
`docs/validation.md`.

**Particionamiento:** Champions League se particiona por temporada
(`season=<año>`) y Polymarket por tipo de mercado (`tipo_mercado=<tipo>`), con el
formato `columna=valor` que entienden herramientas como pandas/pyarrow, Spark o
DuckDB.

## Módulos

| Módulo | Responsable | Qué hace |
|---|---|---|
| `src/ingestion/download_data.py` | Integrante 1 (#4) | Orquesta la descarga de Champions League (API + Kaggle); omite la descarga si ya hay copia de hoy (`--force` para forzar) |
| `src/ingestion/extract_api_football_data.py` | Integrante 1 (#4) | Pide los partidos de la competición `CL` y los aplana a una tabla |
| `src/ingestion/extract_kaggle_historical.py` | Integrante 1 (#4) | Descarga el dataset histórico de Kaggle |
| `src/ingestion/download_polymarket.py` | Integrante 1 (#4) | Busca eventos de "Champions League" en Polymarket y aplana sus mercados |
| `scripts_build_historico.py` | Integrante 2 (#5) | Genera el CSV de la temporada 2022-23 a partir de RSSSF (se corre una sola vez) |
| `src/processing/champions_league.py` | Integrante 2 (#5) | raw → interim → processed de Champions League |
| `src/processing/polymarket.py` | Integrante 3 (#6) | raw → interim → processed de Polymarket; descarga y cachea el historial de precios |
| `src/validation/validators.py` | Integrante 4 (#7) | Funciones reutilizables de validación (devuelven OK/FALLA con detalle) |
| `src/validation/run_validations.py` | Integrante 4 (#7) | Corre 76 validaciones sobre ambas fuentes y el cruce entre ellas |
| `tests/` | Integrante 4 (#7) | 28 pruebas con pytest para ingestión, procesamiento y validación |

## Cómo correr todo

Desde la raíz del repositorio, con el entorno virtual activo y el `.env` configurado:

```bash
# 1. Ingestión (data/raw/)
python -m src.ingestion.download_data
python -m src.ingestion.download_polymarket

# 2. Procesamiento (data/interim/ y data/processed/)
python -m src.processing.champions_league
python -m src.processing.polymarket            # --refrescar-historial para volver a pedir precios

# 3. Validación de calidad (reporte en terminal)
python -m src.validation.run_validations

# 4. Pruebas automáticas
python -m pytest -v
```

El orden importa: `polymarket.py` usa los CSV de Kaggle que descarga
`download_data`, y las validaciones leen lo que generan los dos pipelines. Las
pruebas automáticas no dependen de los datos descargados.

## Configuración, credenciales y logs

- **Credenciales:** `FOOTBALL_DATA_API_TOKEN`, `KAGGLE_USERNAME` y `KAGGLE_KEY` en
  un archivo `.env` (plantilla en `.env.example`, ignorado por git). Polymarket no
  requiere credenciales.
- **Rutas:** todos los módulos calculan sus rutas relativas a la raíz del repo, así
  que funcionan igual en Windows, macOS y Linux.
- **Logs** (`logs/`, ignorado por git): `ingestion.log` (ambas descargas) y
  `processing_champions_league.log`. El pipeline de Polymarket registra solo en
  la terminal.

## Cambios respecto a lo planeado

- **Temporadas históricas:** se agregó una temporada (2022-23) en lugar de 3 a 5,
  para priorizar datos verificados (ver `docs/decisions.md`).
- **Historial de precios de Polymarket:** no estaba en el plan de ingestión; lo
  descarga el propio pipeline de procesamiento y lo guarda en `data/raw/` como
  caché, para no modificar el script de ingestión.
- **Formato:** se mantuvo CSV en todas las capas. La medición del Paso 9 muestra
  que Parquet sería más rápido y ligero, pero con el volumen actual CSV es
  suficiente (ver `docs/validation.md`).
- **Limitaciones conocidas** detectadas por las validaciones (convención de
  `season` mezclada, nombres de equipos sin unificar entre la API y Kaggle, evento
  "2nd Place" sin clasificar, entre otras): documentadas con su corrección sugerida
  en `docs/validation.md`.
