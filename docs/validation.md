# Validación de calidad de datos y desempeño (Entrega 2, Integrante 4)

Este documento resume las validaciones automáticas que se corren sobre los datos
procesados de las dos fuentes del proyecto (Champions League y Polymarket), los
resultados reales que obtuvimos y qué significa cada falla.

- **Código:** `src/validation/validators.py` (funciones reutilizables) y
  `src/validation/run_validations.py` (corre todas las validaciones).
- **Cómo correrlas:** `python -m src.validation.run_validations`
- **Fecha de la corrida documentada:** 21 de octubre de 2026, con datos descargados
  ese mismo día (`download_data`, `download_polymarket`) y procesados con los
  pipelines de los issues #5 y #6.

## Resultado general

**76 validaciones: 70 pasaron y 6 fallaron.** Ninguna falla viene de un error del
código de validación: las seis son problemas reales de los datos o de los
pipelines, y se documentan abajo como limitaciones conocidas.

| Dataset | Validaciones | Pasaron | Fallaron |
|---|---|---|---|
| CL · `interim/matches_clean.csv` | 13 | 12 | 1 |
| CL · `processed/historical_performance.csv` | 15 | 14 | 1 |
| CL · `processed/finals.csv` | 6 | 5 | 1 |
| CL · `processed/season=*/team_stats.csv` | 9 | 9 | 0 |
| PM · `processed/tipo_mercado=*/mercados.csv` | 13 | 13 | 0 |
| PM · `processed/resumen_eventos.csv` | 2 | 1 | 1 |
| PM · `interim/historial_precios.csv` | 7 | 7 | 0 |
| PM · `processed/movimientos_significativos.csv` | 2 | 2 | 0 |
| PM · `processed/referencia_equipos.csv` | 5 | 4 | 1 |
| Cruce entre fuentes | 4 | 3 | 1 |
| **Total** | **76** | **70** | **6** |

## Qué se validó

Las funciones de `validators.py` cubren seis tipos de validación. Los parámetros
(columnas, rangos y volúmenes) se fijaron después de explorar los datos reales, no
con valores genéricos.

| Tipo | Función | Ejemplos de uso en este proyecto |
|---|---|---|
| Estructura | `validate_required_columns`, `validate_dtypes` | columnas obligatorias de cada dataset; `match_id` entero, `prob_implicita` decimal |
| Volumen | `validate_expected_volume` | partidos entre 100 y 1,000; tabla histórica entre 300 y 450 equipos; ≥1,000 puntos de historial |
| Completitud y unicidad | `validate_no_missing`, `validate_no_duplicates` | sin `match_id` ni `market_id` repetidos; sin temporadas repetidas en finales |
| Rangos y reglas | `validate_value_ranges`, `validate_allowed_values`, `validate_row_rule` | goles entre 0 y 15; precios entre 0 y 1; `wins + draws + losses = matches_played` |
| Consistencia temporal | `validate_temporal_consistency` | ningún partido FINISHED con fecha futura; historial de Polymarket entre 2026-01-01 y hoy |
| Consistencia entre tablas/columnas | `validate_cross_table_consistency`, `validate_outcome_prices_sum`, `validate_group_sum` | precios "Yes" + "No" suman 1; suma de probabilidades por evento vs. número de ganadores; mismos equipos en Champions League y Polymarket |

## Validaciones que pasaron (lo más relevante)

- **Partidos:** 269 partidos (144 de la temporada 2026-27 + 125 de 2022-23), sin
  `match_id` repetidos. Los 90 marcadores vacíos corresponden todos a partidos
  `TIMED` (programados, aún no jugados); ningún partido `FINISHED` tiene marcador
  vacío ni fecha futura. Goles entre 0 y 7.
- **Tabla histórica:** la reconstrucción de goles que hizo el Integrante 2 cuadra:
  `goals_for - goals_against = goal_difference` en los 354 equipos, y en el total
  histórico los goles a favor son iguales a los goles en contra (y las victorias a
  las derrotas), como debe ser en un torneo donde cada gol de un equipo lo recibe otro.
- **Estadísticas por equipo:** `win_pct` entre 0 y 1, `wins ≤ matches_played`, y
  `win_pct` vacío únicamente en los 323 equipos marcados sin historial reciente
  (es el comportamiento esperado).
- **Mercados de Polymarket:** 180 mercados sin duplicados, todos activos y abiertos.
  En los 180, el precio de "Yes" más el de "No" suma exactamente 1.0000. Los
  `best_bid` vacíos coinciden exactamente con la marca `sin_ofertas_compra`.
- **Historial de precios:** 8,941 puntos, un punto por mercado y día, fechas del
  3 de julio al 21 de octubre de 2026, precios entre 0 y 1. Los 187 movimientos
  significativos cumplen `|cambio_pp| ≥ umbral_pp` y ninguno cae en los días de
  apertura excluidos.
- **Cruce entre fuentes:** los **36 equipos** de la temporada actual de Champions
  League coinciden con los 36 equipos de los mercados de Polymarket (100%), usando
  la normalización y tabla de alias de `polymarket.py`.

## Validaciones que fallaron y qué significan

### 1. Convención de `season` mezclada (matches_clean)

**Resultado:** 125 de 269 partidos no cumplen *season = año de inicio de la
temporada*. Son los 125 partidos de 2022-23 agregados a mano: están etiquetados
como `2023` y sus fechas son de 2022 (por ejemplo, 7 de septiembre de 2022).

**Qué significa:** la API de football-data.org identifica la temporada por el año
en que empieza (2026-27 → `2026`), mientras que el archivo histórico manual usa el
año en que termina (2022-23 → `2023`). Con las dos convenciones en la misma
columna, una comparación entre temporadas por `season` queda corrida un año. Por
eso la partición se llama `season=2023` cuando debería ser `season=2022` para ser
consistente con la API.

**Estado:** documentado como limitación conocida. Corrección sugerida: cambiar a
`2022` la columna `season` en `data/raw/champions_league_matches_historico_manual.csv`
(o derivar `season` de la fecha del primer partido en `champions_league.py`).

### 2. Partidos que no cuadran en la tabla histórica (historical_performance)

**Resultado:** 2 de 354 equipos no cumplen `wins + draws + losses = matches_played`:

| Equipo | Partidos | V | E | D | V + E + D |
|---|---|---|---|---|---|
| Linfield FC | 40 | 5 | 11 | 23 | 39 |
| 1. FC Frankfurt (Oder) | 21 | 7 | 1 | 12 | 20 |

**Qué significa:** en ambos casos falta exactamente un partido. El error viene de
la fuente de Kaggle (no del procesamiento): los demás 352 equipos cuadran y los
totales de goles siguen siendo consistentes. Una posible explicación es un
partido anulado o resuelto en la mesa que se contó como jugado sin resultado.

**Estado:** documentado como limitación conocida. El impacto es mínimo (2 equipos
con poco peso histórico); no se corrigió para no inventar un resultado.

### 3. Final de 1973-74 repetida (finals)

**Resultado:** la temporada `1973–74` aparece en 2 filas.

**Qué significa:** esa final se jugó dos veces (empate 1-1 y desempate 4-0), y la
fuente registra ambos partidos. El pipeline de Polymarket ya consolida este caso
al contar títulos, pero `processed/champions_league/finals.csv` conserva las dos
filas, así que contar finales por fila daría 69 en lugar de 68 temporadas.

**Estado:** documentado. Quien use `finals.csv` para contar títulos debe quedarse
con la última fila de cada temporada (el desempate), como hace `polymarket.py`.

### 4. Evento "2nd Place" sin número esperado de ganadores (resumen_eventos)

**Resultado:** el evento *UEFA Champions League: 2027 2nd Place* quedó con
`tipo_mercado = otro` y sin `ganadores_esperados`, con una suma de
probabilidades de **1.4295**. Los otros cuatro eventos sí pasan la tolerancia
del 10%: campeón 1.056 (esperado 1), top 8 8.634 (esperado 8), octavos 15.47
(esperado 16) y puestos 9-24 16.27 (esperado 16).

**Qué significa:** la lista `TIPOS_MERCADO` de `polymarket.py` no incluye el evento
de subcampeón (la búsqueda de Polymarket lo devolvió, y no devolvió el evento
"League Phase Winner" que sí está en la lista). Debería clasificarse con 1 ganador
esperado. Aun así, una suma de 1.43 para un evento con un solo ganador indica
precios poco coherentes: es un mercado con muy poca liquidez (volumen total de
~10.7 mil dólares contra ~58.7 millones del evento de campeón), donde los precios
tienen spreads amplios.

**Estado:** documentado. Corrección sugerida: agregar
`("2nd Place", "subcampeon", 1)` a `TIPOS_MERCADO` en `polymarket.py`.

### 5. Equipos sin `winrate_historico` (referencia_equipos)

**Resultado:** 3 de 36 equipos tienen `winrate_historico` vacío: Bodø/Glimt, Como
y Sabah.

**Qué significa:** no se encontraron en la tabla histórica de Kaggle (1955-2023).
El pipeline de Polymarket ya lo reporta como advertencia y no inventa un valor.
A diferencia de la falla 6, aquí la normalización de nombres y la tabla de alias
sí se aplicaron, así que el vacío refleja que estos equipos no tienen registro en
la tabla histórica de la fuente, no un error de cruce.

**Estado:** comportamiento esperado, documentado. Se deja la validación para
detectar si en una descarga futura aparecen más equipos sin coincidencia.

### 6. Nombres de equipos distintos entre la API y la tabla histórica (cruce)

**Resultado:** solo 24 de los 36 equipos de la temporada 2026-27 se encuentran en
la tabla histórica (66.7%, por debajo del mínimo de 80%). Los 12 sin coincidencia:
Como 1907, FC Bayern München, FC Internazionale Milano, FK Bodø/Glimt, Feyenoord
Rotterdam, LASK Linz, PAE AEK, PSV, Racing Club de Lens, Real Betis Balompié,
Sabah FK y Sporting Clube de Portugal.

**Qué significa:** la mayoría sí tiene historial; el problema es que la API usa
nombres oficiales largos y Kaggle usa nombres cortos (`FC Bayern München` vs
`Bayern Munich`). La tabla `TEAM_NAME_MAP` de `champions_league.py` solo cubre
9 equipos. Consecuencia directa: en `team_stats.csv`, equipos como el Bayern o el
Inter aparecen en la partición `sin_historial_reciente` con el promedio general
como respaldo, aunque sí tienen datos.

**Estado:** documentado. Corrección sugerida: reutilizar en `champions_league.py`
la normalización y la tabla `ALIAS` que ya existen en `polymarket.py`, que lograron
el 100% de coincidencia entre la API y Polymarket.

## Comparación de desempeño: CSV vs Parquet

### Qué se midió

La operación de **guardar y leer** el dataset más grande del proyecto,
`data/interim/polymarket/historial_precios.csv` (8,941 filas x 13 columnas), en
CSV (el formato que usan hoy los dos pipelines) y en Parquet. Se midió con
`time.perf_counter()`, tomando la mediana de 5 corridas (3 en el dataset grande),
en la computadora de un integrante del equipo (Windows, Python 3.13, pandas 3.0,
pyarrow 26). Para cada formato se registró:

- tiempo de escritura (`to_csv` / `to_parquet`) y de lectura completa,
- tiempo de lectura de solo 2 de las 13 columnas (`market_id` y `p`),
- tamaño en disco y memoria del DataFrame leído (`memory_usage(deep=True)`).

Para estimar el comportamiento con un volumen mucho mayor, se repitió la medición
con el mismo dataset **replicado 100 veces** (894,100 filas). El script se corrió
como prueba aislada, fuera del pipeline, y borra sus archivos al terminar.

### Resultados

| Prueba | Volumen | Escritura | Lectura completa | Lectura de 2 columnas | Tamaño en disco | Memoria en pandas | Resultado |
|---|---|---|---|---|---|---|---|
| CSV | 8,941 filas | 48.3 ms | 27.3 ms | 14.5 ms | 1.76 MB | 1.79 MB | referencia |
| Parquet | 8,941 filas | 17.5 ms | 8.0 ms | 3.9 ms | 0.08 MB | 1.94 MB | 2.8x más rápido al escribir, 3.4x al leer, 22x menos disco |
| CSV | 894,100 filas | 4,682.2 ms | 2,266.6 ms | 1,133.3 ms | 176.43 MB | 179.06 MB | referencia |
| Parquet | 894,100 filas | 868.1 ms | 233.8 ms | 18.3 ms | 3.43 MB | 193.59 MB | 5.4x más rápido al escribir, 9.7x al leer, 62x al leer 2 columnas, 51x menos disco |

### Hallazgo adicional: tipos de datos

La primera vez que se corrió la medición, **guardar en Parquet falló** con
`OverflowError: int too big to convert`. La causa es la columna `token_id_yes`:
es un identificador de 77 dígitos y, como el CSV no guarda tipos, pandas lo
interpreta como número al leerlo. Ningún tipo numérico de Parquet admite un
entero de ese tamaño. Se corrigió leyendo esa columna como texto
(`dtype={"token_id_yes": "str"}`), que es lo correcto para un identificador.
Aun así, al volver a leer el CSV la columna regresa como `object` (números de
Python), mientras que Parquet la conserva como texto: **Parquet guarda el esquema
de cada columna y el CSV obliga a adivinarlo en cada lectura.**

### Preguntas

**¿Qué operación se midió?** La escritura y lectura del historial de precios de
Polymarket (la salida de `limpiar_historial` en `src/processing/polymarket.py`),
completa y leyendo solo 2 columnas, en CSV y en Parquet.

**¿Qué cambió entre una y otra?** Solo el formato del archivo; los datos son los
mismos. Con 8,941 filas, Parquet escribió 2.8 veces más rápido, leyó 3.4 veces más
rápido y ocupó 22 veces menos espacio (0.08 MB contra 1.76 MB). La memoria en
pandas fue prácticamente igual (1.79 MB contra 1.94 MB; la pequeña diferencia es
la columna `token_id_yes`, que Parquet trae como texto).

**¿Por qué mejoró?** Por tres características de Parquet:

1. **Formato binario con tipos:** los números se guardan como números, no como
   texto que hay que convertir al leer. Leer un CSV implica interpretar carácter
   por carácter y adivinar el tipo de cada columna.
2. **Almacenamiento por columnas:** los valores de una misma columna se guardan
   juntos. Por eso leer solo 2 columnas cuesta casi nada en Parquet (no toca las
   otras 11), mientras que el CSV tiene que recorrer cada fila completa.
3. **Compresión:** valores parecidos y repetidos dentro de una columna (como
   `market_id`, `fecha` o `fetched_at`) se comprimen muy bien.

La memoria no mejora, porque una vez cargados en pandas los datos ocupan lo mismo
sin importar de qué archivo vinieron.

**¿Qué pasaría con un volumen mucho mayor?** La ventaja de Parquet crece. Al
multiplicar los datos por 100, la lectura en CSV tardó 83 veces más (de 27 ms a
2.3 s, casi proporcional al volumen), mientras que en Parquet tardó 29 veces más
(de 8 ms a 234 ms). Leer 2 columnas pasó a ser 62 veces más rápido en Parquet.
Hay que tomar con cautela la compresión de 51x en disco: los datos replicados son
muy repetitivos y se comprimen mejor que datos reales de ese tamaño. Lo que no
cambia con el formato es la memoria: ~180-190 MB para 894 mil filas, que crecería
proporcionalmente. Con volúmenes que no caben en la RAM, el formato ya no basta y
habría que procesar por partes (lectura por bloques, particiones como las
`season=` o `tipo_mercado=` que ya usa el proyecto) o usar herramientas que no
cargan todo a memoria, como DuckDB, Polars o Spark, que además leen Parquet de
forma nativa.

**Conclusión para el proyecto:** con el volumen actual (cientos o miles de filas
por archivo), CSV es suficiente: el Integrante 2 lo eligió por simplicidad (ver
`docs/decisions.md`) y tiene la ventaja de poder abrirse directamente en Excel o
en GitHub. Si el historial de
precios creciera (más mercados, más temporadas o datos por hora en lugar de por
día), conviene migrar `data/interim/` y `data/processed/` a Parquet.
