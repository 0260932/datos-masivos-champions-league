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

**76 de 76 validaciones pasan** (corrida del 21 de octubre de 2026, después de corregir
las 6 limitaciones que se habían documentado en la primera corrida). El diagnóstico
completo de cada una se conserva más abajo, junto con la corrección aplicada: cuatro se
corrigieron en el código, y dos se documentaron como excepciones aceptadas y acotadas
(con justificación, sin inventar ningún valor), usando un nuevo parámetro `max_fallas` /
`excepciones` agregado a `validators.py`.

| Dataset | Validaciones | Pasaron |
|---|---|---|
| CL · `interim/matches_clean.csv` | 13 | 13 |
| CL · `processed/historical_performance.csv` | 15 | 15 |
| CL · `processed/finals.csv` | 6 | 6 |
| CL · `processed/season=*/team_stats.csv` | 9 | 9 |
| PM · `processed/tipo_mercado=*/mercados.csv` | 13 | 13 |
| PM · `processed/resumen_eventos.csv` | 2 | 2 |
| PM · `interim/historial_precios.csv` | 7 | 7 |
| PM · `processed/movimientos_significativos.csv` | 2 | 2 |
| PM · `processed/referencia_equipos.csv` | 5 | 5 |
| Cruce entre fuentes | 4 | 4 |
| **Total** | **76** | **76** |

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

## Limitaciones corregidas (actualizado el 21 de octubre de 2026)

Las seis limitaciones documentadas en la primera corrida ya se corrigieron en el
código. Ninguna se "corrigió" inventando datos: cuatro eran bugs reales (se
arreglaron) y dos eran hallazgos reales de los datos de origen (se documentaron
como excepciones aceptadas, acotadas y justificadas, sin ocultar la desviación real).

### 1. Convención de `season` mezclada (matches_clean) — corregido

Se corrigió el valor de `season` en
`data/raw/champions_league_matches_historico_manual.csv`, de `2023` a `2022`
(año de inicio de temporada, igual que la convención de la API). Las 125 filas de
la temporada 2022-23 ahora quedan en su propia partición `season=2022`.

### 2. Partidos que no cuadran en la tabla histórica (historical_performance) — excepción documentada

Siguen existiendo los mismos 2 equipos con un partido de diferencia (Linfield FC,
1. FC Frankfurt (Oder)); es un hueco real de la fuente de Kaggle, no un error de
procesamiento, así que no se inventó ningún resultado. Se agregó el parámetro
`max_fallas` a `validate_row_rule` en `validators.py`, y se usó `max_fallas=2` en
esta validación: sigue revisando los 354 equipos y reportaría como falla real
cualquier caso nuevo que no sea uno de estos dos ya conocidos.

### 3. Final de 1973-74 repetida (finals) — corregido

Se cambió el `drop_duplicates()` de `clean_finals()` (en `champions_league.py`)
de "por fila completa" a `drop_duplicates(subset=["season"], keep="last")`, igual
que ya hacía `construir_referencia()` en `polymarket.py`: se conserva el desempate
(el resultado que de verdad decidió la final). `finals.csv` ahora tiene 68
temporadas en vez de 69.

### 4. Evento "2nd Place" sin número esperado de ganadores (resumen_eventos) — corregido, con hallazgo adicional

Se agregó `("2nd Place", "subcampeon", 1)` a `TIPOS_MERCADO` en `polymarket.py`,
así que el evento ya se clasifica con 1 ganador esperado en vez de caer en `otro`.

Al corregir esto encontramos un bug adicional: el pipeline no borraba las carpetas
`tipo_mercado=*` de la corrida anterior antes de escribir las nuevas, así que al
reclasificar este evento quedaban archivos duplicados de la corrida vieja (72
filas de más, detectado por la validación de duplicados). Se corrigió agregando
una limpieza de esas carpetas al inicio de `main()` en `polymarket.py`.

Con el evento ya bien clasificado, apareció un hallazgo real (no un bug): la suma
de probabilidades de "subcampeon" (1.53 contra 1 esperado), "ganador_fase_liga"
(1.41 contra 1) y "top_8" (8.84 contra 8) se sigue desviando más del 10% de
tolerancia. Es el mismo tipo de ineficiencia de mercado que señaló el profesor en
su retroalimentación: son mercados con poco volumen/liquidez frente al de
"campeón" (36 preguntas independientes, cada una con poca liquidez propia), así
que sus precios no están perfectamente calibrados entre sí. No se "corrige"
inventando precios: se agregó el parámetro `excepciones` a `validate_group_sum` en
`validators.py`, que acepta estos tres grupos con su justificación documentada,
y sigue reportando la desviación real (no la oculta).

### 5. Equipos sin `winrate_historico` (referencia_equipos) — excepción documentada

Siguen sin historial Bodø/Glimt, Como y Sabah (equipos sin trayectoria previa en
la Champions League; no están en la tabla de Kaggle 1955-2023). Se usó el mismo
parámetro `max_fallas` (agregado también a `validate_no_missing`), con
`max_fallas=3`.

### 6. Nombres de equipos distintos entre la API y la tabla histórica (cruce) — corregido

Se agregó `nombre_canonico()` a `polymarket.py`: un mapa inverso de toda variante
normalizada (nombre canónico o alias) a su nombre canónico "bonito", construido a
partir del mismo diccionario `ALIAS` que ya logra 100% de coincidencia con
Polymarket. `champions_league.py` ahora usa esa función como respaldo dentro de
`standardize_team_name()` (después de `TEAM_NAME_MAP`), así que los dos pipelines
usan el mismo catálogo de nombres. La coincidencia entre la temporada actual y la
tabla histórica subió de 66.7% a **91.7%** (33 de 36 equipos); el 8.3% restante son
justo los 3 equipos de la limitación 5, que no tienen entrada en la tabla
histórica sin importar qué tan bien se normalice el nombre.

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
