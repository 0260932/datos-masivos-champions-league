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


## Extensión del histórico manual a 5 temporadas (2026-10-21/22)

**Decisión:** ampliar `champions_league_matches_historico_manual.csv` de 1
temporada (2022-23) a 5 temporadas consecutivas (2021-22, 2022-23, 2023-24,
2024-25, 2025-26), partido por partido, mismas columnas que la API. Responde
directamente al punto 10 del feedback del profesor sobre Entrega 1: usar a
Claude para extender la base histórica por investigación web, dado que el
plan gratuito de football-data.org solo cubre 2 temporadas.

**Cómo se hizo:** 4 agentes de investigación en paralelo (uno por temporada),
cada uno usando RSSSF (temporadas de fase de grupos: 2021-22, 2023-24) o
Wikipedia + UEFA.com (temporadas de fase de liga: 2024-25, 2025-26) como
fuente primaria, con verificación cruzada contra una segunda fuente
independiente. Antes de integrar los datos se validó automáticamente:
conteo de partidos esperado por temporada (125 en formato de grupos, 189 en
formato de fase de liga), sin `match_id` duplicados, sin un equipo jugando
contra sí mismo, marcadores numéricos en rango. Detalle de fuentes por
temporada en `data/raw/README_historico_manual.md`.

**Limitación documentada:** la temporada 2025-26 es posterior a la fecha de
corte de entrenamiento de Claude, así que depende por completo de la
búsqueda web de esta sesión (no se pudo verificar también contra el
conocimiento previo del modelo, a diferencia de las otras 4 temporadas). Se
recomienda una revisión manual de muestra.

**Consecuencia:** `data/raw/champions_league_matches_historico_manual.csv`
pasó de 125 a 753 filas; `data/interim/champions_league/matches_clean.csv`
pasó de 269 a 897 filas (dentro del rango esperado por la validación de
volumen, [100, 1000]). Las temporadas 2024-25 y 2025-26 ya usan el formato
de fase de liga de 36 equipos (`stage=LEAGUE_STAGE`, con una ronda adicional
`KNOCKOUT_PLAYOFFS` para los lugares 9-24), el mismo formato que la API usa
para la temporada actual 2026-27.

---

## Plataforma en vivo de moneyline (`src/live/`, `src/models/`, `streamlit_app.py`)

## Decisión
Tracker en vivo (terminal y dashboard web en Streamlit) que, cada 30 s,
compara la probabilidad 1X2 de un modelo contra el precio de compra del
mercado moneyline del partido en Polymarket, para Champions League, La
Liga y Premier League.

## Razón y decisiones de diseño
### 1. El modelo parte del precio del mercado
Antes del silbatazo se buscan los goles esperados de cada equipo que
reproducen el 1X2 de Polymarket (precio medio entre compra y venta; ya
iniciado el partido, el último precio antes del inicio tomado del
historial). Durante el partido, los goles restantes se modelan como
Poisson sobre el tiempo que falta. Así el modelo arranca igual que el
mercado y solo se separa de él por lo que pasa en la cancha. La primera
versión usaba solo nuestras estadísticas de Champions y producía
"ventajas" falsas antes del partido (p. ej. Real Madrid +10 pts en el
Clásico, Málaga +31 pts sin datos de ese equipo).

### 2. La ventaja mínima la fija el modelo
Se recalcula la probabilidad con el ritmo de goles de cada equipo ±20%
(los segundos tiempos tienen más goles y el que pierde arriesga). La
ventaja exigida es la distancia entre la probabilidad central y la del
escenario más bajo, más 2 pts de deslizamiento. BUENO exige que incluso el
escenario bajo quede arriba del precio de compra. La exigencia es grande
al inicio y se achica al final del partido.

### 3. Cuánto apostar
Ganancia esperada por $1 = probabilidad / precio − 1. Apuesta sugerida:
¼ de Kelly calculado con el escenario bajo, con tope de 5% de la banca,
porque el modelo no ve tarjetas rojas, lesiones ni penales.

### 4. Marcador en vivo desde ESPN
El plan gratuito de football-data.org no actualiza los partidos en juego
a tiempo: el 23-oct-2026, Alavés–Málaga seguía en `TIMED` y sin marcador
minutos después del inicio (última actualización 00:20 UTC). El
calendario, los nombres y los escudos siguen viniendo de football-data;
el marcador y el minuto, del scoreboard público de ESPN (sin llave). Si
ninguna fuente trae el marcador, el partido queda como `SIN_DATOS` y sin
señal; nunca se supone 0-0.

### 5. Unificación con el código del proyecto
- Nombres de equipo: `normalizar_nombre` y `ALIAS` de
  `src/processing/polymarket.py`, más una regla para no confundir equipos
  que comparten palabra (Manchester United vs Manchester City).
- Historial de precios: `_pedir_historial` de `src/processing/polymarket.py`
  (se le agregó un parámetro opcional `params` para pedir rango y
  fidelidad de 1 minuto; su uso en el pipeline no cambia).
- Estadísticas por equipo de `src/processing/champions_league.py` (respaldo
  cuando un partido no tiene mercado).
- XGBoost del notebook 02 llevado a `src/models/xgboost_resultado.py` con
  las mismas variables, hiperparámetros y diseño de validación.

### 6. XGBoost como segunda opinión, no como señal
Misma validación hacia adelante que el notebook 02 (5 temporadas
completas, 4 cortes): exactitud promedio 49.0% contra 47.9% de predecir
siempre la clase más común; supera la línea base en 3 de 4 cortes, por 1 a
2.4 pts. Como la ventaja es mínima, se muestra junto a cada partido con su
exactitud y no interviene en la señal. Antes de entrenar se unifican los
nombres entre temporadas con el normalizador y los alias del pipeline de
Polymarket: el histórico manual y la API escriben distinto a varios
equipos ("Bayern Munich" vs "FC Bayern München"; 9 nombres unificados).
Por eso los cortes difieren un poco de los del notebook (que supera la
base en 2 de 4), aunque el promedio coincide.

### 7. Postura del proyecto: la plataforma muestra señales de apuesta
Decisión del equipo (18-nov-2026): la plataforma en vivo muestra las señales
BUENO / ESPERAR / NO y la apuesta sugerida, con su sustento ("Por qué",
rango del modelo, ventaja exigida y ganancia esperada). El aviso académico de
`src/processing/polymarket.py` se mantiene y aplica a ese módulo: su
`prob_referencia` es un modelo ingenuo con historia de finales, distinto del
modelo en vivo anclado al mercado. El dashboard conserva la nota de que las
señales son una referencia del modelo, no una garantía.

## Alternativas consideradas
- Umbral fijo elegido por el usuario (5 pts): descartado porque no tiene
  sustento; la incertidumbre real cambia con el minuto y el marcador.
- Usar el XGBoost para decidir la señal: descartado por su validación.
- Poisson independiente con nuestras estadísticas sin anclar al mercado:
  descartado por las ventajas falsas antes del partido.

## Consecuencias
- Las señales solo aparecen en vivo, cuando el mercado tarda en reaccionar
  a un gol o al reloj; antes del partido el modelo coincide con el precio.
- El modelo no considera tarjetas rojas, alineaciones ni cambios.
- Cuando el mercado prevé pocos empates, el ajuste da muchos goles
  esperados (Clásico: 3.5–2.4) y el modelo reacciona menos a cada gol.
- ESPN es una fuente pública no oficial: si cambia su formato, el
  partido queda sin marcador y sin señal (no con datos falsos).
---

## Plataforma v2: más ligas, xG en vivo, GARCH, avisos clicables y prueba histórica

## Decisión
Segunda versión de la plataforma en vivo (`src/live/`, `src/models/`, `streamlit_app.py`), pedida por el equipo para "predecir en vez de reaccionar":
- **10 ligas** (Champions, Premier, La Liga, Serie A, Bundesliga, Ligue 1, Eredivisie, Primeira Liga, Championship, Brasileirão): todas en el plan gratuito de football-data.org, en ESPN y con mercado moneyline en Polymarket (tags `ucl`, `premier-league`, `la-liga`, `sea`, `bundesliga`, `ligue-1`, `ere`, `primeira-liga`, `efl-championship`, `brazil-serie-a`). Vista "Todas las ligas" por defecto.
- **xG en vivo** (`src/live/xg_vivo.py`) desde el comentario jugada por jugada de ESPN.
- **GARCH(1,1)** del precio de Polymarket (`src/models/garch_mercado.py`, librería `arch`).
- **Avisos clicables** (ancla `#partido-<id>`) y link para compartir cada partido (`?liga=PL&partido=ID`).
- **XGBoost fuera de las tarjetas**: se documenta en "Cómo decide la plataforma" con su tabla de validación.
- **Prueba histórica** (`src/models/backtest_xg.py`, `notebooks/03_backtest_xg_mercado.ipynb`).

## Razón y decisiones de diseño
### 1. Calendario y marcador por separado
Con 10 ligas, pedir el calendario a football-data.org en cada vuelta rebasaría el límite de 10 solicitudes/min. El calendario se pide cada 10 min por liga; el marcador, minuto, tiros y rojas salen de ESPN en cada vuelta.

### 2. xG aproximado por zona de tiro
ESPN no publica xG, pero su comentario describe cada tiro. Valores por zona (pie / cabeza), del orden de los modelos públicos de xG: penal 0.76; muy cerca 0.45 / 0.30; área chica 0.30 / 0.20; centro del área 0.12 / 0.08; costados del área 0.06 / 0.05; ángulo difícil 0.04 / 0.03; fuera del área 0.03 / 0.02; sin ubicación 0.06. Para el ritmo futuro se usa **xG sin penales**: un penal no anticipa ocasiones y, si entró, ya está en el marcador. Validado contra un partido real (Hull 2-0 Brighton: 11 y 15 tiros, igual que las estadísticas de ESPN).

### 3. El xG se muestra pero no entra en la señal
Diseño probado: ritmo = (ritmo previo × W + npxG) / (W + minuto), con W = 60 min, más ×0.70 / ×1.20 por roja. En la prueba histórica empeoró el pronóstico. Para no descartarlo por un mal peso, W se eligió con los 40 partidos más antiguos y se evaluó en los 40 más recientes (Brier en minutos 15, 30, 45, 60 y 75):

| W (min) | Ajuste | Prueba |
|---|---|---|
| sin xG | 0.374 | **0.481** |
| 60 | 0.386 | 0.514 |
| 240 | 0.375 | 0.490 |
| 960 | 0.373 | 0.483 |

Entre más pesa el xG por zona, peor pronostica fuera de muestra. Primera decisión del equipo: mostrarlo solo como información.

### 3b. xG por posición del tiro y momentum (versión vigente)
El equipo pidió que las predicciones usen xG y momentum, como las apps de fútbol (FotMob). ESPN sí da la posición de cada tiro (`fieldPositionX/Y`, portería rival en X = 100), así que se entrenó un **xG propio** (`src/models/xg_modelo.py`): regresión logística sobre distancia y ángulo a la portería (cancha de 105 × 68 m), cabezazo y su interacción con la distancia; penales con su tasa real.
- Entrenamiento: 28,009 tiros de 1,004 partidos de las 10 ligas, de hace 22 a 150 días (antes del periodo de la prueba histórica, para no reusar tiros). Penales: 79% de 253.
- Validación en 5,602 tiros no usados: AUC 0.751 (por zona: 0.704; modelos públicos como Opta/FotMob: ~0.78-0.80), log loss 0.285 (por zona 0.301; sin modelo 0.328).

**Momentum** (`src/live/xg_vivo.py`): presión reciente de cada equipo = suma de su xG sin penales y 0.03 por córner, con decaimiento exp(−minutos/8) (vida media ~5.5 min). Se grafica como en FotMob y ladea el ritmo de goles hacia quien presiona: × exp(β·(parte − 0.5)), donde parte es la fracción de la presión reciente de ese equipo.

Calibración (`calibrar()` en `src/models/backtest_xg.py`): W ∈ {30…960} y β ∈ {0, 0.5, 1, 2} se eligen con los 40 partidos más antiguos; se reporta la mitad más reciente.

| | Ajuste (40 antiguos) | Prueba (40 recientes) |
|---|---|---|
| Sin xG | 0.403 | **0.444** |
| xG + momentum (W = 960, β = 0.5) | **0.398** | 0.447 |

En los 80 partidos, con xG y momentum el Brier mejora desde el minuto 30 (minuto 75: 0.288 vs 0.299 sin xG) y empeora al 15; la correlación de adelanto sube de 0.016 a 0.038; las señales aciertan más (53% vs 48%) pero siguen perdiendo (−8¢ vs −7¢ por $1). El mercado sigue siendo el mejor en todos los minutos. Decisión del equipo: **la señal usa xG y momentum** con esos pesos (`USAR_XG_EN_SENAL = True`, `PESO_PREVIO_MIN = 960`, `PESO_MOMENTUM = 0.5`); en la prueba quedó neutral, ya no perjudica como el xG por zona.

### 4. GARCH del ruido del mercado
Rendimientos en logit sobre una malla de 5 min (con malla de 1 min, los muchos minutos sin operaciones colapsaban la varianza a ~0). Los saltos de noticia (más de 1 en logit, como un gol) se excluyen del ajuste: tras un gol el GARCH pronosticaba ±43 pts de ruido. El ruido a 2 min (95%, un lado) reemplaza al colchón fijo cuando es mayor a 2 pts, con tope de 10 pts. Si el último movimiento es un salto (>3σ y ≥2 pts), el mercado cuenta como agitado y no se da BUENO en esa vuelta. En la prueba, GARCH **no** pronosticó la volatilidad mejor que una varianza constante (QLIKE 1025 vs 610, 8,147 pronósticos), así que en la práctica casi siempre manda el mínimo de 2 pts.

### 5. Prueba histórica sin ventajas injustas
80 partidos cerrados de los últimos 21 días (Champions 18, Premier 18, La Liga 20, Serie A 20, Bundesliga 4; se llegó al tope antes de las demás ligas), 7,730 minutos de juego. En cada minuto solo se usa información anterior; los datos de ESPN entran con 60 s de atraso y la compra se hace al precio del minuto siguiente + 1 pt. Resultados:
- Brier del resultado final: el mercado fue el mejor en todos los minutos (p. ej. minuto 45: mercado 0.429, modelo sin xG 0.449, con xG 0.483).
- Adelanto: la correlación entre (modelo − mercado) y el cambio del precio 5 min después fue ~0.02, con y sin xG.
- Señales BUENO: sin xG, 267 señales, 48% de aciertos, −7¢ por $1; con xG por zona, 420 señales, 41%, −16¢ por $1; con xG por posición y momentum, 304 señales, 53%, −8¢ por $1.

## Alternativas consideradas
- Mantener el xG por zona con W = 60 en la señal: descartado por la prueba fuera de muestra.
- Usar la API de FotMob (xG y momentum oficiales): no es pública (responde 404 sin su firma), así que todo sale de ESPN.
- Notificaciones push al celular (ntfy): descartado por el equipo; avisos dentro de la página.
- Malla de 1 min para el GARCH: descartada (varianza colapsada).

## Consecuencias
- La plataforma sigue mostrando señales (postura del equipo), y la metodología muestra que en la prueba histórica no le ganaron al mercado.
- El xG por posición mejoró la predicción respecto al de zona, pero no lo suficiente para batir al mercado; datos que ESPN no publica (tipo de asistencia, posición del portero, toques en el área) podrían mejorarlo más.
- La muestra de la prueba es de 80 partidos y 5 ligas; correrla con más días (`--dias 35 --max-partidos 150`) amplía la evidencia.
