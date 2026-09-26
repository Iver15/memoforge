"""The brief on the dashboard page (D-256)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _brief import FinishedTask  # noqa: E402
from test_brief import BLOCKER, BriefCase, FlowCase, set_status  # noqa: E402
from memoforge import brief_dashboard, i18n, machine  # noqa: E402

URL = "https://claude.ai/artifact/test-page"


def live(finished: FinishedTask, url: str = URL) -> None:
    def mutate(state: dict) -> None:
        state.setdefault("config", {})["dashboard"] = True
        state.setdefault("progress", {})["artifact_url"] = url

    finished.write_state(mutate)


def page(finished: FinishedTask, answer: dict) -> dict:
    block = answer["dashboard"]["write_db"]
    return json.loads(Path(block["file_path"]).read_text(encoding="utf-8"))


class BriefDashboardTest(BriefCase):
    def test_no_block_without_a_live_page(self):
        finished = self.task()
        self.assertNotIn("dashboard", finished.next())  # the fixture task has no artifact_url

    def test_the_writer_dispatch_carries_the_page(self):
        finished = self.task()
        live(finished)
        action = finished.next()
        self.assertEqual("dispatch", action["kind"])
        block = action["dashboard"]["write_db"]
        self.assertEqual((URL, "run", "state"), (block["url"], block["collection"], block["doc_id"]))
        self.assertEqual(str((finished.W / Path(*brief_dashboard.PATCH_FILE)).absolute()), block["file_path"])
        data = page(finished, action)
        self.assertEqual("brief", data["phase"])
        self.assertEqual(i18n.t("en", "ui.brief.dashboard.phase"), data["phase_label"])
        self.assertEqual([i18n.t("en", "ui.brief.dashboard.agents.writer")],
                         [row["description"] for row in data["agents_running"]])
        self.assertEqual(action["chat_line"], data["chat_line"])
        self.assertEqual("running", data["timeline"][-1]["state"])
        memo = machine.dashboard_patch(finished.state())
        for key in ("task_id", "query", "memo", "sources", "reviews", "plan", "intake", "banners"):
            self.assertEqual(memo[key], data[key], key)
        self.assertIsNone(data["gate"])

    def test_a_refusal_before_a_run_leaves_the_page_alone(self):
        finished = self.task()
        live(finished)
        finished.write_state(lambda state: state.update({"current_phase": "drafting"}))
        answer = finished.next()
        self.assertEqual("refused", answer["outcome"])
        self.assertNotIn("dashboard", answer)
        self.assertFalse((finished.W / Path(*brief_dashboard.PATCH_FILE)).exists())

    def test_a_failing_patch_builder_leaves_the_action_alone(self):
        finished = self.task()
        live(finished)
        with mock.patch.object(machine, "dashboard_patch", side_effect=RuntimeError("boom")):
            action = finished.next()
        self.assertEqual("dispatch", action["kind"])
        self.assertNotIn("dashboard", action)

    def test_a_declined_run_updates_the_page_through_report(self):
        finished = self.task()
        set_status(finished, "forced_exit_on_v2_with_remaining_issues", remaining_blocking_issues=[BLOCKER])
        live(finished)
        gate = finished.next()
        self.assertIn(gate["kind"], ("gate-auq", "gate-text"))
        self.assertEqual("waiting", page(finished, gate)["timeline"][-1]["state"])
        answer = finished.report(gate, text="no")
        self.assertEqual("done", answer["kind"])
        data = page(finished, answer)
        self.assertEqual(i18n.t("en", "ui.brief.dashboard.status.declined"), data["status_label"])
        self.assertEqual([], data["agents_running"])

    def test_a_brief_changed_on_disk_report_updates_the_page(self):
        """F6: this `report` answer carries `errors` and `kind: done`; the page still ends on "refused"."""
        finished = self.task()
        live(finished)
        _, review = self.promoted_writer(finished)
        self.assertEqual("dispatch", review["kind"])
        finished.write_fixture_reviews(review)
        (finished.W / "brief" / "v1.md").write_text("# edited by hand\n\n## Bottom line\n\nx\n", encoding="utf-8")
        # `_review_errors` reads the current version through `_verified_bytes` (brief.py:588) and raises.
        answer = finished.report(review, slot="fidelity", status="ok")
        self.assertEqual(["brief_changed_on_disk"], answer["errors"])
        self.assertEqual("done", answer["kind"])
        data = page(finished, answer)
        self.assertEqual("refused", data["status"])
        self.assertEqual("failed", data["timeline"][-1]["state"])  # F4: the refused step is not a green dot
        self.assertEqual([], data["brief"]["checks"])  # F5: an operational refusal code is not a check


class BriefDashboardFlowTest(FlowCase):
    def test_done_ends_the_page_on_the_brief(self):
        finished = self.task()
        live(finished)
        done, _ = self.drive(finished)
        data = page(finished, done)
        self.assertEqual("done", done["kind"])
        self.assertEqual(i18n.t("en", f"ui.brief.dashboard.status.{done['outcome']}"), data["status_label"])
        self.assertEqual(done["path"], data["brief"]["file"])
        self.assertTrue(all(row["state"] == "done" for row in data["timeline"]))


class OverlayTest(unittest.TestCase):
    """`overlay` on the branches a flow does not reach cheaply: a retried step and the check list."""

    @staticmethod
    def run_of(outcome: str, reasons: list) -> dict:
        steps = [
            {"step": "write", "attempt": 1, "started_at": "2026-01-01T00:00:00Z", "ended_at": "2026-01-01T00:01:00Z"},
            {"step": "revise", "attempt": 2, "started_at": "2026-01-01T00:02:00Z", "ended_at": "2026-01-01T00:03:00Z"},
        ]
        return {"outcome": outcome, "outcome_reasons": reasons, "steps": steps, "round": 1, "published": []}

    def test_an_unverified_run_lists_its_checks(self):
        data = brief_dashboard.overlay(self.run_of("unverified", ["BF-01"]), {"kind": "done", "text": "x"}, "en")
        self.assertEqual([i18n.t("en", "memo.brief.checks.BF-01")], data["brief"]["checks"])
        self.assertEqual(i18n.t("en", "ui.brief.dashboard.status.unverified"), data["status_label"])
        self.assertEqual("done", data["timeline"][-1]["state"])
        step = i18n.t("en", "ui.brief.steps.revise")
        self.assertEqual(i18n.t("en", "ui.brief.dashboard.attempt", step=step, attempt=2), data["timeline"][-1]["text"])
        self.assertEqual((2, 2, 2, 2), (data["phase_no"], data["phase_total"], data["steps_done"], data["steps_total"]))

    def test_a_refusal_code_that_is_also_a_check_name_is_not_a_check(self):
        # `writer_failed` names both a refusal (`ui.brief.refused.*`) and a check (`memo.brief.checks.*`).
        data = brief_dashboard.overlay(self.run_of("refused", ["writer_failed"]), {"kind": "done", "text": "x"}, "en")
        self.assertEqual([], data["brief"]["checks"])
        self.assertEqual("failed", data["timeline"][-1]["state"])


class PageTextTest(unittest.TestCase):
    HTML = (Path(__file__).resolve().parents[2] / "lib" / "dashboard.html").read_text(encoding="utf-8")

    def test_the_page_hides_the_brief_tab_without_brief_data(self):
        self.assertIn('id="tab-brief"', self.HTML)
        self.assertIn('if (briefPanel(data.brief)) { available.push("brief"); }', self.HTML)
        self.assertIn('"memo", "brief"];', self.HTML)  # TAB_NAMES ends with the memo and brief tabs

    def test_every_label_the_page_reads_exists_in_every_pack(self):
        for code in ("en", "ru", "de", "fr", "es"):
            labels = i18n.node(code, "ui.dashboard")
            for key in ("tab_brief", "card_brief", "label_brief_status", "label_brief_rounds",
                        "label_brief_checks"):
                self.assertIn(key, labels, (code, key))
            self.assertNotEqual(labels["tab_memo"], labels["tab_brief"], code)  # two tabs, two names


if __name__ == "__main__":
    unittest.main()
