"""JSON evidence writer for experiments; state never contains credentials by design."""

from __future__ import annotations

import json
from pathlib import Path

from .runner import ExecutionResult


def write_execution_log(result: ExecutionResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as log_file:
        json.dump(result.to_dict(), log_file, indent=2, sort_keys=True)
        log_file.write("\n")
