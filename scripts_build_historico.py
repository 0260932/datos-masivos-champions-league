import csv
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
OUT_PATH = ROOT_DIR / "data" / "raw" / "champions_league_matches_historico_manual.csv"

# Temporada 2022-23, UEFA Champions League.
# Fuente: RSSSF (https://rsssf.org/ec/ec202223.html), resultados oficiales UEFA.
# Investigacion asistida por IA (Claude) a partir de fuentes publicas confiables,
# no de la API de football-data.org (que no da acceso a temporadas tan viejas
# en el plan gratuito). match_id generado manualmente (no existe uno oficial
# facil de obtener), partiendo de 900001.
# NOTA sobre eliminatorias (ida/vuelta): para simplificar, se mantiene siempre
# "home_team"/"away_team" como el mismo orden (Equipo A / Equipo B) en ambas
# piernas, en vez de intentar determinar la sede exacta de cada partido de
# vuelta. Esto no afecta las variables agregadas por equipo (goles a favor/en
# contra, % de victorias), que es para lo que se usa este archivo.

rows = []
mid = 900001

def add(stage, matchday, date, home, away, hs, a_s):
    global mid
    rows.append({
        "match_id": mid, "season": 2023, "stage": stage, "matchday": matchday,
        "utc_date": date, "status": "FINISHED",
        "home_team": home, "away_team": away,
        "home_score": hs, "away_score": a_s,
    })
    mid += 1

# --- Fase de grupos ---
groups = {
"A": [("Ajax","Rangers (Glasgow)"),("SSC Napoli","Liverpool"),("Liverpool","Ajax"),("Rangers (Glasgow)","SSC Napoli"),
      ("Ajax","SSC Napoli"),("Liverpool","Rangers (Glasgow)"),("SSC Napoli","Ajax"),("Rangers (Glasgow)","Liverpool"),
      ("Ajax","Liverpool"),("SSC Napoli","Rangers (Glasgow)"),("Liverpool","SSC Napoli"),("Rangers (Glasgow)","Ajax")],
"B": [("Atletico Madrid","FC Porto"),("Club Brugge KV","Bayer 04 Leverkusen"),("Bayer 04 Leverkusen","Atletico Madrid"),("FC Porto","Club Brugge KV"),
      ("Club Brugge KV","Atletico Madrid"),("FC Porto","Bayer 04 Leverkusen"),("Atletico Madrid","Club Brugge KV"),("Bayer 04 Leverkusen","FC Porto"),
      ("Club Brugge KV","FC Porto"),("Atletico Madrid","Bayer 04 Leverkusen"),("Bayer 04 Leverkusen","Club Brugge KV"),("FC Porto","Atletico Madrid")],
"C": [("Barcelona","Viktoria Plzen"),("Internazionale","Bayern Munich"),("Viktoria Plzen","Internazionale"),("Bayern Munich","Barcelona"),
      ("Bayern Munich","Viktoria Plzen"),("Internazionale","Barcelona"),("Barcelona","Internazionale"),("Viktoria Plzen","Bayern Munich"),
      ("Internazionale","Viktoria Plzen"),("Barcelona","Bayern Munich"),("Bayern Munich","Internazionale"),("Viktoria Plzen","Barcelona")],
"D": [("Eintracht Frankfurt","Sporting CP"),("Tottenham Hotspur","Olympique Marseille"),("Sporting CP","Tottenham Hotspur"),("Olympique Marseille","Eintracht Frankfurt"),
      ("Olympique Marseille","Sporting CP"),("Eintracht Frankfurt","Tottenham Hotspur"),("Sporting CP","Olympique Marseille"),("Tottenham Hotspur","Eintracht Frankfurt"),
      ("Eintracht Frankfurt","Olympique Marseille"),("Tottenham Hotspur","Sporting CP"),("Olympique Marseille","Tottenham Hotspur"),("Sporting CP","Eintracht Frankfurt")],
"E": [("Dinamo Zagreb","Chelsea"),("Red Bull Salzburg","AC Milan"),("AC Milan","Dinamo Zagreb"),("Chelsea","Red Bull Salzburg"),
      ("Red Bull Salzburg","Dinamo Zagreb"),("Chelsea","AC Milan"),("AC Milan","Chelsea"),("Dinamo Zagreb","Red Bull Salzburg"),
      ("Red Bull Salzburg","Chelsea"),("Dinamo Zagreb","AC Milan"),("AC Milan","Red Bull Salzburg"),("Chelsea","Dinamo Zagreb")],
"F": [("Celtic (Glasgow)","Real Madrid"),("RB Leipzig","Shakhtar Donetsk"),("Shakhtar Donetsk","Celtic (Glasgow)"),("Real Madrid","RB Leipzig"),
      ("RB Leipzig","Celtic (Glasgow)"),("Real Madrid","Shakhtar Donetsk"),("Celtic (Glasgow)","RB Leipzig"),("Shakhtar Donetsk","Real Madrid"),
      ("Celtic (Glasgow)","Shakhtar Donetsk"),("RB Leipzig","Real Madrid"),("Real Madrid","Celtic (Glasgow)"),("Shakhtar Donetsk","RB Leipzig")],
"G": [("Borussia Dortmund","FC Kobenhavn"),("Sevilla FC","Manchester City"),("FC Kobenhavn","Sevilla FC"),("Manchester City","Borussia Dortmund"),
      ("Manchester City","FC Kobenhavn"),("Sevilla FC","Borussia Dortmund"),("FC Kobenhavn","Manchester City"),("Borussia Dortmund","Sevilla FC"),
      ("Sevilla FC","FC Kobenhavn"),("Borussia Dortmund","Manchester City"),("FC Kobenhavn","Borussia Dortmund"),("Manchester City","Sevilla FC")],
"H": [("SL Benfica","Maccabi Haifa"),("Paris Saint-Germain","Juventus"),("Juventus","SL Benfica"),("Maccabi Haifa","Paris Saint-Germain"),
      ("SL Benfica","Paris Saint-Germain"),("Juventus","Maccabi Haifa"),("Maccabi Haifa","Juventus"),("Paris Saint-Germain","SL Benfica"),
      ("SL Benfica","Juventus"),("Paris Saint-Germain","Maccabi Haifa"),("Juventus","Paris Saint-Germain"),("Maccabi Haifa","SL Benfica")],
}
scores = {
"A": [(4,0),(4,1),(2,1),(0,3),(1,6),(2,0),(4,2),(1,7),(0,3),(3,0),(2,0),(1,3)],
"B": [(2,1),(1,0),(2,0),(0,4),(2,0),(2,0),(0,0),(0,3),(0,4),(2,2),(0,0),(2,1)],
"C": [(5,1),(0,2),(0,2),(2,0),(5,0),(1,0),(3,3),(2,4),(4,0),(0,3),(2,0),(2,4)],
"D": [(0,3),(2,0),(2,0),(0,1),(4,1),(0,0),(0,2),(3,3),(2,1),(1,1),(1,2),(1,2)],
"E": [(1,0),(1,1),(3,1),(1,1),(1,0),(3,0),(0,2),(1,1),(1,2),(0,4),(4,0),(2,1)],
"F": [(0,3),(1,4),(1,1),(2,0),(3,1),(2,1),(0,2),(1,1),(1,1),(3,2),(5,1),(0,4)],
"G": [(3,0),(0,4),(0,0),(2,1),(5,0),(1,4),(0,0),(1,1),(3,0),(0,0),(1,1),(3,1)],
"H": [(2,0),(2,1),(1,2),(1,3),(1,1),(3,1),(2,0),(1,1),(4,3),(7,2),(1,2),(1,6)],
}
dates_pattern_1 = ["2022-09-07","2022-09-13","2022-10-04","2022-10-12","2022-10-26","2022-11-01"]
dates_pattern_2 = ["2022-09-06","2022-09-14","2022-10-05","2022-10-11","2022-10-25","2022-11-02"]

for g in groups:
    dates = dates_pattern_1 if g in ("A","B","C","D") else dates_pattern_2
    for i, ((home, away), (hs, a_s)) in enumerate(zip(groups[g], scores[g])):
        matchday = i // 2 + 1
        date = dates[matchday - 1]
        add(f"GROUP_STAGE_{g}", matchday, date, home, away, hs, a_s)

# --- Eliminatorias (ida/vuelta) ---
ko = [
    ("LAST_16", 7, 8, "2023-02-14", "2023-03-08", "AC Milan", "Tottenham Hotspur", (1,0), (0,0)),
    ("LAST_16", 7, 8, "2023-02-14", "2023-03-08", "Paris Saint-Germain", "Bayern Munich", (0,1), (0,2)),
    ("LAST_16", 7, 8, "2023-02-15", "2023-03-07", "Borussia Dortmund", "Chelsea", (1,0), (0,2)),
    ("LAST_16", 7, 8, "2023-02-15", "2023-03-07", "Club Brugge KV", "SL Benfica", (0,2), (1,5)),
    ("LAST_16", 7, 8, "2023-02-21", "2023-03-15", "Eintracht Frankfurt", "SSC Napoli", (0,2), (0,3)),
    ("LAST_16", 7, 8, "2023-02-21", "2023-03-15", "Liverpool", "Real Madrid", (2,5), (0,1)),
    ("LAST_16", 7, 8, "2023-02-22", "2023-03-14", "Internazionale", "FC Porto", (1,0), (0,0)),
    ("LAST_16", 7, 8, "2023-02-22", "2023-03-14", "RB Leipzig", "Manchester City", (1,1), (0,7)),
    ("QUARTER_FINALS", 9, 10, "2023-04-11", "2023-04-19", "SL Benfica", "Internazionale", (0,2), (3,3)),
    ("QUARTER_FINALS", 9, 10, "2023-04-11", "2023-04-19", "Manchester City", "Bayern Munich", (3,0), (1,1)),
    ("QUARTER_FINALS", 9, 10, "2023-04-12", "2023-04-18", "AC Milan", "SSC Napoli", (1,0), (1,1)),
    ("QUARTER_FINALS", 9, 10, "2023-04-12", "2023-04-18", "Real Madrid", "Chelsea", (2,0), (2,0)),
    ("SEMI_FINALS", 11, 12, "2023-05-09", "2023-05-17", "Real Madrid", "Manchester City", (1,1), (0,4)),
    ("SEMI_FINALS", 11, 12, "2023-05-10", "2023-05-16", "AC Milan", "Internazionale", (0,2), (0,1)),
]
for stage, md1, md2, d1, d2, home, away, leg1, leg2 in ko:
    add(stage, md1, d1, home, away, leg1[0], leg1[1])
    add(stage, md2, d2, home, away, leg2[0], leg2[1])

# Final (partido unico)
add("FINAL", 13, "2023-06-10", "Manchester City", "Internazionale", 1, 0)

OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["match_id","season","stage","matchday","utc_date","status","home_team","away_team","home_score","away_score"])
    writer.writeheader()
    writer.writerows(rows)

print(f"Guardado: {OUT_PATH} ({len(rows)} partidos)")