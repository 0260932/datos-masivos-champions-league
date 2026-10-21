# Decisiones del proyecto

## Decisión
Ampliar el alcance de la Entrega 2 a dos fuentes de datos: profundizar
Champions League (más temporadas históricas y predicción de equipos
nuevos) y agregar Polymarket (mercados públicos de predicción) como
tercera fuente.

## Razón
El profesor dio una lista de 10 ideas de retroalimentación tras la
Entrega 1, que en realidad mezclaban dos proyectos distintos (Champions
League y Polymarket). El equipo decidió trabajar ambos en vez de
descartar uno, usando la nueva arquitectura por capas (raw/interim/processed)
para mantenerlos organizados dentro del mismo repositorio.

## Alternativas consideradas
- Quedarnos solo con Champions League y profundizar únicamente ahí.
- Cambiar el proyecto por completo a Polymarket y dejar Champions League
  como el entregable ya cerrado del primer parcial.

## Consecuencias
- Se divide el trabajo en 4 partes con un issue por tarea principal
  (ver issues #4-#7 en GitHub): arquitectura/ingestión, procesamiento de
  Champions League, procesamiento de Polymarket, y
  validaciones/pruebas/documentación.
- La ingestión de Polymarket usa el endpoint público de búsqueda de la
  Gamma API (`https://gamma-api.polymarket.com/public-search`), que no
  requiere credenciales.
- Cualquier análisis relacionado con Polymarket (valor esperado,
  detección de oportunidades) se documentará explícitamente como
  ejercicio académico, no como asesoría financiera ni recomendación real
  de apuestas.

---

## Decisión
Separar la ingestión del procesamiento en capas (`raw` → `interim` →
`processed`), y mover la corrección de la columna `goals` (hecha en
Entrega 1 dentro del script de ingestión de Kaggle) a la capa de
procesamiento.

## Razón
La Entrega 2 exige una arquitectura por capas explícita. Mezclar
descarga y limpieza en el mismo script no permite distinguir datos
crudos de datos transformados, y dificulta la trazabilidad.

## Alternativas consideradas
Dejar la limpieza dentro del script de ingestión, como en la Entrega 1.

## Consecuencias
- `src/ingestion/extract_kaggle_historical.py` ahora solo descarga
  copias crudas a `data/raw/`.
- La función de corrección de `goals` queda pendiente de reconstruirse
  en `src/processing/` (tarea de Integrante 2, issue #5).

---

## Procesamiento Champions League (Entrega 2, Integrante 2)

- **Columna `goals` corrupta (UCL_AllTime_Performance_Table.csv):** Excel
  interpreto "goals_for:goals_against" como tiempo [h]:mm:ss, acarreando
  a horas cuando goals_against > 60. Se reconstruyo resolviendo
  k = (H - MM - Dif) / 61, goals_against = 60k + MM, goals_for = H - k.
  Verificado en el 100% de las 354 filas (k entero no negativo,
  goals_for - goals_against == Dif).
- **Columna `Pt.` descartada:** identica a `Dif` en el 100% de las filas
  (dato duplicado/mal etiquetado en la fuente).
- **Equipos sin historial reciente:** se marcan con `tiene_historial_reciente=0`
  y se les asigna como respaldo el promedio general de goles a
  favor/en contra de todos los equipos en la tabla historica de Kaggle.
- **Formato de exportacion:** CSV (no Parquet) para `data/interim/` y
  `data/processed/`, por simplicidad; la comparacion formal de
  desempeno entre formatos la hace el Integrante 4.
- **Temporada historica adicional:** se agrego la temporada 2022-23
  completa (fase de grupos + eliminatorias, 125 partidos), obtenida via
  busqueda asistida por IA en RSSSF (https://rsssf.org/ec/ec202223.html).
  Se eligio 1 temporada (no 3-5) para priorizar datos bien verificados
  sobre cantidad.

---

## Decisión
Procesamiento de Polymarket (issue #6) en `src/processing/polymarket.py`:
limpieza `raw` → `interim` → `processed`, detección de movimientos
significativos del precio con su historial, y una comparación académica
contra una probabilidad de referencia histórica.

> **Aviso académico:** `prob_referencia` y `diferencia_probabilidad` son
> resultado de un modelo ingenuo con datos históricos limitados, hecho
> como ejercicio de la materia Datos Masivos. No es asesoría financiera,
> no usa información privilegiada, no es una recomendación de apuesta y
> no debe usarse para apostar dinero real.

## Razón
El profesor pidió explorar la API pública de Polymarket, identificar
cambios significativos de precio y calcular el valor esperado de una
apuesta. Al explorar los datos reales encontramos problemas de calidad
que obligaron a tomar las decisiones siguientes.

### 1. Limpieza de mercados
- Se eliminan los 12 mercados "de relleno" (`active = False`: "Team A",
  "Team H"... y "any other team"). Tienen precio 0.5 por defecto y sin
  volumen; inflaban la suma de probabilidades del evento "Campeón" de
  ~1.05 a ~4.05.
- `best_bid` vacío se conserva vacío y se marca (`sin_ofertas_compra`):
  significa que nadie ofrece comprar, no es un error. `volume` vacío se
  imputa como 0 y se marca (`volumen_imputado`).
- Se extrae el `equipo` de la pregunta con una expresión regular y se
  normaliza (minúsculas, sin acentos, sin "FC/CF/SK...") más una tabla de
  alias, para poder cruzarlo con las otras fuentes del proyecto.
- Particionamiento de `processed` por `tipo_mercado` (campeón, ganador de
  fase de liga, top 8, puestos 9-24, octavos).

### 2. Endpoint de historial de precios
- Endpoint público CLOB `https://clob.polymarket.com/prices-history` con
  el token del resultado "Yes" (`clobTokenIds` del JSON crudo),
  `interval=max` y `fidelity=1440` (un punto por día). Con `interval=1w`
  solo había una semana de datos; con `max` hay todo el historial
  (~3.5 meses, desde el 3 de julio de 2026).
- No se modificó el script de ingestión: el historial se guarda como
  caché cruda en `data/raw/polymarket_prices_history.csv` y solo se vuelve
  a pedir con `--refrescar-historial`.

### 3. Limpieza del historial (tres filtros, en orden)
1. **Precio por defecto exacto:** se descarta un punto si vale 0.50 ± 0.01
   y la mediana típica de su mercado está a ≥ 0.20 de 0.50 (278 puntos en
   95 mercados).
2. **Fallas sistémicas por evento:** si en un mismo evento y día al menos
   el 25% de sus mercados se aleja ≥ 0.20 de su precio típico, esos puntos
   se descartan. Una noticia real mueve a uno o pocos equipos; una falla
   (libro de órdenes vacío) mueve a gran parte del evento a la vez
   (p. ej. 34 de 36 mercados de "League Phase Winner" el 10 de septiembre).
3. **Picos aislados:** se descarta un punto que se aleja ≥ 0.20 de la
   mediana móvil centrada de 5 días de su mercado.

### 4. Definición de "movimiento significativo"
- Se usa el cambio diario en **puntos de probabilidad**, no el cambio
  porcentual (de 0.001 a 0.002 es +100% pero no significa nada).
- Un día es significativo si `|cambio| ≥ máx(0.02, 3 × desviación
  estándar de los cambios diarios de ese mercado)`: el piso de 2 puntos
  evita marcar ruido, y las 3 desviaciones adaptan el umbral a la
  volatilidad de cada mercado.
- Se excluyen los primeros 3 días de cada mercado (ajustes del precio
  inicial) y se marca `dia_incompleto` para el día en curso.
- **Limitación:** esto identifica CUÁNDO se movió el precio, no POR QUÉ.
  Para atribuir causas haría falta cruzar con contexto externo (partidos,
  lesiones, noticias).

### 5. Probabilidad de referencia (valor esperado académico)
- El Integrante 2 aún no ha fusionado su procesamiento, y la temporada
  2026-27 solo tiene 18 partidos jugados, así que se construyó una
  referencia provisional con `UCL_Finals_1955-2023.csv`.
- `prob_referencia` (solo para el evento "Campeón") = proporción de títulos
  históricos con suavizado de Laplace (+1 por equipo), normalizada entre
  los 36 participantes para que sume 1.
- `diferencia_probabilidad = prob_referencia − prob_implicita`, que
  equivale al valor esperado por acción "Yes" de $1 bajo este modelo:
  `VE = p_ref·(1 − p) − (1 − p_ref)·p = p_ref − p`. Es un dato
  descriptivo; el código no toma ninguna decisión de "apostar o no".
- Se agrega `winrate_historico` (victorias / partidos de
  `UCL_AllTime_Performance_Table.csv`) como dato descriptivo, no se usa en
  la referencia porque no es comparable con "probabilidad de ser campeón".
- Correcciones de calidad de datos: la final de 1973-74 aparece dos veces
  (empate y desempate) y se cuenta un solo título por temporada; en la
  tabla histórica las columnas `goals` (leída como hora) y `Pt.` (idéntica
  a `Dif`) no son confiables y no se usan.

## Alternativas consideradas
- Cambio porcentual diario para detectar movimientos: descartado porque
  exagera los cambios en equipos con probabilidad casi nula.
- Win rate histórico como probabilidad de referencia: descartado para el
  valor esperado porque mide victorias por partido, no la probabilidad de
  ser campeón.
- Ampliar la tolerancia alrededor de 0.5 para detectar fallas: descartado
  por arbitrario; se prefirió el criterio de "muchos mercados del mismo
  evento a la vez".

## Consecuencias
- La referencia es deliberadamente ingenua: el archivo de finales termina
  en 2022-23 y no refleja la fuerza actual de los equipos (p. ej. el
  mercado ve al PSG o al Arsenal como favoritos y la historia les da
  0.0125). Una `diferencia_probabilidad` grande indica un desacuerdo entre
  modelo y mercado, no una "oportunidad".
- La suma de probabilidades por evento (`desviacion_suma_vs_esperado`) se
  reporta como medida descriptiva de coherencia de precios; no implica
  que exista un arbitraje aprovechable (faltan comisiones, spreads y
  liquidez real).
- Cuando el Integrante 2 fusione su procesamiento (issue #5), la
  referencia puede reemplazarse por métricas de la temporada actual.
