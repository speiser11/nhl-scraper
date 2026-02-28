"""
NHL Scraper API Server
Serves today's scraped data to the NHL Bet Model app.
Runs the scraper automatically at 10am ET daily.
"""

from flask import Flask, jsonify
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from pathlib import Path
import json
import logging
from datetime import datetime, timezone

from scraper import run as run_scraper

app = Flask(__name__)
CORS(app)  # Allow requests from Claude artifact (any origin)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_FILE = Path("data/today.json")


def load_today():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return None


@app.route("/")
def index():
    return jsonify({"status": "NHL Scraper API running", "endpoints": ["/today", "/health", "/refresh"]})


@app.route("/health")
def health():
    data = load_today()
    return jsonify({
        "status": "ok",
        "data_available": data is not None,
        "data_date": data.get("date") if data else None,
        "scraped_at": data.get("scraped_at") if data else None,
    })


@app.route("/today")
def today():
    """Main endpoint — returns today's full scraped data for the app."""
    data = load_today()
    if not data:
        return jsonify({"error": "No data available yet. Scraper may not have run today."}), 404

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if data.get("date") != today_str:
        return jsonify({
            "warning": f"Data is from {data.get('date')}, not today ({today_str}). Scraper may not have run yet.",
            **data
        })

    return jsonify(data)


@app.route("/refresh")
def refresh():
    """Manually trigger a scraper run. Useful if you need to re-pull mid-day."""
    logger.info("Manual refresh triggered")
    try:
        run_scraper()
        data = load_today()
        return jsonify({"status": "ok", "message": "Scraper ran successfully", "players": len(data.get("players", {}))})
    except Exception as e:
        logger.error(f"Scraper error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


def scheduled_scrape():
    logger.info("Scheduled scrape starting...")
    try:
        run_scraper()
        logger.info("Scheduled scrape complete")
    except Exception as e:
        logger.error(f"Scheduled scrape failed: {e}")


if __name__ == "__main__":
    # Run scraper once on startup if no data for today
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    existing = load_today()
    if not existing or existing.get("date") != today_str:
        logger.info("No data for today — running scraper on startup...")
        try:
            run_scraper()
        except Exception as e:
            logger.error(f"Startup scrape failed: {e}")

    # Schedule daily scrape at 10am ET (15:00 UTC)
    scheduler = BackgroundScheduler()
    scheduler.add_job(scheduled_scrape, "cron", hour=15, minute=0)
    scheduler.start()
    logger.info("Scheduler started — scraper will run daily at 10am ET")

    app.run(host="0.0.0.0", port=8080)
