# NHL Scraper Backend

Pulls tonight's NHL line combinations, PP units, goalie stats, and team data from DailyFaceoff + the NHL public API. Serves it to the NHL Bet Model app via a simple REST API.

## What it does

- **10am ET daily**: automatically scrapes DailyFaceoff for line combos and PP units
- **NHL API**: pulls goalie SV%, shots-against per game, and player season stats
- **`/today` endpoint**: serves everything the app needs in one JSON response
- **`/refresh`**: manually re-run the scraper mid-day (useful after lineup changes)

## Files

| File | Purpose |
|------|---------|
| `scraper.py` | All scraping and NHL API logic |
| `server.py` | Flask API server with daily scheduler |
| `requirements.txt` | Python dependencies |
| `railway.toml` | Railway deployment config |
| `Procfile` | Process definition for Railway/Render |

---

## Deployment — Step by Step

### Step 1: Create a GitHub account
1. Go to [github.com](https://github.com) and sign up (free)
2. Verify your email

### Step 2: Create a new repository
1. Click the **+** icon in the top right → **New repository**
2. Name it: `nhl-scraper`
3. Set to **Private** (recommended)
4. Check **"Add a README file"**
5. Click **Create repository**

### Step 3: Upload these files
1. In your new repo, click **Add file** → **Upload files**
2. Drag and drop ALL of these files:
   - `scraper.py`
   - `server.py`
   - `requirements.txt`
   - `railway.toml`
   - `Procfile`
   - `.gitignore`
3. Click **Commit changes**

### Step 4: Deploy to Railway
1. Go to [railway.app](https://railway.app) and sign up with your GitHub account
2. Click **New Project** → **Deploy from GitHub repo**
3. Select your `nhl-scraper` repository
4. Railway will auto-detect the config and start deploying
5. Wait ~2 minutes for the build to finish
6. Click on your deployment → **Settings** → copy the **Public URL**
   - It will look like: `https://nhl-scraper-production-xxxx.up.railway.app`

### Step 5: Test it
Open your browser and visit:
```
https://your-url.up.railway.app/health
https://your-url.up.railway.app/today
```
You should see JSON data. If `/today` says "No data yet", hit:
```
https://your-url.up.railway.app/refresh
```
This manually triggers the scraper.

### Step 6: Add your URL to the app
In the NHL Bet Model app, there will be a settings field where you paste your Railway URL. Once set, the app will fetch live data automatically every day instead of using hardcoded player data.

---

## Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | API info |
| `GET /health` | Check if server is running and data is fresh |
| `GET /today` | Full today's data (games, players, goalies, shots-against) |
| `GET /refresh` | Manually trigger a scraper run |

## Response format (`/today`)

```json
{
  "date": "2026-02-27",
  "scraped_at": "2026-02-27T15:01:23Z",
  "games": [
    { "away": "BUF", "home": "FLA", "time": "7:00 PM" }
  ],
  "players": {
    "Sam Reinhart": {
      "season": 2.7, "l10": 2.9, "toi": 19.0,
      "toi_trend": "stable", "pp": 1, "pp_toi": 2.6,
      "team": "FLA", "pos": "RW", "home": true, "b2b": false,
      "home_sog": 2.9, "away_sog": 2.5
    }
  },
  "goalie_vs": { "FLA": 0.906, "BUF": 0.912 },
  "shots_against": { "FLA": 27.2, "BUF": 29.0 },
  "scrape_status": { "FLA": true, "BUF": true }
}
```

## Notes

- **DailyFaceoff scraping**: if their HTML structure changes, `scraper.py` may need updating
- **L10 stats**: currently defaults to season average — a future update will calculate from game logs
- **B2B detection**: currently defaults to false — can be added by cross-referencing the schedule
- **Free tier**: Railway free tier may sleep after inactivity. Hit `/refresh` if data seems stale.

## Troubleshooting

**`/today` returns stale data**: hit `/refresh` to re-run the scraper manually

**Scraper returns empty lines**: DailyFaceoff may have changed their HTML. Check the `scrape_status` field — `false` means the scrape failed for that team.

**Railway build fails**: check that all 6 files are uploaded to GitHub correctly
