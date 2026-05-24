import os
import certifi

# Fix macOS SSL certificate issue — tells Python to use certifi's trusted CA bundle
# instead of looking for system certificates (which Python 3.12 on Mac doesn't find)
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()

import re
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()  # Reads your .env file and loads the variables into the environment
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import anthropic
from sheets_helper import get_tasks, add_task, update_task_status

# ── INIT ──────────────────────────────────────────────────────────────────────
slack_app = App(token=os.environ["SLACK_BOT_TOKEN"])
anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SHEET_ID        = os.environ.get("GOOGLE_SHEET_ID", "130s9muvmIyyRsGP8DdewBjilvgDcBDxw2zhHqlQ7U4w")
WEDDING_CHANNEL = os.environ.get("SLACK_CHANNEL_ID", "")
WEDDING_DATE    = datetime(2026, 8, 13)

# ── SYSTEM PROMPT ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are the wedding planning assistant for Irfan and Safa, who are getting married on August 13, 2026. You live in their Slack workspace and help them stay on top of everything.

Your personality: warm, proactive, concise. You know their wedding inside out.

Key context:
- Irfan is the groom. Safa is the bride.
- Key vendors: Temple Tree (Honeymoon accommodation, Both), Chowkit Hotel (Accommodation, Irfan)
- Task categories: Venue, Accommodation, Catering, Flowers, Attire, Photography, Music, Invitations, Honeymoon, Legal, Budget, Guests, Other

Slack formatting rules:
- Use *bold* for section headers and task titles
- Use • for bullet points
- Keep responses concise — this is a chat, not a document
- Never use markdown headers (##) — use *bold* instead

When the user asks you to ADD a task, append this exact line at the very end of your response (after your message):
ACTION:ADD_TASK|<title>|<Irfan or Safa or Both>|<category>|<High or Medium or Low>|<YYYY-MM-DD or blank>|<notes or blank>

When the user asks you to MARK a task as done, append:
ACTION:COMPLETE_TASK|<task_id>

Only include one ACTION line per response. Do not explain or mention the ACTION line in your text."""

# ── HELPERS ───────────────────────────────────────────────────────────────────
def days_to_wedding() -> int:
    return (WEDDING_DATE - datetime.now()).days

def format_tasks(tasks: list) -> str:
    if not tasks:
        return "No tasks found."
    lines = []
    for t in tasks:
        due = f" · due {t.get('Due Date')}" if t.get("Due Date") else ""
        status = "✅" if str(t.get("Status", "")).lower() == "done" else "⬜"
        lines.append(
            f"{status} #{t.get('ID','')} *{t.get('Title','')}* "
            f"[{t.get('Assigned To','')} · {t.get('Category','')} · {t.get('Priority','')}]{due}"
        )
        if t.get("Notes"):
            lines.append(f"   _{t.get('Notes')}_")
    return "\n".join(lines)

def get_user_name(user_id: str) -> str:
    try:
        info = slack_app.client.users_info(user=user_id)
        profile = info["user"]["profile"]
        return profile.get("display_name") or profile.get("real_name") or "Someone"
    except Exception:
        return "Someone"

# ── CLAUDE RESPONSE ───────────────────────────────────────────────────────────
def ask_claude(user_message: str, tasks: list, user_name: str = "") -> str:
    today = datetime.now().strftime("%A, %B %d, %Y")

    context = f"""Today is {today}. {days_to_wedding()} days until the wedding.

Sent by: {user_name or "unknown"}

Current tasks:
{format_tasks(tasks)}

User message: {user_message}"""

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": context}]
    )
    return response.content[0].text

# ── BRIEFING ──────────────────────────────────────────────────────────────────
def generate_briefing() -> str:
    tasks = get_tasks(SHEET_ID)
    today = datetime.now().strftime("%A, %B %d, %Y")

    prompt = f"""Today is {today}. {days_to_wedding()} days until the wedding.

Tasks:
{format_tasks(tasks)}

Generate a morning briefing with these sections:
1. *Today's Priorities* — tasks due today
2. *This Week* — tasks due in the next 7 days (not today)
3. *Blockers* — tasks that can't proceed because a dependency isn't done yet
4. *Risks* — tight timelines, unconfirmed vendors, tasks with no due date that are time-sensitive, tasks with no owner

If a section has nothing to report, say "Nothing for today" — don't skip it.
Start with a one-line summary. Keep it tight and actionable."""

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

# ── ACTION PARSER ─────────────────────────────────────────────────────────────
def parse_and_execute(response_text: str) -> str:
    match = re.search(r'\nACTION:(\w+)\|(.+)$', response_text, re.MULTILINE)
    if not match:
        return response_text

    action = match.group(1)
    params = match.group(2).split("|")
    clean = response_text[:match.start()].strip()

    if action == "ADD_TASK" and len(params) >= 5:
        title       = params[0].strip()
        assigned_to = params[1].strip()
        category    = params[2].strip()
        priority    = params[3].strip()
        due_date    = params[4].strip() if len(params) > 4 else ""
        notes       = params[5].strip() if len(params) > 5 else ""
        ok = add_task(SHEET_ID, title, assigned_to, category, priority, due_date, notes)
        clean += "\n\n✅ *Task added to your Google Sheet!*" if ok else "\n\n⚠️ Couldn't write to sheet — please add manually."

    elif action == "COMPLETE_TASK" and params:
        task_id = params[0].strip()
        ok = update_task_status(SHEET_ID, task_id, "Done")
        clean += f"\n\n✅ *Task #{task_id} marked as done!*" if ok else "\n\n⚠️ Couldn't update sheet — please update manually."

    return clean

# ── SLACK EVENTS ──────────────────────────────────────────────────────────────
@slack_app.event("app_mention")
def handle_mention(event, client):
    """Respond when @mentioned — replies in a thread and shows a thinking indicator."""
    user_id  = event.get("user", "")
    raw_text = event.get("text", "")
    channel  = event.get("channel")
    # If already in a thread, keep that thread. Otherwise start one from this message.
    thread_ts = event.get("thread_ts") or event.get("ts")

    # Strip the @mention tag from the message text
    text = re.sub(r"<@[A-Z0-9]+>", "", raw_text).strip()

    # Post a thinking indicator immediately so the user knows the bot received it
    thinking = client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text="⏳ On it, give me a sec..."
    )

    # Now do the work (this takes a few seconds)
    user_name = get_user_name(user_id)
    tasks     = get_tasks(SHEET_ID)
    response  = ask_claude(text, tasks, user_name)
    response  = parse_and_execute(response)

    # Update the thinking message with the real response
    client.chat_update(
        channel=channel,
        ts=thinking["ts"],
        text=response
    )

@slack_app.event("message")
def handle_dm(event, client):
    """Respond to direct messages with a thinking indicator."""
    # Only handle DMs (channel_type == "im"), not channel messages
    if event.get("channel_type") != "im":
        return
    if event.get("bot_id") or event.get("subtype"):
        return

    user_id  = event.get("user", "")
    channel  = event.get("channel")
    text     = event.get("text", "").strip()

    # Thinking indicator
    thinking = client.chat_postMessage(
        channel=channel,
        text="⏳ On it, give me a sec..."
    )

    user_name = get_user_name(user_id)
    tasks     = get_tasks(SHEET_ID)
    response  = ask_claude(text, tasks, user_name)
    response  = parse_and_execute(response)

    # Replace thinking message with actual response
    client.chat_update(
        channel=channel,
        ts=thinking["ts"],
        text=response
    )

# ── SCHEDULED BRIEFING ────────────────────────────────────────────────────────
def send_daily_briefing():
    if not WEDDING_CHANNEL:
        print("SLACK_CHANNEL_ID not set — skipping briefing.")
        return
    try:
        briefing = generate_briefing()
        slack_app.client.chat_postMessage(
            channel=WEDDING_CHANNEL,
            text=f"*🌸 Good morning, Irfan & Safa! Wedding briefing — {datetime.now().strftime('%A %B %d')}:*\n\n{briefing}"
        )
        print(f"Briefing sent at {datetime.now()}")
    except Exception as e:
        print(f"Error sending briefing: {e}")

# ── START ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Daily briefing at 8:00am (server timezone — set TZ=Asia/Kuala_Lumpur on Render)
    scheduler = BackgroundScheduler()
    scheduler.add_job(send_daily_briefing, CronTrigger(hour=8, minute=0))
    scheduler.start()
    print(f"Bot started. {days_to_wedding()} days to the wedding!")

    handler = SocketModeHandler(slack_app, os.environ["SLACK_APP_TOKEN"])
    handler.start()
