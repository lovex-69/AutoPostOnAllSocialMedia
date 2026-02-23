# ExecutionX — GitHub Copilot Prompt

> Copy everything below this line and paste it into GitHub Copilot in a new workspace/folder (`E:\ExecutionX\`).

---

Build a standalone Node.js script called "ExecutionX" that schedules video posts on X (Twitter) via Playwright browser automation. This script reads from my EXISTING Supabase database (same one used by my Python backend) and only touches X-related status columns — it must NOT interfere with my working YouTube + LinkedIn posting pipeline.

## Context

I have an existing multi-platform auto-posting backend (Python/FastAPI) deployed on Render that handles YouTube and LinkedIn. X/Twitter requires a paid API ($100/mo) which I refuse to pay. This script is the workaround — it uses browser automation to post/schedule on X's web UI instead.

## Supabase Connection

- Supabase URL: `https://hjpawupnqyugbmsdmcmh.supabase.co`
- Table name: `ai_tools` (this ALREADY EXISTS — do NOT create or modify the table)
- Existing columns used:
  - `id` (integer, primary key)
  - `name` (text) — the AI tool name
  - `caption` (text) — full post text (includes title + description + hashtags)
  - `video_url` (text) — originally a URL, but for local use this will be a local file path to the MP4
  - `status` (text) — overall status (READY, POSTING, POSTED, FAILED)
  - `x_status` (text) — X-specific status: PENDING, POSTING, POSTED, FAILED, SKIPPED
  - `scheduled_at` (timestamp, nullable) — when the post should go live
  - `created_at` (timestamp)

## Tech Stack

- Node.js (ES modules or CommonJS, your choice)
- Playwright (chromium)
- @supabase/supabase-js
- dotenv

## Environment Variables (.env file)

```
SUPABASE_URL=https://hjpawupnqyugbmsdmcmh.supabase.co
SUPABASE_ANON_KEY=<I'll fill this in>
X_EMAIL=<my X login email>
X_USERNAME=<my X username, needed if X asks "verify your identity">
X_PASSWORD=<my X password>
```

## Script Behavior (schedule-x.js)

### 1. Fetch posts from Supabase

- Query `ai_tools` where `x_status` IN ('PENDING', 'READY', 'FAILED')
- AND `video_url` IS NOT NULL
- Order by `scheduled_at` ASC NULLS LAST, then `created_at` ASC
- If no posts found, log "No pending X posts" and exit cleanly

### 2. Launch Browser

- Playwright Chromium, headless: false (must be visible)
- slowMo: 150 (human-like speed)
- Use a persistent browser context stored in `./x-browser-data/` so login session persists between runs (don't need to log in every time)
- Default viewport: 1280x800

### 3. Login Flow (only if not already logged in)

- Navigate to `https://x.com/home`
- Check if already logged in (look for compose button or profile avatar)
- If NOT logged in:
  a. Go to `https://x.com/i/flow/login`
  b. Wait for email input → type X_EMAIL → click Next
  c. If X shows "Enter your phone number or username" verification step → type X_USERNAME → click Next
  d. Wait for password input → type X_PASSWORD → click "Log in"
  e. Wait for home timeline to load (wait for compose button)
  f. If 2FA prompt appears → pause and show console message "Please complete 2FA manually, then press Enter..." → wait for user input
- Add 2-second delay after login

### 4. For Each Post

a. Navigate to `https://x.com/compose/post` OR click the main compose/post button
b. Wait for the compose dialog/editor to appear
c. Type the `caption` text into the tweet composer
   - Use `keyboard.type()` with a small delay (30ms per char) for human-like typing
   - Or use `page.evaluate` to set text via `execCommand('insertText')` if typing is too slow for long captions
d. Upload the video:
   - Find the media upload button / file input
   - Use `setInputFiles()` with the `video_url` path (local MP4 file path)
   - Wait for video upload to complete (progress bar disappears, video thumbnail appears)
   - Timeout: 120 seconds for video upload (videos can be large)
e. If `scheduled_at` is set and is in the future:
   - Click the schedule button (calendar icon near the post button)
   - Set the date and time from `scheduled_at`
   - Click "Confirm" / "Schedule"
   - Then click "Schedule post" button
f. If `scheduled_at` is null or in the past:
   - Just click the "Post" button to publish immediately
g. Wait for success confirmation (dialog closes, or success toast appears)
h. Update Supabase: SET `x_status = 'POSTED'` WHERE `id = <current post id>`
i. Log: `✓ Posted: <name> (id: <id>)`
j. Random delay between 3-8 seconds before next post

### 5. Error Handling Per Post

- Wrap each post in try/catch
- If a post fails:
  - Screenshot the page → save to `./errors/error-<id>-<timestamp>.png`
  - Update Supabase: SET `x_status = 'FAILED'` WHERE `id = <current post id>`
  - Log: `✗ Failed: <name> (id: <id>) — <error message>`
  - Continue to next post (do NOT stop)

### 6. Cleanup

- After all posts processed, log summary: `Done: X posted, Y failed, Z total`
- Close the browser page but keep the persistent context (so session persists)
- Exit process

## DOM Selectors

X changes their UI frequently. Put ALL selectors in a separate config object at the top of the file:

```js
const SELECTORS = {
  // Login
  emailInput: 'input[autocomplete="username"]',
  nextButton: '[role="button"]:has-text("Next")',
  usernameInput: 'input[data-testid="ocfEnterTextTextInput"]',
  passwordInput: 'input[name="password"]',
  loginButton: '[data-testid="LoginForm_Login_Button"]',

  // Home / compose detection
  composeButton: '[data-testid="SideNav_NewTweet_Button"]',
  tweetTextarea: '[data-testid="tweetTextArea_0"]',

  // Media upload
  fileInput: 'input[data-testid="fileInput"]',
  mediaUploadProgress: '[data-testid="progressBar"]',

  // Post / Schedule
  postButton: '[data-testid="tweetButton"]',
  scheduleButton: '[data-testid="scheduledButton"]',
  scheduleConfirm: '[data-testid="scheduledConfirmationPrimaryAction"]',

  // Fallback selectors (in case testid changes)
  composeArea: '[role="textbox"][data-offset-key]',
  postButtonAlt: 'div[data-testid="tweetButtonInline"]',
};
```

Include comments noting these may need updating. The script should try primary selectors first, fall back to alternates.

## File Structure

```
executionx/
  schedule-x.js          ← main script
  .env                   ← credentials (gitignored)
  .env.example           ← template without real values
  package.json
  .gitignore             ← node_modules, .env, x-browser-data/, errors/
  README.md              ← setup + usage instructions
  errors/                ← auto-created, screenshots of failures
  x-browser-data/        ← persistent browser session (gitignored)
```

## package.json scripts

```json
{
  "scripts": {
    "post": "node schedule-x.js",
    "post:dry": "DRY_RUN=true node schedule-x.js"
  }
}
```

## Dry Run Mode

If env var `DRY_RUN=true`:
- Fetch posts from Supabase normally
- Open browser, log in normally
- Navigate to compose, type text, attach video
- But do NOT click Post/Schedule — stop before the final click
- Log what WOULD have been posted
- Do NOT update Supabase status

## Critical Constraints

- ONLY update `x_status` column — never touch `status`, `youtube_status`, `linkedin_status`, `ig_status` or any other column
- `video_url` contains LOCAL file paths like `E:\ExecutionPosting\uploads\video.mp4` or relative paths — handle both
- The `caption` field already contains the full formatted text (title + description + hashtags) — post it as-is, do not modify it
- Must work on Windows (my OS)
- Use persistent browser context so I only log in once
- Include `console.log` with timestamps for all actions
- The script runs manually (I type `npm run post`) — it is NOT a daemon or cron job

## README.md should include

1. Prerequisites (Node.js, npm)
2. Setup steps (`npm install`, copy `.env.example`, fill in values, `npx playwright install chromium`)
3. How to run: `npm run post`
4. How to do a dry run: `npm run post:dry`
5. Troubleshooting: what to do if selectors break, how to update them
6. Note that X may change their UI — selectors in SELECTORS object need periodic updates
