"""Shared driver for the S2 tests: runs the protocol loop with the `mf probe dry-run` fixtures."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

# D-147: `planning` issues a real `mf sources preflight` script step, and `act()` runs script steps
# in process. CONVENTIONS forbids a network call from the suite, so every driven run is offline.
os.environ.setdefault("MEMOFORGE_OFFLINE", "1")

from memoforge import gates, machine, modes, probe, state_io, task  # noqa: E402


def namespace(**kwargs) -> argparse.Namespace:
    kwargs.setdefault("human", False)
    return argparse.Namespace(**kwargs)


class Driver:
    """`task new` + the next/act/report loop, with a hook for injecting failures."""

    def __init__(
        self,
        root: Path,
        mode: str = "full",
        *,
        slug: str = "test",
        user_config: dict | None = None,
        language: str = "en",
        ui_language: str = "en",
    ) -> None:
        self.root = Path(root)
        self.mode = mode
        self.work_dir = self.root / f"memo-20260908T120000Z-{slug}"
        task.create_work_dir_tree(self.work_dir)
        # D-224: the memo and interface languages of the task, so a test can drive a non-English run.
        state = task.build_initial_state(
            task_id=self.work_dir.name,
            user_query="How long may the client keep customer records?",
            language=language,
            work_dir=self.work_dir,
            output_folder=self.root,
            config=modes.resolve_config(None, user_config or {}),
            ui_language=ui_language,
        )
        state_io.create_state(self.work_dir, state)
        self.next_calls = 0

    # -- primitives --------------------------------------------------------
    def state(self) -> dict:
        return state_io.read_state(self.work_dir)

    def next(self) -> dict:
        self.next_calls += 1
        return machine.run_next(namespace(workdir=str(self.work_dir)))

    def report(self, step: str, attempt: int, **kwargs) -> dict:
        payload = {
            "workdir": str(self.work_dir),
            "step": step,
            "attempt": attempt,
            "agent": None,
            "status": "ok",
            "answers": None,
            "generation": None,
            "stdout": None,
        }
        payload.update(kwargs)
        return machine.run_report(namespace(**payload))

    def parse_gate(self, action: dict, text: str) -> dict:
        return gates.run_parse(
            namespace(
                workdir=str(self.work_dir),
                gate=None,
                text=text,
                step=action["step_id"],
                attempt=action["attempt"],
                generation=action.get("generation", 0),
            )
        )

    # -- acting on one action ---------------------------------------------
    def act(
        self,
        action: dict,
        *,
        fail_slots: tuple = (),
        skip_slots: tuple = (),
        gate_text: str | None = None,
    ) -> None:
        kind = action.get("kind")
        if kind == "script":
            machine.run_command(list(action["command"]))
            return
        if kind == "inline-llm":
            target = self.work_dir / action["write_to"]
            document = probe.fixture_plan() if target.name == "plan.json" else probe.fixture_mcp_probe()
            target.parent.mkdir(parents=True, exist_ok=True)
            state_io.write_json_atomic(target, document)
            self.report(action["step_id"], action["attempt"])
            return
        if kind == "gate-auq":
            self.report(
                action["step_id"],
                action["attempt"],
                answers=probe._plan_answers(
                    self.work_dir, state_io.read_state(self.work_dir), self.mode
                ),
                generation=action.get("generation", 0),
            )
            return
        if kind == "gate-text":
            reply = gate_text or probe.GATE_REPLIES.get(str(action.get("phase")), "continue")
            self.parse_gate(action, reply)
            return
        if kind == "dispatch":
            step = {"step_id": action["step_id"], "attempt": action["attempt"]}
            for agent in action["agents"]:
                slot = agent["slot"]
                if slot in skip_slots:
                    continue
                if slot in fail_slots:
                    self.report(action["step_id"], action["attempt"], agent=slot, status="fail")
                    continue
                probe.run_fixture_agent(self.work_dir, self.state(), step, agent)
                self.report(action["step_id"], action["attempt"], agent=slot)
            return

    # -- loops -------------------------------------------------------------
    def run_until(self, phase: str, *, limit: int = 200) -> dict:
        """Stop with the first action `next` issues while the run is in `phase` (not acted on)."""
        for _ in range(limit):
            action = self.next()
            if action.get("errors"):
                raise AssertionError(f"next failed: {action['errors']}")
            if action.get("phase") == phase:
                return action
            self.act(action)
        raise AssertionError(f"phase {phase} never reached")

    def run_to_end(self, *, limit: int = 200) -> dict:
        for _ in range(limit):
            action = self.next()
            if action.get("errors"):
                raise AssertionError(f"next failed: {action['errors']}")
            if action.get("kind") == "terminal":
                return action
            self.act(action)
        raise AssertionError("the run never reached a terminal phase")


def temp_root(case) -> Path:
    """A TemporaryDirectory bound to the test case's lifetime."""
    holder = tempfile.TemporaryDirectory(prefix="mf-test-")
    case.addCleanup(holder.cleanup)
    return Path(holder.name)
