#!/usr/bin/env python
"""PreToolUse permission gate: fetch allowlist, WebSearch and the `mf` bash auto-allow (ТЗ §8.1).

Three modes, one script, stdlib only. Every mode is *fail-open into the normal permission flow*:
the only two possible outputs are `{}` (say nothing — Claude Code asks the user as usual) and an
`allow` decision. The gate never denies, never writes state and never raises: any malformed input,
missing file or unexpected error ends as `{}` with exit code 0.

    --mode fetch      allow when every URL found under the keys `url|uri|link|href` of
                      `tool_input` resolves to a host on `hooks/allowlist.txt`
                      (suffix match with a dot, so `evil-europa.eu` never matches `europa.eu`)
    --mode websearch  allow while a memoforge task is active (`hooks_common.find_active_task`)
                      and `CLAUDE_PLUGIN_OPTION_WEBSEARCH_AUTOALLOW` is not `false`
    --mode bash       allow exactly one invocation of `<plugin_root>/scripts/mf[.cmd]`, absolute
                      path, no shell operators anywhere in the command line

`analysis/03-scripts-audit.md` §6 (S-03) is the reason the fetch mode never scans arbitrary string
values for a URL: `{"url": null, "prompt": "https://edpb.europa.eu"}` used to be auto-allowed.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path
from urllib.parse import urlparse

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

HOOK_EVENT_NAME = "PreToolUse"

URL_KEYS: tuple[str, ...] = ("url", "uri", "link", "href")
"""§8.1: the only `tool_input` keys the fetch gate reads."""

ALLOWLIST_RELATIVE = "hooks/allowlist.txt"

WEBSEARCH_OPTION = "CLAUDE_PLUGIN_OPTION_WEBSEARCH_AUTOALLOW"

FALSE_VALUES: tuple[str, ...] = ("0", "false", "no", "off")
"""Mirror of `task.coerce_bool`; anything else (including an unset option) counts as enabled."""

SHELL_METACHARACTERS: tuple[str, ...] = ("&", ";", "|", "<", ">", "`", "$", "\n", "\r")
"""§8.1 bash gate: `&&`, `;`, `|`, `||`, redirects, `$( )`, backticks and newlines, plus `$`
expansions (an unexpanded `${VAR}` can never equal the absolute path we require anyway)."""

MF_BASENAMES: tuple[str, ...] = ("mf", "mf.cmd")


# --- plugin root ----------------------------------------------------------


def plugin_root() -> Path:
    """`$CLAUDE_PLUGIN_ROOT` when it looks like this plugin, otherwise the file's own root."""
    env = (os.environ.get("CLAUDE_PLUGIN_ROOT") or "").strip()
    if env:
        candidate = Path(env)
        try:
            if (candidate / "scripts" / "memoforge").is_dir():
                return candidate.absolute()
        except OSError:
            pass
    return PLUGIN_ROOT


# --- allowlist ------------------------------------------------------------


def load_allowlist(root: str | os.PathLike | None = None) -> frozenset:
    """Hosts of `hooks/allowlist.txt`; `#` starts a comment, so the `optional` group stays off."""
    path = Path(root or plugin_root()) / ALLOWLIST_RELATIVE
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        return frozenset()
    hosts = set()
    for line in raw.split("\n"):
        host = line.split("#", 1)[0].strip().lower()
        if host:
            hosts.add(host)
    return frozenset(hosts)


def host_allowed(host: str, allowlist: frozenset) -> bool:
    """Exact host or a dot-anchored suffix of one (`edpb.europa.eu` yes, `evil-europa.eu` no)."""
    if not host:
        return False
    if host in allowlist:
        return True
    return any(host.endswith("." + entry) for entry in allowlist)


def request_host(url: object) -> str:
    """Lower-case hostname of a url that may be requested; `""` when it may not be (D-151).

    The shared predicate of `memoforge.sources.request_host`, duplicated here because a hook stays
    stdlib-only and imports nothing at startup: `urlsplit(...).hostname` (IDNA-safe, lower-cased),
    `http(s)` only, and no userinfo at all — `https://outside.example#@govinfo.gov` is
    `outside.example` in both parsers, and `https://evil.example@govinfo.gov/` is refused outright
    rather than read as either of its two hosts. An empty answer is never allow-listed, so every
    url this cannot read simply falls through to the normal permission prompt.
    """
    text = str(url or "").strip()
    try:
        parts = urlparse(text)
        if parts.scheme.lower() not in ("http", "https"):
            return ""
        if "@" in (parts.netloc or ""):
            return ""
        return (parts.hostname or "").lower()
    except ValueError:
        return ""


def url_hosts(tool_input: object) -> list:
    """Lower-cased hosts of the `url|uri|link|href` values; `[]` for anything else (S-03)."""
    if not isinstance(tool_input, dict):
        return []
    hosts = []
    for key in URL_KEYS:
        value = tool_input.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        hosts.append(request_host(value))
    return hosts


# --- decisions ------------------------------------------------------------


def allow(reason: str) -> dict:
    """The one positive answer this gate can give (§8.1)."""
    return {
        "hookSpecificOutput": {
            "hookEventName": HOOK_EVENT_NAME,
            "permissionDecision": "allow",
            "permissionDecisionReason": reason,
        }
    }


def decide_fetch(payload: dict, root: str | os.PathLike | None = None) -> dict:
    """Allow only when every URL key present resolves to an allowlisted host."""
    hosts = url_hosts(payload.get("tool_input"))
    if not hosts:
        return {}
    allowlist = load_allowlist(root)
    if not all(host_allowed(host, allowlist) for host in hosts):
        return {}
    return allow("memoforge legal-research allowlist: " + ", ".join(hosts))


def websearch_enabled() -> bool:
    """`userConfig.websearch_autoallow`, default true (§8.4)."""
    return (os.environ.get(WEBSEARCH_OPTION) or "").strip().lower() not in FALSE_VALUES


def decide_websearch(payload: dict) -> dict:
    """Allow while a memoforge task is active and the option is on; otherwise stay silent."""
    if not websearch_enabled():
        return {}
    from memoforge import hooks_common

    cwd = payload.get("cwd")
    work_dir = hooks_common.find_active_task(cwd if isinstance(cwd, str) and cwd else None)
    if work_dir is None:
        return {}
    return allow(f"memoforge task active in {work_dir}; research WebSearch pre-approved")


def _has_metacharacter(text: str) -> bool:
    return any(token in text for token in SHELL_METACHARACTERS)


def _unquote(token: str) -> str:
    """`shlex.split(..., posix=False)` keeps the quotes; strip one matching outer pair."""
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        return token[1:-1]
    return token


def _norm(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def mf_targets(root: str | os.PathLike | None = None) -> set:
    """The two executables the bash gate accepts, normalised (§8.1: bare `mf.cmd` is not one)."""
    scripts = Path(root or plugin_root()) / "scripts"
    return {_norm(scripts / name) for name in MF_BASENAMES}


def parse_variants(command: str) -> list:
    """Token lists of both lexers (POSIX quoting and the Windows `posix=False` variant)."""
    variants = []
    for posix in (True, False):
        try:
            tokens = shlex.split(command, posix=posix)
        except ValueError:
            continue
        if not posix:
            tokens = [_unquote(token) for token in tokens]
        if tokens:
            variants.append(tokens)
    return variants


def decide_bash(payload: dict, root: str | os.PathLike | None = None) -> dict:
    """Auto-allow exactly one absolute-path `mf`/`mf.cmd` invocation; everything else passes through."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return {}
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return {}
    if _has_metacharacter(command):
        return {}
    variants = parse_variants(command)
    if not variants:
        return {}
    targets = mf_targets(root)
    for tokens in variants:
        executable = tokens[0]
        if not executable or not os.path.isabs(executable):
            continue
        if _norm(executable) not in targets:
            continue
        if any(_has_metacharacter(argument) for argument in tokens[1:]):
            return {}
        return allow(f"memoforge launcher: {executable}")
    return {}


DECIDERS = {"fetch": decide_fetch, "websearch": decide_websearch, "bash": decide_bash}


def decide(mode: str, payload: object, root: str | os.PathLike | None = None) -> dict:
    """Dispatch one hook payload to its mode; anything unexpected is `{}` (§8.1)."""
    if not isinstance(payload, dict):
        return {}
    if mode == "websearch":
        return decide_websearch(payload)
    handler = DECIDERS.get(mode)
    if handler is None:
        return {}
    return handler(payload, root)


# --- entry point ----------------------------------------------------------


def read_stdin() -> str:
    """Hook payload as text; `utf-8-sig` because PowerShell writes a BOM (§8.1)."""
    data = sys.stdin.buffer.read()
    return data.decode("utf-8-sig", errors="replace")


def main(argv: list | None = None, stdin_text: str | None = None, root: str | os.PathLike | None = None) -> str:
    """Return the JSON the hook prints; never raises, so the caller always exits 0."""
    try:
        parser = argparse.ArgumentParser(description="memoforge PreToolUse permission gate")
        parser.add_argument("--mode", required=True, choices=sorted(DECIDERS))
        args = parser.parse_args(argv)
        text = read_stdin() if stdin_text is None else stdin_text
        payload = json.loads(text) if text.strip() else None
        return json.dumps(decide(args.mode, payload, root), ensure_ascii=False)
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 — a hook must never fail loudly (§8.1)
        return "{}"


def _utf8_stdout() -> None:
    """CONVENTIONS: a hook may print a path or a host that the console codepage cannot encode."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass


if __name__ == "__main__":
    _utf8_stdout()
    try:
        sys.stdout.write(main())
    except SystemExit:
        sys.stdout.write("{}")
    sys.exit(0)
