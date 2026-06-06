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
from sheets_helper import get_tasks, add_task, update_task_status, batch_update_task_statuses
from docs_helper import get_doc_content, append_to_doc

# ── INIT ──────────────────────────────────────────────────────────────────────
slack_app = App(token=os.environ["SLACK_BOT_TOKEN"])
anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SHEET_ID        = os.environ.get("GOOGLE_SHEET_ID", "130s9muvmIyyRsGP8DdewBjilvgDcBDxw2zhHqlQ7U4w")
DOC_ID          = os.environ.get("WEDDING_DOC_ID", "1XqVdc0BWyqZby2cTMXLq9A-qqjoQt6Nb8QnVL3lgCL0")
WEDDING_CHANNEL = os.environ.get("SLACK_CHANNEL_ID", "")
WEDDING_DATE    = datetime(2026, 8, 13)

# Threads where the bot is actively waiting for follow-up info
# Maps thread_ts -> channel_id
pending_threads: dict = {}

# ── SYSTEM PROMPT ─────────────────────────────────────────────────────────────
# Loaded from system_prompt.txt — edit that file to change bot behaviour, then redeploy.
_prompt_path = os.path.join(os.path.dirname(__file__), "system_prompt.txt")
with open(_prompt_path, "r") as _f:
    SYSTEM_PROMPT = _f.read().strip()

# ── HELPERS ───────────────────────────────────────────────────────────────────
def days_to_wedding() -> int:
    return (WEDDING_DATE - datetime.now()).days

STATUS_EMOJI = {
    "done":        "✅",
    "in progress": "🔄",
    "cancelled":   "❌",
    "not started": "⬜",
    "todo":        "⬜",  # legacy fallback
}

def format_tasks(tasks: list) -> str:
    if not tasks:
        return "No tasks found."
    lines = []
    for t in tasks:
        due    = f" · due {t.get('Due Date')}" if t.get("Due Date") else ""
        status = str(t.get("Status", "")).lower()
        emoji  = STATUS_EMOJI.get(status, "⬜")
        lines.append(
            f"{emoji} #{t.get('ID','')} *{t.get('Title','')}* "
            f"[{t.get('Assigned To','')} · {t.get('Category','')} · {t.get('Priority','')} · {t.get('Status','')}]{due}"
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

# ── THREAD HISTORY ────────────────────────────────────────────────────────────
def get_thread_history(client, channel: str, thread_ts: str) -> list:
    """Fetch and format all previous messages in a thread for Claude's context."""
    try:
        result = client.conversations_replies(channel=channel, ts=thread_ts)
        messages = result.get("messages", [])[:-1]  # Exclude the current message
        history = []
        for msg in messages:
            text = re.sub(r"<@[A-Z0-9]+>", "", msg.get("text", "")).strip()
            if not text or "⏳" in text:  # Skip empty or thinking messages
                continue
            speaker = "Assistant" if msg.get("bot_id") else f"{msg.get('user', 'User')}"
            history.append(f"{speaker}: {text}")
        return history
    except Exception as e:
        print(f"[slack] Error fetching thread history: {e}")
        return []

# ── CONTEXT EXTRACTION ────────────────────────────────────────────────────────
def extract_and_store_context(conversation: str):
    """After each conversation, check if anything important should be remembered in the wedding doc."""
    prompt = f"""Read this wedding planning conversation and extract any important information worth remembering long-term — vendor decisions, guest details, budget constraints, preferences, confirmed bookings, or key decisions made.

Conversation:
{conversation}

If nothing worth storing, reply only: NONE
Otherwise reply with one line per item in this exact format:
CONTEXT:<category>|<concise note>

Categories: Vendor, Guest, Budget, Decision, Venue, Accommodation, Honeymoon, Timeline, General"""

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )
    text = response.content[0].text.strip()
    if text.upper() == "NONE":
        return
    for line in text.split("\n"):
        if line.startswith("CONTEXT:"):
            parts = line[8:].split("|", 1)
            if len(parts) == 2:
                append_to_doc(DOC_ID, parts[0].strip(), parts[1].strip())

# ── CLAUDE RESPONSE ───────────────────────────────────────────────────────────
def ask_claude(user_message: str, tasks: list, user_name: str = "", thread_history: list = None) -> str:
    today = datetime.now().strftime("%A, %B %d, %Y")
    wedding_context = get_doc_content(DOC_ID)

    thread_context = ""
    if thread_history:
        thread_context = "\nThread conversation so far:\n" + "\n".join(thread_history) + "\n"

    context = f"""Today is {today}. {days_to_wedding()} days until the wedding.

Sent by: {user_name or "unknown"}
{thread_context}
Wedding context (decisions & info built up over time):
{wedding_context or "No context stored yet."}

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
    tasks           = get_tasks(SHEET_ID)
    wedding_context = get_doc_content(DOC_ID)
    today           = datetime.now().strftime("%A, %B %d, %Y")

    prompt = f"""Today is {today}. {days_to_wedding()} days until the wedding on August 13, 2026.

Task statuses in use: Not Started ⬜, In Progress 🔄, Done ✅, Cancelled ❌

Wedding context (decisions & info built up over time):
{wedding_context or "No context stored yet."}

All tasks:
{format_tasks(tasks)}

Generate a morning briefing in this exact structure:

*🎯 Focus Today* — Pick the 1-3 most important things to do today and explain briefly why each one matters or is time-sensitive. Be direct and opinionated. Prefer tasks that are Not Started or In Progress.

*📋 Due Today* — Tasks with today's due date (exclude Cancelled and Done).

*📅 This Week* — Tasks due in the next 7 days (not today). Exclude Cancelled and Done.

*🔄 In Progress* — Tasks currently marked In Progress. Flag any that seem stalled (no recent activity or past due date).

*🚧 Blockers* — Tasks that can't proceed because something else isn't done yet. Name both the blocked task and what's blocking it.

*⚠️ Risks* — Tight timelines, unconfirmed vendors, tasks with no due date that are time-sensitive, anything that could cause problems if not addressed soon.

If a section has nothing to report, say "Nothing for now" — don't skip it.
Keep it tight, specific, and actionable. Use Slack formatting (*bold*, • bullets)."""

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

# ── ACTION PARSER ─────────────────────────────────────────────────────────────
def parse_and_execute(response_text: str, tasks: list = None, thread_ts: str = None, channel: str = None) -> str:
    lines = response_text.split("\n")
    action_lines = [l for l in lines if l.startswith("ACTION:")]
    if not action_lines:
        return response_text

    # Build a quick id→title lookup from the current task list
    task_titles = {str(t.get("ID")): t.get("Title", "") for t in (tasks or [])}

    def task_label(task_id: str) -> str:
        title = task_titles.get(str(task_id))
        return f"#{task_id} — {title}" if title else f"#{task_id}"

    clean = "\n".join(l for l in lines if not l.startswith("ACTION:")).strip()
    confirmations = []
    status_updates = []   # list of (task_id, status, label) — batched into one sheets call

    for line in action_lines:
        parts  = line.split("|")
        action = parts[0][len("ACTION:"):]
        params = [p.strip() for p in parts[1:]]

        if action == "AWAITING_INFO":
            if thread_ts and channel:
                pending_threads[thread_ts] = channel

        elif action == "ADD_TASK" and len(params) >= 5:
            title, assigned_to, category, priority = params[0], params[1], params[2], params[3]
            due_date = params[4] if len(params) > 4 else ""
            notes    = params[5] if len(params) > 5 else ""
            ok = add_task(SHEET_ID, title, assigned_to, category, priority, due_date, notes)
            if ok:
                due_str = f" · due {due_date}" if due_date else ""
                confirmations.append(f"✅ *Added to Google Sheet:* {title} [{assigned_to} · {category} · {priority}{due_str}]")
            else:
                confirmations.append(f"⚠️ Couldn't write \"{title}\" to sheet — please add manually.")
            if thread_ts and thread_ts in pending_threads:
                del pending_threads[thread_ts]

        elif action == "COMPLETE_TASK" and params:
            task_id = params[0]
            label   = task_label(task_id)
            status_updates.append((task_id, "Done", f"✅ *Marked Done in Google Sheet:* {label}", f"⚠️ Couldn't update {label} — please update manually."))

        elif action == "UPDATE_STATUS" and len(params) >= 2:
            task_id, new_status = params[0], params[1]
            emoji = STATUS_EMOJI.get(new_status.lower(), "🔄")
            label = task_label(task_id)
            status_updates.append((task_id, new_status, f"{emoji} *Updated in Google Sheet:* {label} → {new_status}", f"⚠️ Couldn't update {label} — please update manually."))

    # Batch all status updates into a single sheets round-trip
    if status_updates:
        results = batch_update_task_statuses(SHEET_ID, [(t[0], t[1]) for t in status_updates])
        for (_, _, ok_msg, fail_msg), ok in zip(status_updates, results):
            confirmations.append(ok_msg if ok else fail_msg)

    if confirmations:
        clean += "\n\n" + "\n".join(confirmations)

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

    try:
        # Now do the work (this takes a few seconds)
        user_name      = get_user_name(user_id)
        tasks          = get_tasks(SHEET_ID)
        thread_history = get_thread_history(client, channel, thread_ts)
        response       = ask_claude(text, tasks, user_name, thread_history)
        response       = parse_and_execute(response, tasks=tasks, thread_ts=thread_ts, channel=channel)

        # Silently extract and store any important wedding context from this exchange
        full_exchange = "\n".join(thread_history or []) + f"\n{user_name}: {text}\nAssistant: {response}"
        extract_and_store_context(full_exchange)
    except Exception as e:
        print(f"[bot] Error handling mention from {user_id}: {e}")
        response = "⚠️ Sorry, something went wrong on my end. Please try again in a moment!"

    # Update the thinking message with the real response
    client.chat_update(
        channel=channel,
        ts=thinking["ts"],
        text=response
    )

def handle_pending_thread_reply(event, client):
    """Respond to a message in a thread the bot is actively following for task info."""
    thread_ts = event.get("thread_ts")
    channel   = pending_threads.get(thread_ts)
    if not channel:
        return
    if event.get("bot_id") or event.get("subtype"):
        return

    user_id = event.get("user", "")
    text    = event.get("text", "").strip()
    if not text:
        return

    thinking = client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text="⏳ On it, give me a sec..."
    )

    user_name      = get_user_name(user_id)
    tasks          = get_tasks(SHEET_ID)
    thread_history = get_thread_history(client, channel, thread_ts)
    response       = ask_claude(text, tasks, user_name, thread_history)
    response       = parse_and_execute(response, tasks=tasks, thread_ts=thread_ts, channel=channel)

    client.chat_update(channel=channel, ts=thinking["ts"], text=response)

    full_exchange = "\n".join(thread_history or []) + f"\n{user_name}: {text}\nAssistant: {response}"
    extract_and_store_context(full_exchange)

@slack_app.event("message")
def handle_message(event, client):
    """Handle DMs (reply directly) and channel thread replies when bot is awaiting task info."""
    if event.get("bot_id") or event.get("subtype"):
        return

    channel_type = event.get("channel_type")
    thread_ts    = event.get("thread_ts")
    text         = event.get("text", "")

    # If the message contains a @mention anywhere, handle_mention will pick it up.
    # Return here to avoid double-processing (which causes duplicate task creation).
    if re.search(r"<@[A-Z0-9]+>", text):
        return

    # Route channel thread replies to the pending-thread handler.
    if channel_type != "im" and thread_ts:
        handle_pending_thread_reply(event, client)
        return

    # Only continue for DMs
    if channel_type != "im":
        return

    user_id = event.get("user", "")
    channel = event.get("channel")
    text    = event.get("text", "").strip()
    if not text:
        return

    # Thinking indicator
    thinking = client.chat_postMessage(
        channel=channel,
        text="⏳ On it, give me a sec..."
    )

    user_name = get_user_name(user_id)
    tasks     = get_tasks(SHEET_ID)
    response  = ask_claude(text, tasks, user_name)
    response  = parse_and_execute(response, tasks=tasks)

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
