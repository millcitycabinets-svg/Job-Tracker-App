"""Turn a screenshot, typed line or dictation into a draft entry using Claude.

The draft is only a suggestion: the owner confirms or edits it on screen, then it is saved through the
normal endpoints (so every validation and the audit log still apply).
"""

import base64
import os
from datetime import date

from fastapi import HTTPException

MODEL = os.environ.get("INBOX_MODEL", "claude-sonnet-5-5")

TOOL = {
    "name": "file_entry",
    "description": "Record what the owner's note or screenshot describes as one entry for the job tracker.",
    "input_schema": {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["payment", "hours", "cost", "unknown"],
                "description": "payment = money IN from a customer. hours = a crew member's hours on a job. "
                               "cost = money OUT on a job (materials, countertops, subcontractor, design, "
                               "delivery, referral, unexpected, change order). unknown = can't tell.",
            },
            "job": {"type": "string", "description": "Exact job name from the provided list, or empty if none fits."},
            "amount": {"type": "number", "description": "Dollar amount (payment or cost)."},
            "date": {"type": "string", "description": "YYYY-MM-DD. Use today's date if none is stated."},
            "method": {"type": "string", "enum": ["card", "check", "ach", "cash", "other"]},
            "payment_type": {"type": "string", "enum": ["deposit", "progress", "final", "change_order"]},
            "status": {"type": "string", "enum": ["received", "pending"],
                       "description": "pending if the payment is promised or on its way, not landed yet."},
            "processing_fee": {"type": "number", "description": "Card fee if shown."},
            "worker": {"type": "string", "description": "Exact crew name from the provided list (hours only)."},
            "hours": {"type": "number"},
            "line": {"type": "integer", "enum": [2, 4, 5, 7, 8, 9, 11],
                     "description": "Cost line: 2 change order (money in, positive), 4 materials, 5 assembly, "
                                    "7 countertops, 8 design, 9 subcontractor or delivery, 11 unexpected."},
            "payee": {"type": "string"},
            "note": {"type": "string", "description": "Short note; required reason for unexpected costs."},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "question": {"type": "string", "description": "If something is ambiguous, one short question for the owner."},
        },
        "required": ["kind", "confidence"],
    },
}


def parse(text: str, image: bytes | None, image_type: str | None, job_names: list[str], crew_names: list[str]) -> dict:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(503, "Smart input isn't set up yet: add ANTHROPIC_API_KEY to the app's settings.")
    import anthropic  # imported here so the app runs without the key or package configured

    context = (
        f"Today is {date.today().isoformat()}. Mill City Cabinetry job tracker.\n"
        f"Open jobs: {', '.join(job_names) or '(none)'}\n"
        f"Crew: {', '.join(crew_names) or '(none)'}\n"
        "Match job and crew names to these lists, allowing for nicknames, first or last names only, and typos. "
        "Leave job empty rather than guessing between two jobs.\n\n"
        f"Owner's note: {text.strip() or '(no text, see attachment)'}"
    )
    content: list[dict] = []
    if image:
        data = base64.standard_b64encode(image).decode()
        block = "document" if image_type == "application/pdf" else "image"
        content.append({"type": block, "source": {"type": "base64", "media_type": image_type, "data": data}})
    content.append({"type": "text", "text": context})

    try:
        msg = anthropic.Anthropic().messages.create(
            model=MODEL, max_tokens=1024, tools=[TOOL], tool_choice={"type": "tool", "name": "file_entry"},
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.APIError as e:
        raise HTTPException(502, f"Couldn't read that right now ({e.__class__.__name__}). Try again.")
    entry = next((b.input for b in msg.content if b.type == "tool_use"), None)
    if not entry:
        raise HTTPException(502, "Couldn't read that. Try rewording it.")

    # Never trust a name the model made up: keep only exact matches from our lists.
    if entry.get("job") not in job_names:
        entry["job"] = ""
    if entry.get("worker") not in crew_names:
        entry["worker"] = ""
    return entry
