"""The rules as stored, and a check that records its pass or refusal in risk_decisions (the journal reads it)."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from ghostline import risk
from ghostline.db import Database


def rule_set(db: Database) -> risk.RuleSet:
    return risk.RuleSet.from_rows(db.fetch_all("SELECT key, value, enabled FROM risk_rules"))


def save_rules(db: Database, rules: list[dict[str, Any]], by: str) -> list[dict[str, Any]]:
    """Upsert each rule, logging every change (old → new). Unknown keys are refused by the route."""
    out = []
    with db.connection() as conn:
        for r in rules:
            old = conn.execute("SELECT value, enabled FROM risk_rules WHERE key = %s", (r["key"],)).fetchone()
            value, enabled = Decimal(str(r["value"])), bool(r["enabled"])
            if old and Decimal(old["value"]) == value and bool(old["enabled"]) == enabled:
                continue
            conn.execute("INSERT INTO risk_rules (key, value, enabled, updated_at) VALUES (%s, %s, %s, now()) "
                         "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, enabled = EXCLUDED.enabled, "
                         "updated_at = now()", (r["key"], value, enabled))
            conn.execute("INSERT INTO risk_rule_changes (key, old_value, old_enabled, new_value, new_enabled, "
                         "changed_by) VALUES (%s, %s, %s, %s, %s, %s)",
                         (r["key"], old["value"] if old else None, old["enabled"] if old else None, value, enabled,
                          by))
            before = None if not old else {"value": str(old["value"]), "enabled": old["enabled"]}
            out.append({"key": r["key"], "old": before, "new": {"value": str(value), "enabled": enabled}})
        conn.commit()
    return out


def check_and_record(db: Database, idea: risk.Idea, account: risk.Account, subject_kind: str,
                     subject_id: int | None = None) -> risk.Decision:
    d = rule_set(db).check_idea(idea, account)
    db.execute("INSERT INTO risk_decisions (subject_kind, subject_id, result, rule, reason) "
               "VALUES (%s, %s, %s, %s, %s)", (subject_kind, subject_id, d.result, d.rule, d.reason))
    return d
