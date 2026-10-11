"""Windows native bridge: parse the cached interpreter without splitting paths at spaces."""

from __future__ import annotations
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memoforge import pylauncher  # noqa: E402


def main() -> None:
    command = pylauncher.python_cmd()
    root = pylauncher.plugin_root()
    env = dict(os.environ)
    site = str(pylauncher.site_packages_dir())
    env["PYTHONPATH"] = site + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    os.execvpe(command[0], command + [str(root / "scripts/memoforge/__main__.py")] + sys.argv[1:], env)


if __name__ == "__main__":
    main()
