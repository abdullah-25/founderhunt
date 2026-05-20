"""Infer funding stage from Y Combinator batch tags (e.g. W25, S24)."""

import re
from datetime import datetime

YC_BATCH_RE = re.compile(r"\(([WS])(\d{2})\)|\b([WS])(\d{2})\b")


def extract_yc_batches(text: str) -> list[str]:
    batches: list[str] = []
    for match in YC_BATCH_RE.finditer(text):
        if match.group(1) and match.group(2):
            batches.append(f"{match.group(1)}{match.group(2)}")
        elif match.group(3) and match.group(4):
            batches.append(f"{match.group(3)}{match.group(4)}")
    return batches


def batch_code_to_stage(code: str, *, reference_year: int | None = None) -> str:
    """Map a YC batch code to a FounderHunt funding stage."""
    if len(code) < 2 or code[0] not in ("W", "S"):
        return "unknown"

    batch_year = 2000 + int(code[1:])
    ref = reference_year or datetime.utcnow().year
    age = ref - batch_year

    if age <= 1:
        return "pre_seed"
    if age == 2:
        return "seed"
    if age <= 4:
        return "series_a"
    if age <= 6:
        return "series_b"
    return "series_c_plus"


def infer_stage_from_yc_text(*texts: str) -> str:
    batches: list[str] = []
    for text in texts:
        if text:
            batches.extend(extract_yc_batches(text))

    if not batches:
        return "unknown"

    latest = max(batches, key=lambda code: (int(code[1:]), code[0]))
    return batch_code_to_stage(latest)
