"""Turn a screenshot, typed line or dictation into a draft entry using Claude.
# Errors from Claude use 424, not 502/504: DigitalOcean's edge replaces 502/504 bodies with its own page, hiding the cause.

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


RTO_TOOL = {
    "name": "read_ready_to_order",
    "description": "Pull job facts out of a Mill City READY TO ORDER email, its attached estimate PDF, or a vendor's reply.",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_vendor_reply": {"type": "boolean",
                                "description": "True if a cabinet vendor (Highland/HCI, Oasis, Procraft...) wrote this, e.g. a sales order."},
            "job_name": {"type": "string",
                         "description": "Job name as written after 'READY TO ORDER:', WITHOUT any 'change order' / 'add on' "
                                        "suffix, e.g. 'Keith Olson - Centennial Vanity'."},
            "is_change_order": {"type": "boolean", "description": "Subject or body says change order or add-on/add on."},
            "customer": {"type": "string"},
            "rep_name": {"type": "string", "description": "Sales rep / job owner if shown anywhere (email or PDF)."},
            "manufacturer": {"type": "string", "description": "Cabinet supplier, e.g. Highland, Oasis, Procraft."},
            "invoice_total": {"type": "number", "description": "Total the CUSTOMER pays, from the Mill City estimate/invoice PDF."},
            "material_total": {"type": "number", "description": "What Mill City pays the VENDOR (sales order total), if shown."},
            "boxes": {"type": "integer"},
            "modifications": {"type": "integer", "description": "Number of modifications; 0 if NA or NONE."},
        },
        "required": ["is_vendor_reply", "is_change_order"],
    },
}


def read_rto(subject: str, body: str, pdf: bytes | None) -> dict:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(503, "ANTHROPIC_API_KEY is not set on the app")
    import anthropic

    content: list[dict] = []
    if pdf:
        content.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                       "data": base64.standard_b64encode(pdf).decode()}})
    content.append({"type": "text", "text": f"Subject: {subject}\n\n{body[:20000]}\n\n"
                                            "Leave out any field you can't find. Never guess a dollar amount. Answer only by calling the read_ready_to_order tool."})
    try:
        msg = anthropic.Anthropic().messages.create(
            model=MODEL, max_tokens=1024, tools=[RTO_TOOL], tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.APIError as e:
        raise HTTPException(424, f"Claude unavailable ({e.__class__.__name__})")
    return next((b.input for b in msg.content if b.type == "tool_use"), {})


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
        f"Owner's note: {text.strip() or '(no text, see attachment)'}\n\nAnswer only by calling the file_entry tool."
    )
    content: list[dict] = []
    if image:
        data = base64.standard_b64encode(image).decode()
        block = "document" if image_type == "application/pdf" else "image"
        content.append({"type": block, "source": {"type": "base64", "media_type": image_type, "data": data}})
    content.append({"type": "text", "text": context})

    try:
        msg = anthropic.Anthropic().messages.create(
            model=MODEL, max_tokens=1024, tools=[TOOL], tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.APIError as e:
        raise HTTPException(424, f"Couldn't read that right now ({e.__class__.__name__}: {str(e)[:200]}). Try again.")
    except Exception as e:  # surface anything else instead of a blank 500
        raise HTTPException(424, f"Quick log failed ({e.__class__.__name__}: {str(e)[:200]})")
    entry = next((b.input for b in msg.content if b.type == "tool_use"), None)
    if not entry:
        raise HTTPException(424, "Couldn't read that. Try rewording it.")

    # Never trust a name the model made up: keep only exact matches from our lists.
    if entry.get("job") not in job_names:
        entry["job"] = ""
    if entry.get("worker") not in crew_names:
        entry["worker"] = ""
    return entry
