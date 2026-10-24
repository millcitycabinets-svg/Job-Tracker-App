"""The 15-line profit waterfall for one job. Every figure is calculated from entries; nothing is typed in."""

from decimal import ROUND_HALF_UP, Decimal

from .models import COUNTED

LINES = {
    1: "Original contract",
    2: "Change orders",
    3: "Adjusted revenue",
    4: "Materials",
    5: "Assembly",
    6: "Install labor",
    7: "Countertops",
    8: "Design",
    9: "Subcontractors",
    10: "Card processing fees",
    11: "Unexpected",
    12: "Referral payout",
    13: "Job profit",
    14: "Sales commission",
    15: "Net to Mill City",
}
COST_LINES = range(4, 13)
CENT = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(value or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def labor_total(entry) -> Decimal:
    return money(entry.hours * entry.rate)


def compute(job) -> dict:
    by_line = {n: Decimal(0) for n in LINES}
    for e in job.ledger:
        by_line[e.line] += e.amount
    for e in job.labor:
        by_line[6] += labor_total(e)
    for e in job.assembly:
        if e.status in COUNTED:
            by_line[5] += e.total
    for r in job.reimbursements:
        if r.status in COUNTED:
            # shortcut: job reimbursements always land on materials; add a line picker if other kinds show up
            by_line[4] += r.amount

    by_line[1] = job.invoice_total or Decimal(0)
    by_line[3] = by_line[1] + by_line[2]
    by_line[4] += job.material_total or Decimal(0)
    by_line[12] += job.referral_amount or Decimal(0)
    by_line[13] = by_line[3] - sum(by_line[n] for n in COST_LINES)

    rate = (job.rep.commission_rate if job.rep else None) or Decimal(0)
    # shortcut: no commission on a loss; Skyler put the negative-profit rule on hold
    by_line[14] = max(by_line[13], Decimal(0)) * rate
    by_line[15] = by_line[13] - by_line[14]

    lines = [{"line": n, "label": label, "amount": str(money(by_line[n]))} for n, label in LINES.items()]
    revenue = by_line[3]
    margin = (by_line[15] / revenue * 100).quantize(Decimal("0.1")) if revenue else None
    return {
        "lines": lines,
        "job_profit": str(money(by_line[13])),
        "commission": str(money(by_line[14])),
        "net": str(money(by_line[15])),
        "margin_pct": str(margin) if margin is not None else None,
        "material_pending": job.material_total is None,
    }
