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

# Map DailyFaceoff team names to NHL API abbreviations
TEAM_MAP = {
    "Anaheim Ducks": "ANA", "Boston Bruins": "BOS", "Buffalo Sabres": "BUF",
    "Calgary Flames": "CGY", "Carolina Hurricanes": "CAR", "Chicago Blackhawks": "CHI",
    "Colorado Avalanche": "COL", "Columbus Blue Jackets": "CBJ", "Dallas Stars": "DAL",
    "Detroit Red Wings": "DET", "Edmonton Oilers": "EDM", "Florida Panthers": "FLA",
    "Minnesota Wild": "MIN", "Montreal Canadiens": "MTL", "Nashville Predators": "NSH",
    "New Jersey Devils": "NJD", "New York Islanders": "NYI", "New York Rangers": "NYR",
    "Ottawa Senators": "OTT", "Philadelphia Flyers": "PHI", "Pittsburgh Penguins": "PIT",
    "San Jose Sharks": "SJS", "Seattle Kraken": "SEA", "St. Louis Blues": "STL",
    "Tampa Bay Lightning": "TBL", "Toronto Maple Leafs": "TOR", "Utah Mammoth": "UTA",
    "Vancouver Canucks": "VAN", "Vegas Golden Knights": "VGK", "Washington Capitals": "WSH",
    "Winnipeg Jets": "WPG",
}


# ── NHL API helpers ────────────────────────────────────────────────────────────
def get_tonight_games():
    """Fetch tonight's NHL schedule from the official API."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    url = f"{NHL_API}/schedule/{today}"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        games = []
        for game_week in data.get("gameWeek", []):
            if game_week.get("date") == today:
                for g in game_week.get("games", []):
                    away = g["awayTeam"]["abbrev"]
                    home = g["homeTeam"]["abbrev"]
                    game_time = g.get("startTimeUTC", "")
                    games.append({"away": away, "home": home, "time": game_time, "id": g["id"]})
        print(f"Found {len(games)} games tonight: {[f'{g['away']}@{g['home']}' for g in games]}")
        return games
    except Exception as e:
        print(f"Error fetching schedule: {e}")
        return []


def get_team_stats(team_abbr):
    """Get team shots-against per game from NHL API."""
    try:
        url = f"{NHL_API}/club-stats/{team_abbr}/now"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        # shots against per game
        sa_pg = data.get("teamStats", {}).get("shotsAgainstPerGame", None)
        return round(float(sa_pg), 1) if sa_pg else 29.0
    except Exception as e:
        print(f"  Warning: Could not get team stats for {team_abbr}: {e}")
        return 29.0


def get_goalie_stats(team_abbr):
    """Get starting goalie and their SV% from NHL API roster + stats."""
    try:
        # Get roster
        url = f"{NHL_API}/roster/{team_abbr}/current"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        roster = r.json()

        goalies = roster.get("goalies", [])
        best_goalie = None
        best_gp = 0

        for g in goalies:
            pid = g["id"]
            # Get player stats
            stats_url = f"{NHL_API}/player/{pid}/landing"
            sr = requests.get(stats_url, timeout=10)
            if not sr.ok:
                continue
            sdata = sr.json()
            season_stats = sdata.get("featuredStats", {}).get("regularSeason", {}).get("subSeason", {})
            gp = season_stats.get("gamesPlayed", 0)
            svp = season_stats.get("savePctg", None)
            name = f"{g.get('firstName', {}).get('default', '')} {g.get('lastName', {}).get('default', '')}"
            if gp > best_gp and svp is not None:
                best_gp = gp
                best_goalie = {"name": name, "save_pct": round(float(svp), 3), "gp": gp}

        if best_goalie:
            print(f"  Goalie {team_abbr}: {best_goalie['name']} {best_goalie['save_pct']}")
            return best_goalie
        return {"name": "Unknown", "save_pct": 0.910, "gp": 0}

    except Exception as e:
        print(f"  Warning: Could not get goalie for {team_abbr}: {e}")
        return {"name": "Unknown", "save_pct": 0.910, "gp": 0}


def get_player_season_stats(player_name, team_abbr):
    """Search for a player and return their season SOG stats."""
    try:
        # Search by name
        search_url = f"{NHL_API}/player/{player_name.lower().replace(' ', '-')}/landing"
        # Try roster approach instead - find player on team roster
        roster_url = f"{NHL_API}/roster/{team_abbr}/current"
        r = requests.get(roster_url, timeout=10)
        if not r.ok:
            return None
        roster = r.json()

        all_players = roster.get("forwards", []) + roster.get("defensemen", [])
        target = player_name.lower().strip()

        for p in all_players:
            fname = p.get("firstName", {}).get("default", "")
            lname = p.get("lastName", {}).get("default", "")
            full = f"{fname} {lname}".lower()
            if full == target or lname.lower() == target.split()[-1].lower():
                pid = p["id"]
                stats_url = f"{NHL_API}/player/{pid}/landing"
                sr = requests.get(stats_url, timeout=10)
                if not sr.ok:
                    continue
                sdata = sr.json()
                sub = sdata.get("featuredStats", {}).get("regularSeason", {}).get("subSeason", {})
                gp = sub.get("gamesPlayed", 1)
                shots = sub.get("shots", 0)
                toi_str = sub.get("avgToi", "0:00")
                # Parse TOI mm:ss -> decimal
                parts = toi_str.split(":")
                toi_dec = int(parts[0]) + int(parts[1]) / 60 if len(parts) == 2 else 0
                sog_pg = round(shots / max(gp, 1), 1)
                return {
                    "season_sog": sog_pg,
                    "toi": round(toi_dec, 1),
                    "gp": gp,
                    "id": pid,
                }
    except Exception as e:
        print(f"  Warning: Could not get stats for {player_name}: {e}")
    return None


# ── DailyFaceoff scraper ───────────────────────────────────────────────────────
def scrape_dailyfaceoff_lines(team_abbr):
    """
    Scrape line combinations and PP units for a team from DailyFaceoff.
    Returns dict with lines and pp_units.
    """
    # DailyFaceoff uses full team names in URLs
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
        return None

    url = f"https://www.dailyfaceoff.com/teams/{slug}/line-combinations/"
    print(f"  Scraping {url}")

    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        result = {
            "lines": [],      # [{"line": 1, "lw": "Name", "c": "Name", "rw": "Name"}]
            "pp_units": [],   # [{"unit": 1, "players": ["Name", ...]}]
            "raw_html_ok": True,
        }

        # ── Parse even-strength lines ──
        # DailyFaceoff line tables have class patterns like "line-combos"
        line_tables = soup.find_all("table", class_=re.compile(r"line", re.I))
        if not line_tables:
            # Try finding by section headers
            line_tables = soup.find_all("div", class_=re.compile(r"line-combo", re.I))

        for table in line_tables[:4]:  # lines 1-4
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all("td")
                if len(cells) >= 3:
                    players = [c.get_text(strip=True) for c in cells if c.get_text(strip=True)]
                    if len(players) >= 3:
                        result["lines"].append({
                            "line": len(result["lines"]) + 1,
                            "lw": players[0],
                            "c": players[1],
                            "rw": players[2],
                        })

        # ── Parse PP units ──
        pp_sections = soup.find_all(string=re.compile(r"Power Play", re.I))
        for pp_text in pp_sections:
            parent = pp_text.parent
            # Look for player names near this section
            unit_num = 1 if "1" in str(pp_text) else 2
            nearby = parent.find_next("table") or parent.find_next("ul")
            if nearby:
                names = [el.get_text(strip=True) for el in nearby.find_all(["td", "li", "span"])
                         if el.get_text(strip=True) and len(el.get_text(strip=True)) > 3]
                if names:
                    result["pp_units"].append({"unit": unit_num, "players": names[:5]})

        print(f"  {team_abbr}: {len(result['lines'])} lines, {len(result['pp_units'])} PP units found")
        return result

    except requests.RequestException as e:
        print(f"  Error scraping {team_abbr}: {e}")
        return {"lines": [], "pp_units": [], "raw_html_ok": False, "error": str(e)}


# ── Build player objects ───────────────────────────────────────────────────────
def build_player_data(games, lines_data, goalie_data, team_stats_data):
    """
    Combine line data with NHL API stats to build PLAYER_DATA object
    matching the format expected by the app.
    """
    players = {}

    # Determine which teams are home vs away
    home_teams = {g["home"] for g in games}
    away_teams = {g["away"] for g in games}

    for team_abbr, team_lines in lines_data.items():
        is_home = team_abbr in home_teams

        # Determine PP unit per player
        pp_assignment = {}
        for pp in team_lines.get("pp_units", []):
            for name in pp.get("players", []):
                pp_assignment[name.lower()] = pp["unit"]

        # Process each line
        for line in team_lines.get("lines", []):
            for pos, name in [("LW", line.get("lw")), ("C", line.get("c")), ("RW", line.get("rw"))]:
                if not name or name in players:
                    continue

                # Get stats from NHL API
                api_stats = get_player_season_stats(name, team_abbr)
                season_sog = api_stats["season_sog"] if api_stats else 2.0
                toi = api_stats["toi"] if api_stats else 17.0

                pp_unit = pp_assignment.get(name.lower(), 0)
                # Estimate PP TOI based on unit
                pp_toi = 2.5 if pp_unit == 1 else 1.2 if pp_unit == 2 else 0.0

                players[name] = {
                    "season": season_sog,
                    "l10": season_sog,      # Will need manual override or game log calc
                    "toi": toi,
                    "toi_trend": "stable",  # Would need multi-game history to calculate
                    "pp": pp_unit,
                    "pp_toi": pp_toi,
                    "team": team_abbr,
                    "pos": pos,
                    "home": is_home,
                    "b2b": False,           # Would need schedule cross-reference
                    "home_sog": round(season_sog * 1.05, 1),
                    "away_sog": round(season_sog * 0.95, 1),
                }

    return players


# ── Main ───────────────────────────────────────────────────────────────────────
def run():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"\n=== NHL Scraper running for {today} ===\n")

    # 1. Get tonight's schedule
    games = get_tonight_games()
    if not games:
        print("No games found tonight.")
        output = {"date": today, "games": [], "players": {}, "goalie_vs": {}, "shots_against": {}, "error": "No games tonight"}
        (DATA_DIR / "today.json").write_text(json.dumps(output, indent=2))
        return

    # 2. Get team stats + goalie data for all teams playing
    all_teams = list({g["away"] for g in games} | {g["home"] for g in games})
    print(f"\nFetching team stats for: {all_teams}")

    goalie_data = {}
    shots_against = {}
    for team in all_teams:
        print(f"\n{team}:")
        goalie_data[team] = get_goalie_stats(team)
        shots_against[team] = get_team_stats(team)

    # goalie_vs: for each team, the SV% of the goalie they're SHOOTING AGAINST tonight
    goalie_vs = {}
    for g in games:
        # Away team shoots against home goalie
        goalie_vs[g["away"]] = goalie_data[g["home"]]["save_pct"]
        # Home team shoots against away goalie
        goalie_vs[g["home"]] = goalie_data[g["away"]]["save_pct"]

    # 3. Scrape DailyFaceoff for lines + PP units
    print(f"\nScraping DailyFaceoff lines...")
    lines_data = {}
    for team in all_teams:
        lines_data[team] = scrape_dailyfaceoff_lines(team)

    # 4. Build player data
    print(f"\nBuilding player data...")
    players = build_player_data(games, lines_data, goalie_data, shots_against)

    # 5. Format games for the app
    app_games = []
    for g in games:
        # Convert UTC time to readable ET
        try:
            dt = datetime.fromisoformat(g["time"].replace("Z", "+00:00"))
            # Simple UTC-5 offset for ET
            hour = (dt.hour - 5) % 24
            ampm = "PM" if hour >= 12 else "AM"
            hour12 = hour % 12 or 12
            time_str = f"{hour12}:00 {ampm}"
        except:
            time_str = g["time"]
        app_games.append({"away": g["away"], "home": g["home"], "time": time_str})

    # 6. Save output
    output = {
        "date": today,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "games": app_games,
        "players": players,
        "goalie_vs": goalie_vs,
        "goalie_details": goalie_data,
        "shots_against": shots_against,
        "scrape_status": {
            team: lines_data[team].get("raw_html_ok", False)
            for team in all_teams
        }
    }

    out_path = DATA_DIR / "today.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"\n✓ Saved to {out_path}")
    print(f"  {len(players)} players, {len(app_games)} games")
    print(f"  Scrape success: {output['scrape_status']}")


if __name__ == "__main__":
    run()
