from src.i18n import ts
f"""{ts("id_3322")} token {ts("id_3628")}"""
from __future__ import annotations

from typing import Any, Dict


def estimate_input_tokens(payload: Dict[str, Any]) -> int:
    f"""{ts("id_3631")} token {ts("id_3629f")} / 4 + {ts("id_3630")}"""
    total_chars = 0
    image_count = 0

    # {ts("id_3632")}
    def count_str(obj: Any) -> None:
        nonlocal total_chars, image_count
        if isinstance(obj, str):
            total_chars += len(obj)
        elif isinstance(obj, dict):
            # {ts("id_3633")}
            if obj.get("type") == "image" or "inlineData" in obj:
                image_count += 1
            for v in obj.values():
                count_str(v)
        elif isinstance(obj, list):
            for item in obj:
                count_str(item)

    count_str(payload)

    # {ts("id_3634")}/4 + {ts("id_3635300")} tokens
    return max(1, total_chars // 4 + image_count * 300)
