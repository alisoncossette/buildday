"""Stead — care companion (Tier 2). SUBJECT to PRD.md.

Captures Ruby's day (mood, meds, food intake, hygiene, events) from MARS sensing + voice notes,
produces a fast shift HANDOFF summary, and tracks longitudinal TRENDS for the doctor.
"""


class CareLog:
    def __init__(self):
        # day -> latest observation dict. Ordered by insertion (so trends read chronologically).
        self._entries = {}

    def record(self, day, **entry):
        """Append an observation for `day` (mood, meds, food_intake, hygiene, note, ...).
        A later record for the same day updates that day's observation."""
        existing = self._entries.get(day, {})
        existing.update(entry)
        self._entries[day] = existing
        return dict(existing)

    def handoff_summary(self, day) -> str:
        """A short shift handoff for `day` — the 20-second briefing that replaces the hour-long one.
        Grounded in what was actually recorded (meds, food, mood). Always <= 280 chars."""
        entry = self._entries.get(day)
        if not entry:
            return f"No observations recorded for {day}."

        parts = [f"Handoff {day}:"]
        if "mood" in entry:
            parts.append(f"mood {entry['mood']}")
        if "meds" in entry:
            parts.append(f"meds {entry['meds']}")
        if "food_intake" in entry:
            parts.append(f"food intake {int(round(entry['food_intake'] * 100))}%")
        if "hygiene" in entry:
            parts.append(f"hygiene {entry['hygiene']}")
        if "note" in entry:
            parts.append(f"note: {entry['note']}")

        summary = "; ".join([parts[0]] + parts[1:]) if len(parts) > 1 else parts[0]
        return summary[:280]

    def trends(self, metric, window) -> dict:
        """Longitudinal trend for `metric` over the last `window` days, to answer the doctor's
        'how has she been?'. Returns {'direction': 'up'|'down'|'flat', 'change': float, 'values': [...]}."""
        days = sorted(self._entries.keys())
        values = [
            self._entries[d][metric]
            for d in days
            if metric in self._entries[d] and isinstance(self._entries[d][metric], (int, float))
        ]
        if window is not None:
            values = values[-window:]

        if len(values) < 2:
            return {"direction": "flat", "change": 0.0, "values": list(values)}

        change = values[-1] - values[0]
        if change > 0:
            direction = "up"
        elif change < 0:
            direction = "down"
        else:
            direction = "flat"
        return {"direction": direction, "change": float(change), "values": list(values)}
