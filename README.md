# ExecutionPosting

Automated multi-platform social media posting backend for an AI-tools media page.

When a row in the `ai_tools` table is set to **READY**, the system automatically:

1. Generates platform-specific captions via OpenAI.
2. Downloads the MP4 video.
3. Uploads natively to **LinkedIn**, **Instagram**, **YouTube Shorts**, and **X**.
4. Updates per-platform and overall status in the database.
5. Cleans up the local video file.

---

## Quick Start

```bash
# 1. Clone and enter the project
cd ExecutionPosting

# 2. Create a virtual environment
python -m venv .venv && .venv\Scripts\activate   # Windows
# python -m venv .venv && source .venv/bin/activate  # macOS/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
copy .env.example .env        # then fill in your real keys

# 5. Run
uvicorn app.main:app --reload
```

The scheduler starts automatically and polls every **5 minutes** (configurable via `SCHEDULER_INTERVAL_MINUTES`).

Health check: `GET http://localhost:8000/health`

---

## Render Keep-Alive

To reduce free-tier sleeping on Render, this repo includes a cron service in `render.yaml`:

- Runs every `5` minutes
- Executes `python scripts/keep_alive_ping.py`
- Pings `${KEEP_ALIVE_URL}/health` (fallback: `RENDER_EXTERNAL_URL`)

Set `KEEP_ALIVE_URL` to your Render web service URL if you rename the service.

### UptimeRobot Backup (Recommended)

Add a second external monitor in UptimeRobot so your service gets traffic even if Render cron is delayed.

- **Monitor Type:** HTTP(s)
- **Friendly Name:** `execution-posting-health`
- **URL:** `https://execution-posting-api.onrender.com/healthz`
- **Monitoring Interval:** `5 minutes` (or `2 minutes` on paid plans)
- **Keyword/Port:** leave defaults

This is a best-effort mitigation for free plans and helps reduce cold starts.

---

## Project Structure

```
app/
  main.py                  # FastAPI entrypoint
  config.py                # env-based Settings
  database.py              # SQLAlchemy engine & session
  models.py                # AITool ORM model
  scheduler.py             # APScheduler + retry logic
  services/
    caption_generator.py   # OpenAI captions
    video_downloader.py    # MP4 streamer + cleanup
    linkedin_service.py    # LinkedIn Company Page
    instagram_service.py   # Instagram Reels (Meta Graph API)
    youtube_service.py     # YouTube Shorts (Data API v3)
    x_service.py           # X / Twitter
  utils/
    logger.py              # Structured logging
requirements.txt
.env.example
```

---

## Database

The application expects a PostgreSQL database. On first startup, the `ai_tools` table is created automatically.

| Column             | Type     | Notes                                         |
|--------------------|----------|-----------------------------------------------|
| id                 | Integer  | Primary key                                   |
| tool_name          | String   | Required                                      |
| handle             | String   | Creator social handle                         |
| description        | Text     | Tool description                              |
| website            | String   | Official URL                                  |
| video_url          | String   | Direct MP4 link                               |
| status             | String   | DRAFT / READY / POSTED / FAILED               |
| linkedin_status    | String   | PENDING / SUCCESS / FAILED                    |
| instagram_status   | String   | PENDING / SUCCESS / FAILED                    |
| youtube_status     | String   | PENDING / SUCCESS / FAILED                    |
| x_status           | String   | PENDING / SUCCESS / FAILED                    |
| created_at         | DateTime | Auto-set on insert                            |
| posted_at          | DateTime | Set when status becomes POSTED                |

---

## Retry & Error Handling

* Every platform upload is wrapped with a retry decorator (**3 attempts**, exponential backoff starting at **2 s**).
* If **at least one** platform succeeds → overall status = `POSTED`.
* If **all** platforms fail → overall status = `FAILED`.
* One platform's failure never crashes the scheduler or blocks the others.
