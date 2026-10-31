"""Monthly statements per book: deposits, withdrawals, every trade, fees, realized P&L after fees, and cash at the
month's end, as JSON, CSV, or a one-file PDF. Dividends are the cash dividends paid in the month (corporate
actions, applied in the simulated books and confirmed by the owner in the real one)."""

from __future__ import annotations

import calendar
import csv
import datetime as dt
import io
from decimal import Decimal
from typing import Any

from ghostline import ledger
from ghostline.db import Database
from ghostline.ledger import Trade


def month_bounds(month: str) -> tuple[dt.datetime, dt.datetime]:
    y, m = (int(x) for x in month.split("-"))
    start = dt.datetime(y, m, 1, tzinfo=dt.UTC)
    end = dt.datetime(y, m, calendar.monthrange(y, m)[1], 23, 59, 59, 999999, tzinfo=dt.UTC)
    return start, end


def build(db: Database, book: str, month: str) -> dict[str, Any]:
    b = db.fetch_one("SELECT id, name FROM books WHERE kind = %s", (book,))
    if not b:
        raise LookupError(f"no book {book}")
    start, end = month_bounds(month)
    flows = db.fetch_all(
        "SELECT amount, at, note FROM cash_flows WHERE book_id = %s AND at <= %s ORDER BY at, id", (b["id"], end)
    )
    rows = db.fetch_all("SELECT * FROM trades WHERE book_id = %s AND at <= %s ORDER BY at, id", (b["id"], end))
    trades = [Trade.from_row(r) for r in rows]
    closed, _ = ledger.settle(trades)
    in_month = [r for r in rows if start <= r["at"] <= end]
    m_flows = [f for f in flows if start <= f["at"] <= end]
    deposits = sum((Decimal(f["amount"]) for f in m_flows if Decimal(f["amount"]) > 0), Decimal(0))
    withdrawals = -sum((Decimal(f["amount"]) for f in m_flows if Decimal(f["amount"]) < 0), Decimal(0))
    realized = sum((c.pnl for c in closed if start <= c.at <= end), Decimal(0))
    fees = sum((Decimal(r["fees"] or 0) for r in in_month), Decimal(0))
    dividends = sum((Decimal(r["quantity"]) * Decimal(r["fill"]) for r in in_month if r["side"] == "dividend"),
                    Decimal(0))
    return {
        "book": book,
        "name": b["name"],
        "month": month,
        "deposits": str(ledger.money(deposits)),
        "withdrawals": str(ledger.money(withdrawals)),
        "fees": str(ledger.money(fees)),
        "realized_pnl": str(ledger.money(realized)),
        "dividends": str(ledger.money(dividends)),
        "dividends_note": "cash dividends paid in the month",
        "cash_at_end": str(ledger.cash_balance([Decimal(f["amount"]) for f in flows], trades)),
        "flows": [{"at": f["at"].isoformat(), "amount": str(f["amount"]), "note": f["note"]} for f in m_flows],
        "trades": [
            {
                "at": r["at"].isoformat(),
                "ticker": r["ticker"],
                "asset": r["asset"],
                "side": r["side"],
                "quantity": format(Decimal(r["quantity"]).normalize(), "f"),
                "fill": str(r["fill"]),
                "fees": str(r["fees"]),
                "option_symbol": r.get("option_symbol"),
                "note": r.get("note"),
            }
            for r in in_month
        ],
    }


def to_csv(s: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Ghostline statement", s["name"], s["month"]])
    for k in ("deposits", "withdrawals", "fees", "realized_pnl", "cash_at_end"):
        w.writerow([k, s[k]])
    w.writerow(["dividends", s["dividends_note"]])
    w.writerow([])
    w.writerow(["date", "type", "ticker", "side", "quantity", "price", "fees", "amount", "note"])
    for f in s["flows"]:
        w.writerow(
            [
                f["at"],
                "deposit" if Decimal(f["amount"]) > 0 else "withdrawal",
                "",
                "",
                "",
                "",
                "",
                f["amount"],
                f["note"] or "",
            ]
        )
    for t in s["trades"]:
        w.writerow(
            [
                t["at"],
                t["asset"],
                t["option_symbol"] or t["ticker"],
                t["side"],
                t["quantity"],
                t["fill"],
                t["fees"],
                "",
                t["note"] or "",
            ]
        )
    return buf.getvalue()


# ── a plain PDF, written by hand: Letter pages, Helvetica (a base font every reader has), text lines ───────────
def _pdf_text(s: str) -> str:
    s = s.encode("latin-1", "replace").decode("latin-1")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def to_pdf(s: dict[str, Any]) -> bytes:
    lines = [
        (16, f"Ghostline statement: {s['name']}, {s['month']}"),
        (10, ""),
        (11, f"Deposits {s['deposits']}    Withdrawals {s['withdrawals']}    Fees {s['fees']}"),
        (11, f"Realized P&L after fees {s['realized_pnl']}    Cash at month end {s['cash_at_end']}"),
        (11, f"Dividends: {s['dividends_note']}"),
        (10, ""),
    ]
    if s["flows"]:
        lines.append((12, "Deposits and withdrawals"))
        lines += [(9, f"{f['at'][:10]}  {f['amount']:>14}  {(f['note'] or '')[:70]}") for f in s["flows"]]
        lines.append((10, ""))
    lines.append((12, "Trades" if s["trades"] else "No trades this month"))
    for t in s["trades"]:
        what = t["option_symbol"] or t["ticker"]
        lines.append(
            (9, f"{t['at'][:10]}  {t['side']:<13} {t['quantity']:>12} {what:<22} at {t['fill']:>12}  fees {t['fees']}")
        )
    pages: list[list[tuple[int, str]]] = [[]]
    y = 0
    for size, text in lines:
        step = size + 6
        if y + step > 700:
            pages.append([])
            y = 0
        pages[-1].append((size, text))
        y += step
    objects: list[bytes] = []
    n_pages = len(pages)
    # 1 catalog, 2 pages, 3 font, then (page, content) pairs
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n_pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, page in enumerate(pages):
        ops, y = ["BT"], 750
        for size, text in page:
            ops.append(f"/F1 {size} Tf 1 0 0 1 50 {y} Tm ({_pdf_text(text)}) Tj")
            y -= size + 6
        ops.append(f"/F1 8 Tf 1 0 0 1 50 30 Tm (Page {i + 1} of {n_pages}) Tj")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {5 + 2 * i} 0 R >>".encode()
        )
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()
