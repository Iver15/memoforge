"""Entry point: `python <root>/scripts/memoforge/__main__.py <group> <cmd> [...]` (ТЗ §5.1)."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # executed as a plain file by scripts/mf and scripts/mf.cmd
    _HERE = Path(__file__).resolve().parent
    # Python puts the script's own folder on `sys.path`, which exposes the internal
    # `memoforge/docx/` package as a top-level `docx` and shadows python-docx — the import
    # `docx/renderer.py` needs (§5.5, §5.6). Only the package folder's parent belongs there.
    sys.path[:] = [entry for entry in sys.path if not entry or Path(entry).resolve() != _HERE]
    sys.path.insert(0, str(_HERE.parent))
    from memoforge import cli
else:  # executed as `python -m memoforge`
    from . import cli


def main() -> int:
    """Run the CLI with process argv."""
    return cli.main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
