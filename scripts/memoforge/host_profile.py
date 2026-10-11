"""Small host boundary; importing this module requires no optional dependencies."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HostProfile:
    name: str
    plugin_name: str
    option_defaults: dict


def get_profile(root: Path | None = None) -> HostProfile:
    root = root or Path(__file__).resolve().parents[2]
    metadata = root / 'memoforge-platform.json'
    packaged = None
    if metadata.exists():
        try:
            packaged = json.loads(metadata.read_text(encoding='utf-8'))['platform']
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError('invalid_platform: malformed package metadata') from exc
    explicit = os.environ.get('MEMOFORGE_PLATFORM') or None
    name = explicit or packaged or 'claude'
    if name not in ('claude', 'codex', 'zcode') or (packaged and packaged not in ('claude', 'codex', 'zcode')):
        raise ValueError(f'invalid_platform: {name}')
    if explicit and packaged and explicit != packaged:
        raise ValueError(f'platform_mismatch: package={packaged}, environment={explicit}')
    defaults = {} if name == 'claude' else {'memo_language': 'ru', 'ui_language': 'ru', 'dashboard': 'false', 'writer_model': 'inherit'}
    return HostProfile(name, 'memoforge' if name == 'claude' else 'memoforge-'+name, defaults)


def runtime_root() -> Path:
    own = Path(__file__).resolve().parents[2]
    profile = get_profile(own)
    keys = {'claude': ('CLAUDE_PLUGIN_ROOT',), 'codex': ('PLUGIN_ROOT', 'CLAUDE_PLUGIN_ROOT'), 'zcode': ('ZCODE_PLUGIN_ROOT', 'CLAUDE_PLUGIN_ROOT')}[profile.name]
    for key in keys:
        raw = os.environ.get(key)
        if not raw:
            continue
        candidate = Path(raw)
        if (candidate/'scripts'/'memoforge').is_dir() and get_profile(candidate).name == profile.name:
            return candidate.absolute()
    return own


def data_candidates(profile: HostProfile, root: Path) -> list[dict]:
    out = []
    keys = {'claude': ('CLAUDE_PLUGIN_DATA',), 'codex': ('PLUGIN_DATA',), 'zcode': ('ZCODE_PLUGIN_DATA',)}[profile.name]
    for key in keys:
        raw = (os.environ.get(key) or '').strip()
        if raw:
            out.append({'source': key.lower(), 'path': Path(raw)})
    # Compatibility variables belong to this host only when their root identifies this package.
    compatible_root = os.environ.get('CLAUDE_PLUGIN_ROOT')
    if profile.name != 'claude' and compatible_root and Path(compatible_root).resolve() == root.resolve():
        raw = (os.environ.get('CLAUDE_PLUGIN_DATA') or '').strip()
        if raw:
            out.append({'source': 'claude_plugin_data', 'path': Path(raw)})
    local = (os.environ.get('LOCALAPPDATA') or '').strip()
    home = (os.environ.get('HOME') or os.environ.get('USERPROFILE') or '').strip()
    if local:
        out.append({'source': 'localappdata', 'path': Path(local)/profile.name/'plugin-data'/profile.plugin_name})
    elif home:
        out.append({'source': 'home', 'path': Path(home)/('.'+profile.name)/'plugin-data'/profile.plugin_name})
    out.append({'source': 'plugin_root', 'path': root/'.data'})
    return out


def require_task_platform(state: dict) -> None:
    expected = state.get('runtime_platform', 'claude')
    current = get_profile().name
    if expected != current:
        raise ValueError(f'platform_mismatch: task={expected}, host={current}')
