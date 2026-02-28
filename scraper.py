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
import threading
from datetime import datetime, timezone



app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_FILE = Path("data/today.json")
scraper_running = False
scraper_status = "idle"


def load_today():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return None


def run_scraper_background():
    global scraper_running, scraper_status
    if scraper_running:
        logger.info("Scraper already running, skipping")
        return
    scraper_running = True
    scraper_status = "running"
    try:
        logger.info("Scraper starting...")
        run_scraper()
        scraper_status = "done"
        logger.info("Scraper complete")
    except Exception as e:
        scraper_status = f"error: {e}"
        logger.error(f"Scraper failed: {e}")
    finally:
        scraper_running = False


@app.route("/")
def index():
    return jsonify({"status": "NHL Scraper API", "endpoints": ["/today", "/health", "/refresh"]})


@app.route("/health")
def health():
    data = load_today()
    return jsonify({
        "status": "ok",
        "scraper_status": scraper_status,
        "data_available": data is not None,
        "data_date": data.get("date") if data else None,
        "scraped_at": data.get("scraped_at") if data else None,
        "player_count": len(data.get("players", {})) if data else 0,
    })


@app.route("/today")
def today():
    data = load_today()
    if not data:
        return jsonify({"error": "No data yet — hit /refresh to run the scraper"}), 404
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if data.get("date") != today_str:
        return jsonify({"warning": f"Data is from {data.get('date')}, not today. Hit /refresh.", **data})
    return jsonify(data)


@app.route("/refresh")
def refresh():
    """Trigger scraper in background — returns immediately."""
    if scraper_running:
        return jsonify({"status": "already_running", "message": "Scraper is already running, check /health for progress"})
    thread = threading.Thread(target=run_scraper_background, daemon=True)
    thread.start()
    return jsonify({"status": "started", "message": "Scraper started in background. Check /health in 2-3 minutes for results."})


def scheduled_scrape():
    logger.info("Scheduled scrape starting...")
    run_scraper_background()


if __name__ == "__main__":
    # Run scraper on startup if no data for today
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    existing = load_today()
    if not existing or existing.get("date") != today_str:
        logger.info("No data for today — running scraper on startup...")
        thread = threading.Thread(target=run_scraper_background, daemon=True)
        thread.start()

    scheduler = BackgroundScheduler()
    scheduler.add_job(scheduled_scrape, "cron", hour=15, minute=0)
    scheduler.start()
    logger.info("Scheduler started — scraper runs daily at 10am ET")

    app.run(host="0.0.0.0", port=8080)
