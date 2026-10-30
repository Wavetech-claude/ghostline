A private investor attached a broker confirmation (a screenshot or a PDF) at the path below. Read it with the Read
tool and extract the trade's fields. Do not invent a field you cannot read; leave it null.

File: {path}

Answer with JSON only:
{{
  "ticker": "<symbol or null>",
  "asset": "stock" | "option" | null,
  "side": "buy" | "sell" | "sell_to_open" | "buy_to_close" | null,
  "quantity": "<number as text or null>",
  "fill": "<price per share, or premium per share for an option, as text or null>",
  "at": "<ISO 8601 date-time or null>",
  "fees": "<number as text or null>",
  "broker": "<broker name or null>",
  "option_symbol": "<OCC symbol or null>",
  "notes": "<anything on the confirmation the owner should check, one sentence>"
}}
