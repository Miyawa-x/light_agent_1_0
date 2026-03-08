# Memory (v0)

This module provides three memory buckets, each backed by a JSON file.

Files

- `state_memory.json`: status-like memory (frequent updates)
- `dialogue_memory.json`: short-term dialogue turns
- `long_term_memory.json`: user facts + compressed summaries

Usage (Python)

```python
from memory_store import StateMemory, DialogueMemory, LongTermMemory

state = StateMemory()
state.reset_daily_if_needed()
state.set_item("cs61a", "completed")
state.set_item("take_meds", "pending", {"reset": "daily", "quota_per_day": 3})
state.increment_done("take_meds")

dialogue = DialogueMemory()
dialogue.append_turn("user", "I am learning MindSpore.")
dialogue.append_turn("assistant", "Great, I'll remember that.")

lt = LongTermMemory()
lt.add_fact("User is learning MindSpore.", importance=7, fact_type="skill", source="user", confidence=0.9)
lt.add_summary("User wants daily reminders to take meds.", importance=5)
```

Notes

- Daily reset uses local date (`YYYY-MM-DD`) and resets items with `meta.reset = "daily"`.
- State memory should not store future schedules or repeat rules; those belong in tasks/schedule.
- Dialogue memory caps at a max turn count (see `append_turn(max_turns=...)`).
