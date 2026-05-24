# Wedding Bot — Setup Guide

Three things to set up: Slack App, Google Service Account, Anthropic API key.
Then push to GitHub and deploy to Render. ~45 mins total.

---

## Step 1 — Create your Slack App

1. Go to https://api.slack.com/apps and click **Create New App → From scratch**
2. Name it **Wedding Planner** and pick your workspace
3. In the left sidebar go to **OAuth & Permissions** → scroll to **Bot Token Scopes** and add:
   - `app_mentions:read`
   - `channels:history`
   - `channels:read`
   - `chat:write`
   - `groups:history`
   - `groups:read`
   - `im:history`
   - `im:read`
   - `im:write`
   - `users:read`
4. Click **Install to Workspace** → **Allow**
5. Copy the **Bot User OAuth Token** (starts with `xoxb-`) → this is your `SLACK_BOT_TOKEN`
6. In the left sidebar go to **Socket Mode** → toggle **Enable Socket Mode** → ON
7. Give the token a name (e.g. "wedding-bot-token") → click **Generate**
8. Copy the **App-Level Token** (starts with `xapp-`) → this is your `SLACK_APP_TOKEN`
9. In the left sidebar go to **Event Subscriptions** → toggle ON
10. Under **Subscribe to bot events** add:
    - `app_mention`
    - `message.channels`
    - `message.im`
    - `message.groups`
11. Save changes
12. Go to your `#wedding-planning` channel in Slack → right-click → **View channel details** → copy the **Channel ID** at the bottom → this is your `SLACK_CHANNEL_ID`
13. Invite the bot to the channel: type `/invite @Wedding Planner` in `#wedding-planning`

---

## Step 2 — Google Service Account

This gives the bot read/write access to your Google Sheet.

1. Go to https://console.cloud.google.com
2. Create a new project (e.g. "wedding-bot") or use an existing one
3. In the search bar, search **Google Sheets API** → Enable it
4. Also enable **Google Drive API**
5. Go to **IAM & Admin → Service Accounts** → **Create Service Account**
6. Name it "wedding-bot" → click through to create
7. Click the service account → go to **Keys** tab → **Add Key → Create new key → JSON**
8. A JSON file downloads — this is your `GOOGLE_CREDENTIALS_JSON`
9. Open the JSON file, copy the entire contents
10. Open your Google Sheet → click **Share** → paste the `client_email` from the JSON → give it **Editor** access

---

## Step 3 — Anthropic API Key

1. Go to https://console.anthropic.com
2. Sign in or create an account
3. Go to **API Keys** → **Create Key**
4. Copy the key (starts with `sk-ant-`) → this is your `ANTHROPIC_API_KEY`
5. Add a small amount of credit ($5 will last months for this use case)

---

## Step 4 — Push to GitHub

1. Create a new **private** repository at https://github.com/new (name it `wedding-bot`)
2. On your computer, open Terminal in the `wedding-bot` folder and run:
   ```
   git init
   git add .
   git commit -m "Initial wedding bot"
   git remote add origin https://github.com/YOUR_USERNAME/wedding-bot.git
   git push -u origin main
   ```

---

## Step 5 — Deploy to Render

1. Go to https://render.com and sign up / log in
2. Click **New → Background Worker**
3. Connect your GitHub account and select the `wedding-bot` repo
4. Render will detect `render.yaml` automatically
5. Go to **Environment** and add these variables one by one:
   - `SLACK_BOT_TOKEN` → your xoxb- token
   - `SLACK_APP_TOKEN` → your xapp- token
   - `SLACK_CHANNEL_ID` → your channel ID
   - `ANTHROPIC_API_KEY` → your Anthropic key
   - `GOOGLE_CREDENTIALS_JSON` → paste the entire JSON content
6. Click **Deploy**

---

## You're live!

Once deployed, test it by going to `#wedding-planning` and typing:
> @Wedding Planner what's on my plate today?

The bot will read your Google Sheet and reply in the channel.

Daily briefings arrive at **8:00am Malaysia time** automatically.

---

## What the bot can do

| Say this | What happens |
|----------|-------------|
| `@Wedding Planner what's due today?` | Briefing for today |
| `@Wedding Planner give me this week's plan` | Weekly overview |
| `@Wedding Planner add a task for Safa to confirm the florist by June 1` | Adds to Google Sheet |
| `@Wedding Planner mark task 3 as done` | Updates sheet |
| `@Wedding Planner any risks I should know about?` | Risk analysis |
| `@Wedding Planner what does Safa have on her plate?` | Safa's task list |
