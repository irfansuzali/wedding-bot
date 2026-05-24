import os
import json
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def _client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
    return gspread.authorize(creds)

def _tasks_sheet(sheet_id: str):
    client = _client()
    wb = client.open_by_key(sheet_id)
    # Try "Tasks" tab first, fall back to first sheet
    try:
        return wb.worksheet("Tasks")
    except gspread.WorksheetNotFound:
        return wb.get_worksheet(0)

def get_tasks(sheet_id: str) -> list:
    try:
        ws = _tasks_sheet(sheet_id)
        return ws.get_all_records()
    except Exception as e:
        print(f"[sheets] Error reading tasks: {e}")
        return []

def add_task(
    sheet_id: str,
    title: str,
    assigned_to: str,
    category: str,
    priority: str,
    due_date: str = "",
    notes: str = "",
) -> bool:
    try:
        ws = _tasks_sheet(sheet_id)
        records = ws.get_all_records()
        next_id = max((r.get("ID", 0) for r in records), default=0) + 1
        ws.append_row([
            next_id,
            title,
            assigned_to,
            category,
            priority,
            due_date,
            "Not Started",
            notes,
            datetime.now().strftime("%Y-%m-%d"),
        ])
        return True
    except Exception as e:
        print(f"[sheets] Error adding task: {e}")
        return False

def get_wedding_context(sheet_id: str) -> list:
    """Read stored wedding context notes."""
    try:
        client = _client()
        wb = client.open_by_key(sheet_id)
        ws = wb.worksheet("Wedding Context")
        return ws.get_all_records()
    except Exception as e:
        print(f"[sheets] Wedding Context tab not found or error: {e}")
        return []

def add_wedding_context(sheet_id: str, category: str, note: str) -> bool:
    """Store an important piece of wedding context."""
    try:
        client = _client()
        wb = client.open_by_key(sheet_id)
        try:
            ws = wb.worksheet("Wedding Context")
        except gspread.WorksheetNotFound:
            print("[sheets] Wedding Context tab doesn't exist yet.")
            return False
        ws.append_row([
            datetime.now().strftime("%Y-%m-%d"),
            category,
            note,
            "Slack"
        ])
        return True
    except Exception as e:
        print(f"[sheets] Error storing context: {e}")
        return False

def update_task_status(sheet_id: str, task_id: str, status: str) -> bool:
    try:
        ws = _tasks_sheet(sheet_id)
        records = ws.get_all_records()
        for i, record in enumerate(records, start=2):  # row 1 = header
            if str(record.get("ID")) == str(task_id):
                ws.update_cell(i, 7, status)  # column 7 = Status
                return True
        return False
    except Exception as e:
        print(f"[sheets] Error updating task: {e}")
        return False
