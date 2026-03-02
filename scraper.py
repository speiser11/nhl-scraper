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
try:
    from zoneinfo import ZoneInfo
    _CT = ZoneInfo("America/Chicago")
except Exception:
    _CT = None

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
    game_list = [g["away"] + "@" + g["home"] for g in games]
    print(f"Games tonight: {game_list}")
    return games


def get_team_stats(team_abbr):
    """Shots-against per game — tries club-stats endpoint, falls back to standings."""
    # Try the club-stats endpoint first
    for endpoint in [f"club-stats/{team_abbr}/now", f"club-stats-season/{team_abbr}"]:
        data = api_get(f"{NHL_API}/{endpoint}", endpoint)
        if data:
            # Response may be a dict or a list — normalize to dict
            d = data[0] if isinstance(data, list) and data else data if isinstance(data, dict) else {}
            for field in ["shotsAgainstPerGame", "shotsAgainst", "saPerGame", "sa"]:
                val = d.get(field)
                if val and float(val) > 15:
                    result = round(float(val), 1)
                    print(f"  {team_abbr} SA/g: {result}")
                    return result

    # Fall back to standings
    data = api_get(f"{NHL_API}/standings/now", "standings")
    if data:
        for team in data.get("standings", []):
            abbrev = safe_get(team, "teamAbbrev", "default") or team.get("teamAbbrev", "")
            if abbrev == team_abbr:
                gp = team.get("gamesPlayed", 1) or 1
                for field in ["shotsAgainst", "shotsAgainstPerGame", "sa"]:
                    val = team.get(field)
                    if val:
                        sa = float(val)
                        result = sa if sa < 50 else round(sa / gp, 1)
                        print(f"  {team_abbr} SA/g: {result} (standings/{field})")
                        return result
                # Debug: show shot-related keys so we can fix the field name if still failing
                shot_keys = [k for k in team.keys() if any(x in k.lower() for x in ["shot", "sa", "sog"])]
                print(f"  {team_abbr}: no SA/g field found. Available shot-related keys: {shot_keys}")
                break

    print(f"  {team_abbr} SA/g: default {DEFAULT_SHOTS_AGAINST}")
    return DEFAULT_SHOTS_AGAINST


def get_goalie_stats(team_abbr, confirmed_name=None):
    """Best goalie SV% from roster. Uses confirmed_name if provided, else most-games-played."""
    data = api_get(f"{NHL_API}/roster/{team_abbr}/current", f"roster/{team_abbr}")
    if not data:
        return {"name": "Unknown", "save_pct": DEFAULT_SAVE_PCT, "gp": 0, "confirmed": False}

    best = None
    best_gp = 0
    confirmed_result = None

    for g in data.get("goalies", []):
        pid = g.get("id")
        if not pid:
            continue
        sdata = api_get(f"{NHL_API}/player/{pid}/landing", f"goalie/{pid}")
        if not sdata:
            continue

        sub = (
            safe_get(sdata, "featuredStats", "regularSeason", "subSeason") or
            safe_get(sdata, "last5Games", 0) or
            {}
        )
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

        if confirmed_name and svp is not None and not confirmed_result:
            if (lname.lower() == confirmed_name.lower().split()[-1] or
                    name.lower() == confirmed_name.lower()):
                confirmed_result = {"name": name, "save_pct": round(float(svp), 3), "gp": gp, "confirmed": True}

        if gp > best_gp and svp is not None:
            best_gp = gp
            best = {"name": name, "save_pct": round(float(svp), 3), "gp": gp, "confirmed": False}

    if confirmed_result:
        print(f"  {team_abbr} goalie: CONFIRMED {confirmed_result['name']} {confirmed_result['save_pct']}")
        return confirmed_result

    if best:
        print(f"  {team_abbr} goalie selected: {best['name']} {best['save_pct']}")
        return best

    print(f"  {team_abbr} goalie: default")
    return {"name": "Unknown", "save_pct": DEFAULT_SAVE_PCT, "gp": 0, "confirmed": False}


def get_player_l10_sog(pid):
    """Average SOG over last 10 regular season games from game log."""
    data = api_get(f"{NHL_API}/player/{pid}/game-log/now", f"gamelog/{pid}")
    if not data:
        return None
    # gameTypeId 2 = regular season; log is newest-first
    regular = [g for g in data.get("gameLog", []) if g.get("gameTypeId") == 2]
    last10 = regular[:10]
    if not last10:
        return None
    return round(sum(g.get("shots", 0) for g in last10) / len(last10), 1)


def get_player_season_stats(player_name, team_abbr):
    """Season SOG/g, L10 SOG/g, and TOI for a player."""
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

            season_sog = round(shots / gp, 1)
            l10_sog = get_player_l10_sog(pid)
            return {"season_sog": season_sog, "l10_sog": l10_sog or season_sog, "toi": round(toi_dec, 1), "gp": gp}

    return None


# ── Natural Stat Trick ─────────────────────────────────────────────────────────
NST_NAME_TO_ABBR = {
    "anaheim ducks":"ANA","boston bruins":"BOS","buffalo sabres":"BUF",
    "calgary flames":"CGY","carolina hurricanes":"CAR","chicago blackhawks":"CHI",
    "colorado avalanche":"COL","columbus blue jackets":"CBJ","dallas stars":"DAL",
    "detroit red wings":"DET","edmonton oilers":"EDM","florida panthers":"FLA",
    "minnesota wild":"MIN","montreal canadiens":"MTL","nashville predators":"NSH",
    "new jersey devils":"NJD","new york islanders":"NYI","new york rangers":"NYR",
    "ottawa senators":"OTT","philadelphia flyers":"PHI","pittsburgh penguins":"PIT",
    "san jose sharks":"SJS","seattle kraken":"SEA","st louis blues":"STL",
    "tampa bay lightning":"TBL","toronto maple leafs":"TOR",
    "utah mammoth":"UTA","utah hockey club":"UTA",
    "vancouver canucks":"VAN","vegas golden knights":"VGK","washington capitals":"WSH",
    "winnipeg jets":"WPG","los angeles kings":"LAK",
}

def get_nst_team_stats():
    """Scrape SA/g, CF%, and xGF% for all teams from Natural Stat Trick."""
    now = datetime.now(timezone.utc)
    year = now.year
    season = f"{year}{year+1}" if now.month >= 10 else f"{year-1}{year}"
    url = (f"https://www.naturalstattrick.com/teamtable.php"
           f"?fromseason={season}&thruseason={season}&stype=2&sit=all"
           f"&score=all&rate=n&team=all&loc=B&gpf=&gpt=&fd=&td=")
    print(f"  NST URL: {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.find("table")
        if not table:
            print("  NST: table not found")
            return {}

        # Find headers — th's are inside tr inside thead, not direct children of thead
        header_row = table.find("thead").find("tr") if table.find("thead") else None
        if not header_row:
            print("  NST: no header row found")
            return {}
        headers = [th.get_text(strip=True) for th in header_row.find_all("th")]
        print(f"  NST headers ({len(headers)}): {headers[:10]}")
        idx = {h: i for i, h in enumerate(headers)}

        out = {}
        tbody = table.find("tbody")
        rows = tbody.find_all("tr") if tbody else table.find_all("tr")[1:]
        for i, row in enumerate(rows):
            cells = [td.get_text(strip=True) for td in row.find_all("td", recursive=False)]
            if not cells:
                continue
            if i < 3:
                print(f"  NST debug row {i}: {cells[:5]} ({len(cells)} cols)")
            # Find team name — search cells for a known team name
            team_name = None
            team_col = idx.get("Team", 1)
            if team_col < len(cells):
                team_name = cells[team_col].lower().strip()
            abbr = NST_NAME_TO_ABBR.get(team_name) if team_name else None
            if not abbr:
                continue
            try:
                gp  = int(cells[idx["GP"]]) or 1
                sa  = float(cells[idx["SA"]]) if "SA" in idx else 0
                cf  = float(cells[idx["CF%"]]) if "CF%" in idx else 0
                xgf = float(cells[idx["xGF%"]]) if "xGF%" in idx else 0
                out[abbr] = {
                    "sa_per_game": round(sa / gp, 1),
                    "cf_pct":  cf,
                    "xgf_pct": xgf,
                }
            except (ValueError, IndexError):
                continue

        print(f"  NST: loaded {len(out)} teams")
        return out
    except Exception as e:
        print(f"  NST error: {e}")
        return {}


# ── DailyFaceoff scraper ───────────────────────────────────────────────────────
def scrape_confirmed_goalies():
    """Scrape DailyFaceoff starting goalies page. Returns {team_abbr: {"name":..., "confirmed":bool}}."""
    url = "https://www.dailyfaceoff.com/starting-goalies/"
    SLUG_TO_ABBR = {
        "anaheim-ducks":"ANA","boston-bruins":"BOS","buffalo-sabres":"BUF",
        "calgary-flames":"CGY","carolina-hurricanes":"CAR","chicago-blackhawks":"CHI",
        "colorado-avalanche":"COL","columbus-blue-jackets":"CBJ","dallas-stars":"DAL",
        "detroit-red-wings":"DET","edmonton-oilers":"EDM","florida-panthers":"FLA",
        "minnesota-wild":"MIN","montreal-canadiens":"MTL","nashville-predators":"NSH",
        "new-jersey-devils":"NJD","new-york-islanders":"NYI","new-york-rangers":"NYR",
        "ottawa-senators":"OTT","philadelphia-flyers":"PHI","pittsburgh-penguins":"PIT",
        "san-jose-sharks":"SJS","seattle-kraken":"SEA","st-louis-blues":"STL",
        "tampa-bay-lightning":"TBL","toronto-maple-leafs":"TOR","utah-mammoth":"UTA",
        "vancouver-canucks":"VAN","vegas-golden-knights":"VGK","washington-capitals":"WSH",
        "winnipeg-jets":"WPG",
    }
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        result = {}

        # Find team links, then walk up to find the game card containing goalie info
        team_links = soup.find_all("a", href=re.compile(r"/teams/[\w-]+/"))
        print(f"  Goalie page: {len(team_links)} team links found")

        for tlink in team_links:
            href = tlink.get("href", "")
            m = re.search(r"/teams/([\w-]+)/", href)
            if not m:
                continue
            slug = m.group(1)
            abbr = SLUG_TO_ABBR.get(slug)
            if not abbr or abbr in result:
                continue

            # Walk up from the team link to find a container that also has a player (goalie) link
            container = tlink.find_parent()
            for _ in range(10):
                if not container or container.name in ("body", "html"):
                    break
                goalie_link = container.find("a", href=re.compile(r"/players/"))
                if goalie_link:
                    name = goalie_link.get_text(strip=True)
                    if name and len(name) > 3:
                        text = container.get_text(separator=" ")
                        is_confirmed = bool(re.search(r"\bconfirmed\b", text, re.I))
                        result[abbr] = {"name": name, "confirmed": is_confirmed}
                        break
                container = container.find_parent()

        summary = [f"{k}:{v['name']}" + (" ✓" if v["confirmed"] else "") for k, v in result.items()]
        print(f"  Starting goalies: {', '.join(summary) if summary else 'none found'}")
        return result
    except Exception as e:
        print(f"  Goalie page error: {e}")
        return {}


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
        result = {"lines": [], "d_pairs": [], "pp_units": [], "raw_html_ok": True}

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

        # Links 12-17 = 3 defensive pairs of 2
        for i in range(12, min(len(seen), 18), 2):
            chunk = seen[i:i+2]
            if len(chunk) == 2:
                result["d_pairs"].append({
                    "pair": len(result["d_pairs"]) + 1,
                    "ld": chunk[0], "rd": chunk[1],
                })

        # PP units — look for section headings ("1st Powerplay Unit", "2nd Powerplay Unit")
        pp_texts = soup.find_all(string=re.compile(r"powerplay unit", re.I))
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

        print(f"  {team_abbr}: {len(result['lines'])} lines, {len(result['d_pairs'])} D-pairs, {len(result['pp_units'])} PP units")
        return result

    except Exception as e:
        print(f"  Error scraping {team_abbr}: {e}")
        return {"lines": [], "pp_units": [], "raw_html_ok": False, "error": str(e)}


# ── Build player data ──────────────────────────────────────────────────────────
def get_b2b_teams(games):
    """Return set of team abbrevs playing back-to-back tonight."""
    from datetime import timedelta
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    data = api_get(f"{NHL_API}/schedule/{yesterday}", "yesterday_schedule")
    if not data:
        return set()
    played_yesterday = set()
    for gw in data.get("gameWeek", []):
        if gw.get("date") == yesterday:
            for g in gw.get("games", []):
                away = safe_get(g, "awayTeam", "abbrev", default="")
                home = safe_get(g, "homeTeam", "abbrev", default="")
                if away: played_yesterday.add(away)
                if home: played_yesterday.add(home)
    tonight_teams = {g["away"] for g in games} | {g["home"] for g in games}
    b2b = played_yesterday & tonight_teams
    print(f"  B2B teams tonight: {b2b if b2b else 'none'}")
    return b2b


def build_player_data(games, lines_data, goalie_data, b2b_teams):
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

        skater_slots = (
            [("LW", line.get("lw")) for line in team_lines.get("lines", [])] +
            [("C",  line.get("c"))  for line in team_lines.get("lines", [])] +
            [("RW", line.get("rw")) for line in team_lines.get("lines", [])] +
            [("LD", pair.get("ld")) for pair in team_lines.get("d_pairs", [])] +
            [("RD", pair.get("rd")) for pair in team_lines.get("d_pairs", [])]
        )
        for pos, name in skater_slots:
            if not name or name in players:
                continue
            is_d = pos in ("LD", "RD")
            api_stats = get_player_season_stats(name, team_abbr)
            season_sog = api_stats["season_sog"] if api_stats else (1.5 if is_d else 2.0)
            l10_sog    = api_stats["l10_sog"]    if api_stats else season_sog
            toi        = api_stats["toi"]        if api_stats else (21.0 if is_d else 17.0)
            pp_unit = pp_assignment.get(name.lower(), 0)
            pp_toi = 2.5 if pp_unit == 1 else 1.2 if pp_unit == 2 else 0.0

            players[name] = {
                "season": season_sog, "l10": l10_sog,
                "toi": toi, "toi_trend": "stable",
                "pp": pp_unit, "pp_toi": pp_toi,
                "team": team_abbr, "pos": pos,
                "home": is_home, "b2b": team_abbr in b2b_teams,
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

    print(f"\n--- Confirmed Goalies ---")
    confirmed_goalies = scrape_confirmed_goalies()

    for team in all_teams:
        print(f"\n--- {team} ---")
        conf_name = confirmed_goalies.get(team, {}).get("name")
        goalie_data[team] = get_goalie_stats(team, confirmed_name=conf_name)

    print(f"\n--- Natural Stat Trick ---")
    nst_stats = get_nst_team_stats()
    shots_against = {t: nst_stats[t]["sa_per_game"] if t in nst_stats else DEFAULT_SHOTS_AGAINST for t in all_teams}

    goalie_vs = {}
    for g in games:
        goalie_vs[g["away"]] = goalie_data[g["home"]]["save_pct"]
        goalie_vs[g["home"]] = goalie_data[g["away"]]["save_pct"]

    print(f"\n--- Checking B2B ---")
    b2b_teams = get_b2b_teams(games)

    print(f"\n--- Scraping DailyFaceoff ---")
    lines_data = {team: scrape_dailyfaceoff_lines(team) for team in all_teams}

    print(f"\n--- Building player data ---")
    players = build_player_data(games, lines_data, goalie_data, b2b_teams)

    app_games = []
    for g in games:
        try:
            dt = datetime.fromisoformat(g["time"].replace("Z", "+00:00"))
            if _CT:
                dt_loc = dt.astimezone(_CT)
                h, m = dt_loc.hour, dt_loc.minute
            else:
                h = (dt.hour - 6) % 24  # rough CST fallback
                m = dt.minute
            ampm = "PM" if h >= 12 else "AM"
            time_str = f"{h % 12 or 12}:{str(m).zfill(2)} {ampm} CT"
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
        "nst_team_stats": nst_stats,
        "scrape_status": {t: lines_data[t].get("raw_html_ok", False) for t in all_teams},
    }

    (DATA_DIR / "today.json").write_text(json.dumps(output, indent=2))
    print(f"\n✓ Done — {len(players)} players, {len(app_games)} games")
    push_to_gist(output)
    return output


def push_to_gist(data):
    """Update the GitHub Gist with today's scraped data."""
    import os
    token = os.environ.get("GITHUB_TOKEN")
    gist_id = os.environ.get("GIST_ID")
    if not token or not gist_id:
        print("  Skipping Gist push — GITHUB_TOKEN or GIST_ID not set")
        return
    try:
        url = f"https://api.github.com/gists/{gist_id}"
        payload = {"files": {"nhl-today.json": {"content": json.dumps(data, indent=2)}}}
        r = requests.patch(url, json=payload, headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }, timeout=15)
        if r.ok:
            print(f"  ✓ Gist updated successfully")
        else:
            print(f"  ✗ Gist update failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"  ✗ Gist push error: {e}")


if __name__ == "__main__":
    run()
