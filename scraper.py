"""
NHL Daily Scraper
Pulls tonight's lines, PP units, and goalie stats from DailyFaceoff + NHL API.
Runs once daily and saves output to data/today.json
"""

import requests
import json
import re
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

NHL_API = "https://api-web.nhle.com/v1"
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

DEFAULT_SHOTS_AGAINST = 29.0
DEFAULT_SAVE_PCT = 0.910


# ── Helpers ────────────────────────────────────────────────────────────────────
def safe_get(d, *keys, default=None):
    """Safely traverse nested dicts."""
    for key in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(key)
        if d is None:
            return default
    return d


def api_get(url, label=""):
    """GET with error handling — returns parsed JSON or None."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  API error [{label}]: {e}")
        return None


# ── NHL API ────────────────────────────────────────────────────────────────────
def get_tonight_games():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = api_get(f"{NHL_API}/schedule/{today}", "schedule")
    if not data:
        return []
    games = []
    for gw in data.get("gameWeek", []):
        if gw.get("date") == today:
            for g in gw.get("games", []):
                try:
                    away = safe_get(g, "awayTeam", "abbrev", default="")
                    home = safe_get(g, "homeTeam", "abbrev", default="")
                    if away and home:
                        games.append({"away": away, "home": home, "time": g.get("startTimeUTC", ""), "id": g.get("id", "")})
                except Exception as e:
                    print(f"  Game parse error: {e}")
    print(f"Games tonight: {[f'{g[\"away\"]}@{g[\"home\"]}' for g in games]}")
    return games


def get_team_stats(team_abbr):
    """Shots-against per game from standings."""
    data = api_get(f"{NHL_API}/standings/now", "standings")
    if data:
        for team in data.get("standings", []):
            abbrev = safe_get(team, "teamAbbrev", "default") or team.get("teamAbbrev", "")
            if abbrev == team_abbr:
                gp = team.get("gamesPlayed", 1) or 1
                sa = team.get("shotsAgainst", None)
                if sa:
                    result = round(sa / gp, 1)
                    print(f"  {team_abbr} SA/g: {result}")
                    return result
    print(f"  {team_abbr} SA/g: default {DEFAULT_SHOTS_AGAINST}")
    return DEFAULT_SHOTS_AGAINST


def get_goalie_stats(team_abbr):
    """Best goalie SV% from roster + player landing pages."""
    data = api_get(f"{NHL_API}/roster/{team_abbr}/current", f"roster/{team_abbr}")
    if not data:
        return {"name": "Unknown", "save_pct": DEFAULT_SAVE_PCT, "gp": 0}

    best = None
    best_gp = 0

    for g in data.get("goalies", []):
        pid = g.get("id")
        if not pid:
            continue
        sdata = api_get(f"{NHL_API}/player/{pid}/landing", f"goalie/{pid}")
        if not sdata:
            continue

        # Try multiple key paths for season stats
        sub = (
            safe_get(sdata, "featuredStats", "regularSeason", "subSeason") or
            safe_get(sdata, "last5Games", 0) or
            {}
        )
        # Also check seasonTotals array
        if not sub:
            totals = sdata.get("seasonTotals", [])
            for t in totals:
                if t.get("season") and t.get("gamesPlayed"):
                    sub = t
                    break

        gp = sub.get("gamesPlayed", 0) or sub.get("gp", 0) or 0
        svp = sub.get("savePctg") or sub.get("savePct") or sub.get("svPct") or sub.get("savePercentage")

        fname = safe_get(g, "firstName", "default") or str(g.get("firstName", ""))
        lname = safe_get(g, "lastName", "default") or str(g.get("lastName", ""))
        name = f"{fname} {lname}".strip()

        print(f"  {team_abbr} goalie candidate: {name} GP={gp} SV%={svp}")

        if gp > best_gp and svp is not None:
            best_gp = gp
            best = {"name": name, "save_pct": round(float(svp), 3), "gp": gp}

    if best:
        print(f"  {team_abbr} goalie selected: {best['name']} {best['save_pct']}")
        return best

    print(f"  {team_abbr} goalie: default")
    return {"name": "Unknown", "save_pct": DEFAULT_SAVE_PCT, "gp": 0}


def get_player_season_stats(player_name, team_abbr):
    """Season SOG/g and TOI for a player."""
    data = api_get(f"{NHL_API}/roster/{team_abbr}/current", f"roster/{team_abbr}")
    if not data:
        return None

    all_players = data.get("forwards", []) + data.get("defensemen", [])
    target_last = player_name.lower().split()[-1]
    target_full = player_name.lower().strip()

    for p in all_players:
        fname = safe_get(p, "firstName", "default") or str(p.get("firstName", ""))
        lname = safe_get(p, "lastName", "default") or str(p.get("lastName", ""))
        full = f"{fname} {lname}".lower().strip()

        if full == target_full or lname.lower() == target_last:
            pid = p.get("id")
            if not pid:
                continue
            sdata = api_get(f"{NHL_API}/player/{pid}/landing", f"player/{pid}")
            if not sdata:
                continue

            sub = safe_get(sdata, "featuredStats", "regularSeason", "subSeason") or {}
            if not sub:
                totals = sdata.get("seasonTotals", [])
                for t in totals:
                    if t.get("gamesPlayed", 0) > 0:
                        sub = t
                        break

            gp = max(sub.get("gamesPlayed", 1) or sub.get("gp", 1) or 1, 1)
            shots = sub.get("shots", 0) or sub.get("sog", 0) or 0
            toi_str = sub.get("avgToi", "17:00") or "17:00"

            try:
                parts = str(toi_str).split(":")
                toi_dec = int(parts[0]) + int(parts[1]) / 60 if len(parts) == 2 else 17.0
            except:
                toi_dec = 17.0

            return {"season_sog": round(shots / gp, 1), "toi": round(toi_dec, 1), "gp": gp}

    return None


# ── DailyFaceoff scraper ───────────────────────────────────────────────────────
def scrape_dailyfaceoff_lines(team_abbr):
    abbr_to_slug = {
        "ANA": "anaheim-ducks", "BOS": "boston-bruins", "BUF": "buffalo-sabres",
        "CGY": "calgary-flames", "CAR": "carolina-hurricanes", "CHI": "chicago-blackhawks",
        "COL": "colorado-avalanche", "CBJ": "columbus-blue-jackets", "DAL": "dallas-stars",
        "DET": "detroit-red-wings", "EDM": "edmonton-oilers", "FLA": "florida-panthers",
        "MIN": "minnesota-wild", "MTL": "montreal-canadiens", "NSH": "nashville-predators",
        "NJD": "new-jersey-devils", "NYI": "new-york-islanders", "NYR": "new-york-rangers",
        "OTT": "ottawa-senators", "PHI": "philadelphia-flyers", "PIT": "pittsburgh-penguins",
        "SJS": "san-jose-sharks", "SEA": "seattle-kraken", "STL": "st-louis-blues",
        "TBL": "tampa-bay-lightning", "TOR": "toronto-maple-leafs", "UTA": "utah-mammoth",
        "VAN": "vancouver-canucks", "VGK": "vegas-golden-knights", "WSH": "washington-capitals",
        "WPG": "winnipeg-jets",
    }

    slug = abbr_to_slug.get(team_abbr, "")
    if not slug:
        return {"lines": [], "pp_units": [], "raw_html_ok": False}

    url = f"https://www.dailyfaceoff.com/teams/{slug}/line-combinations/"
    print(f"  Scraping: {url}")

    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        result = {"lines": [], "pp_units": [], "raw_html_ok": True}

        # Grab all player links — DailyFaceoff player links contain /players/
        player_links = soup.find_all("a", href=re.compile(r"/players/"))
        seen = []
        for a in player_links:
            name = a.get_text(strip=True)
            if name and len(name) > 3 and name not in seen:
                seen.append(name)

        # First 12 = 4 forward lines of 3
        for i in range(0, min(len(seen), 12), 3):
            chunk = seen[i:i+3]
            if len(chunk) == 3:
                result["lines"].append({
                    "line": len(result["lines"]) + 1,
                    "lw": chunk[0], "c": chunk[1], "rw": chunk[2],
                })

        # PP units — look for section headings
        pp_texts = soup.find_all(string=re.compile(r"Power Play|PP1|PP2", re.I))
        pp_players_seen = []
        for pt in pp_texts[:2]:
            parent = pt.find_parent()
            if not parent:
                continue
            unit_num = 1 if not pp_players_seen else 2
            pp_links = parent.find_all_next("a", href=re.compile(r"/players/"), limit=5)
            names = [a.get_text(strip=True) for a in pp_links if a.get_text(strip=True) not in pp_players_seen]
            if names:
                result["pp_units"].append({"unit": unit_num, "players": names[:5]})
                pp_players_seen.extend(names[:5])

        print(f"  {team_abbr}: {len(result['lines'])} lines, {len(result['pp_units'])} PP units")
        return result

    except Exception as e:
        print(f"  Error scraping {team_abbr}: {e}")
        return {"lines": [], "pp_units": [], "raw_html_ok": False, "error": str(e)}


# ── Build player data ──────────────────────────────────────────────────────────
def build_player_data(games, lines_data, goalie_data):
    players = {}
    home_teams = {g["home"] for g in games}

    for team_abbr, team_lines in lines_data.items():
        if not team_lines:
            continue
        is_home = team_abbr in home_teams

        pp_assignment = {}
        for pp in team_lines.get("pp_units", []):
            for name in pp.get("players", []):
                if name and name.strip():
                    pp_assignment[name.strip().lower()] = pp["unit"]

        for line in team_lines.get("lines", []):
            for pos, name in [("LW", line.get("lw")), ("C", line.get("c")), ("RW", line.get("rw"))]:
                if not name or name in players:
                    continue
                api_stats = get_player_season_stats(name, team_abbr)
                season_sog = api_stats["season_sog"] if api_stats else 2.0
                toi = api_stats["toi"] if api_stats else 17.0
                pp_unit = pp_assignment.get(name.lower(), 0)
                pp_toi = 2.5 if pp_unit == 1 else 1.2 if pp_unit == 2 else 0.0

                players[name] = {
                    "season": season_sog, "l10": season_sog,
                    "toi": toi, "toi_trend": "stable",
                    "pp": pp_unit, "pp_toi": pp_toi,
                    "team": team_abbr, "pos": pos,
                    "home": is_home, "b2b": False,
                    "home_sog": round(season_sog * 1.05, 1),
                    "away_sog": round(season_sog * 0.95, 1),
                }

    return players


# ── Main ───────────────────────────────────────────────────────────────────────
def run():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"\n=== NHL Scraper {today} ===\n")

    games = get_tonight_games()
    if not games:
        out = {"date": today, "games": [], "players": {}, "goalie_vs": {}, "shots_against": {}, "error": "No games tonight"}
        (DATA_DIR / "today.json").write_text(json.dumps(out, indent=2))
        print("No games — saved empty result")
        return out

    all_teams = list({g["away"] for g in games} | {g["home"] for g in games})
    goalie_data = {}
    shots_against = {}

    for team in all_teams:
        print(f"\n--- {team} ---")
        goalie_data[team] = get_goalie_stats(team)
        shots_against[team] = get_team_stats(team)

    goalie_vs = {}
    for g in games:
        goalie_vs[g["away"]] = goalie_data[g["home"]]["save_pct"]
        goalie_vs[g["home"]] = goalie_data[g["away"]]["save_pct"]

    print(f"\n--- Scraping DailyFaceoff ---")
    lines_data = {team: scrape_dailyfaceoff_lines(team) for team in all_teams}

    print(f"\n--- Building player data ---")
    players = build_player_data(games, lines_data, goalie_data)

    app_games = []
    for g in games:
        try:
            dt = datetime.fromisoformat(g["time"].replace("Z", "+00:00"))
            hour = (dt.hour - 5) % 24
            ampm = "PM" if hour >= 12 else "AM"
            time_str = f"{hour % 12 or 12}:{str(dt.minute).zfill(2)} {ampm}"
        except:
            time_str = "TBD"
        app_games.append({"away": g["away"], "home": g["home"], "time": time_str})

    output = {
        "date": today,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "games": app_games,
        "players": players,
        "goalie_vs": goalie_vs,
        "goalie_details": goalie_data,
        "shots_against": shots_against,
        "scrape_status": {t: lines_data[t].get("raw_html_ok", False) for t in all_teams},
    }

    (DATA_DIR / "today.json").write_text(json.dumps(output, indent=2))
    print(f"\n✓ Done — {len(players)} players, {len(app_games)} games")
    return output


if __name__ == "__main__":
    run()
