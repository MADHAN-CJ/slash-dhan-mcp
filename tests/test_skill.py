from __future__ import annotations

import re
from pathlib import Path

import slashdhan

SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"


def test_every_function_named_in_the_skill_exists() -> None:
    text = SKILL.read_text(encoding="utf-8")
    names = set(re.findall(r"\bz\.([A-Za-z_][A-Za-z0-9_]*)", text))
    missing = sorted(n for n in names if not hasattr(slashdhan, n))
    assert not missing, missing
    assert "import slashdhan as z" in text
    assert "notify_email" in text and "feed_status" in text
    for banned in ("zapkite", "Zerodha", "Kite", "Zap ", "Slash Markets"):
        assert banned not in text, banned
