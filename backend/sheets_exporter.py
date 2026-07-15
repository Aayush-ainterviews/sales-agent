"""
SheetsExporter: turn a run's output JSON into a Google Sheet and return its link.

Backend-only, like send_executor (Q21): the Google service-account credential lives
here and never reaches the sandbox. The agent produces outputs/*.json; this converts
that array of lead objects into a spreadsheet and shares it "anyone with the link can
view". Optional feature — if GOOGLE_SERVICE_ACCOUNT_JSON is unset, callers get a clear
error rather than a crash.
"""

import json
import logging

from backend import config

# google libs are imported lazily inside the functions below, so the backend still boots
# (and every other route works) even if they aren't installed / the feature is unused.

log = logging.getLogger("sheets_exporter")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class SheetsNotConfigured(Exception):
    """GOOGLE_SERVICE_ACCOUNT_JSON is missing -> the feature is off."""


def _creds():
    raw = config.GOOGLE_SERVICE_ACCOUNT_JSON
    if not raw:
        raise SheetsNotConfigured()
    from google.oauth2.service_account import Credentials
    return Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)


def _cell(v):
    """One JSON value -> one spreadsheet cell. Nested objects/arrays are JSON-stringified."""
    if v is None:
        return ""
    if isinstance(v, (str, int, float, bool)):
        return v
    return json.dumps(v, ensure_ascii=False)


def _rows_from_json(data) -> list[list]:
    """Array of lead objects -> header row + value rows. Accepts a bare list or a dict
    wrapping the list under a common key (leads/rows/data/results/items)."""
    if isinstance(data, dict):
        for k in ("leads", "rows", "data", "results", "items"):
            if isinstance(data.get(k), list):
                data = data[k]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        data = [data]

    cols: list[str] = []
    for obj in data:
        if isinstance(obj, dict):
            for k in obj.keys():
                if k not in cols:
                    cols.append(k)

    if not cols:  # non-dict rows -> a single "value" column
        return [["value"]] + [[_cell(x)] for x in data]

    rows: list[list] = [cols]
    for obj in data:
        if isinstance(obj, dict):
            rows.append([_cell(obj.get(c, "")) for c in cols])
        else:
            rows.append([_cell(obj)] + [""] * (len(cols) - 1))
    return rows


def export_to_sheet(data, title: str) -> str:
    """Create a spreadsheet from `data`, make it link-viewable, return its URL."""
    from googleapiclient.discovery import build

    creds = _creds()
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)

    rows = _rows_from_json(data)
    ss = sheets.spreadsheets().create(body={"properties": {"title": title}}).execute()
    sid = ss["spreadsheetId"]
    sheets.spreadsheets().values().update(
        spreadsheetId=sid,
        range="A1",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()
    # anyone with the link can view (chosen access model)
    drive.permissions().create(fileId=sid, body={"type": "anyone", "role": "reader"}).execute()

    log.info("exported sheet %s (%d rows) title=%r", sid, max(0, len(rows) - 1), title)
    return f"https://docs.google.com/spreadsheets/d/{sid}"
