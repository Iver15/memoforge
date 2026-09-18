"""Synthetic language packs for tests — never the real `lib/i18n/*.json` files (D-168).

`fake_pack` starts from a deep copy of the English floor (`i18n_en.EN`), so a test pack
always has the full key tree and only the dotted-key overrides differ. `RU` is the shared
Russian overlay the language tests build on.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from memoforge import i18n, i18n_en

RU: dict = {
    "memo.sections.executive_summary": "Резюме",
    "memo.sections.background": "Контекст",
    "memo.sections.facts": "Факты",
    "memo.sections.assumptions": "Допущения",
    "memo.sections.conclusion": "Выводы",
    "memo.sections.recommendations": "Рекомендации",
    "memo.risk.label": "Риск",
    "memo.risk.levels.high": "высокий",
    "memo.risk.levels.medium": "средний",
    "memo.risk.levels.low": "низкий",
    "memo.risk.levels.undetermined": "не определён",
    "memo.lint_off": ["L-03"],
    "memo.placeholders_ignore_case": False,
}
"""Dotted-key overrides for a Russian test pack: sections, the risk label and levels,
`L-03` off and case-sensitive placeholders."""


def _apply(pack: dict, dotted: str, value: object) -> None:
    """Set one dotted key (`memo.risk.label`) inside a nested pack dict."""
    node = pack
    parts = dotted.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def fake_pack(directory: str | Path, code: str, overrides: dict | None = None) -> Path:
    """Write `<directory>/<code>.json`: EN deep copy + dotted-key overrides, UTF-8, non-ASCII raw."""
    pack = copy.deepcopy(i18n_en.EN)
    pack["code"] = code
    pack["name"] = i18n.LANGUAGE_NAMES.get(code, code)
    for dotted, value in (overrides or {}).items():
        _apply(pack, dotted, value)
    path = Path(directory) / f"{code}.json"
    path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    return path
