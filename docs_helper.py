import os
import json
from datetime import datetime
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
]

def _creds():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        info = json.loads(creds_json)
        return Credentials.from_service_account_info(info, scopes=SCOPES)
    return Credentials.from_service_account_file("credentials.json", scopes=SCOPES)

def _docs_service():
    return build("docs", "v1", credentials=_creds())

def _drive_service():
    return build("drive", "v3", credentials=_creds())

# ── READ ──────────────────────────────────────────────────────────────────────
def get_doc_content(doc_id: str) -> str:
    """Read the full plain-text content of the wedding context doc."""
    try:
        # Use Drive export — simpler than parsing the Docs JSON structure
        content = _drive_service().files().export(
            fileId=doc_id,
            mimeType="text/plain"
        ).execute()
        return content.decode("utf-8").strip()
    except Exception as e:
        print(f"[docs] Error reading doc: {e}")
        return ""

# ── WRITE ─────────────────────────────────────────────────────────────────────
def append_to_doc(doc_id: str, category: str, note: str) -> bool:
    """Append a dated context note to the wedding doc under the right section."""
    try:
        service = _docs_service()
        doc     = service.documents().get(documentId=doc_id).execute()

        # Find the end of the document body to insert before the final newline
        body_content = doc.get("body", {}).get("content", [])
        end_index    = body_content[-1].get("endIndex", 2) - 1

        date_str = datetime.now().strftime("%Y-%m-%d")
        entry    = f"\n[{date_str}] {category}: {note}"

        service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [{"insertText": {"location": {"index": end_index}, "text": entry}}]}
        ).execute()
        return True
    except Exception as e:
        print(f"[docs] Error appending to doc: {e}")
        return False
