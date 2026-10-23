# champions_league_matches_historico_manual.csv

Datos partido-por-partido de 5 temporadas de la UEFA Champions League que el
plan gratuito de football-data.org no cubre (solo da acceso a las ultimas 2
temporadas), obtenidos mediante busqueda asistida por IA (Claude): 2021-22,
2022-23, 2023-24, 2024-25 y 2025-26. Mismas columnas que
`champions_league_matches_api.csv` (match_id, season, stage, matchday,
utc_date, status, home_team, away_team, home_score, away_score).

**Temporadas y fuentes:**

| Temporada | season | Partidos | Formato | Fuente principal | Verificacion cruzada |
|---|---|---|---|---|---|
| 2021-22 | 2021 | 125 | Grupos (32 equipos) | RSSSF (rsssf.org/ec/ec202122.html) | footballwebpages.co.uk, CBS Sports, prensa de la final |
| 2022-23 | 2022 | 125 | Grupos (32 equipos) | RSSSF (rsssf.org/ec/ec202223.html) | — (unica temporada de la primera version de este archivo) |
| 2023-24 | 2023 | 125 | Grupos (32 equipos) | RSSSF (rsssf.org/ec/ec202324.html) | Wikipedia (fechas por partido de eliminatorias) |
| 2024-25 | 2024 | 189 | Fase de liga (36 equipos, formato nuevo) | Wikipedia (fase de liga + fase eliminatoria) | AP "Champions League Glance" (wtop.com), SportsMole (fechas por partido) |
| 2025-26 | 2025 | 189 | Fase de liga (36 equipos, formato nuevo) | Wikipedia (fase de liga + fase eliminatoria) | UEFA.com, bluewin.ch, adaderana.lk, milliyet.com.tr (resultado de la final) |

**Total: 753 partidos.**

**Notas:**
- match_id generado manualmente (no hay uno oficial facil de obtener), un
  rango de 1000 numeros por temporada: 900001+ (2022-23), 910001+ (2021-22),
  920001+ (2023-24), 930001+ (2024-25), 940001+ (2025-26).
- Formato de la competencia: 2021-22 a 2023-24 usan fase de grupos de 32
  equipos (stage = GROUP_STAGE_A..H, matchday 1-6, luego LAST_16/
  QUARTER_FINALS/SEMI_FINALS/FINAL en matchday 7-13). 2024-25 y 2025-26 ya
  usan el formato nuevo de 36 equipos en una sola tabla (stage = LEAGUE_STAGE,
  matchday 1-8), seguido de una ronda de desempate 9o-24o lugar
  (KNOCKOUT_PLAYOFFS, matchday 9-10) y las mismas rondas de
