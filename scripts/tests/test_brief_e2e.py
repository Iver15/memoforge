"""End to end: `/memoforge:brief` over a finished memo, driven the way the skill drives it (plan 75A, D-226).

A task finished by `_pipeline.Driver(...).run_to_end()`; `mf brief next` / `mf brief report` answered
by fixture agents — a writer that writes a lint-clean brief derived from the delivered memo (one block
per leaf, the leaf's first token, the leaf's verdict) and two reviewers that approve every item. The
run ends `clean` with a valid `brief/brief.docx` that has no Sources part, and the task's own files
keep their bytes.
"""

from __future__ import annotations

import html
import re
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _brief import FinishedTask  # noqa: E402
from memoforge import brief_lint, docx, i18n, lint  # noqa: E402
from memoforge.docx import fallback, validate  # noqa: E402

PROSE = {
    "en": {
        "title": "decision brief",
        "question": "How long may the client keep customer records?",
        "main": (
            "The client may keep customer records only while the purpose that justified them lasts. "
            "The overall risk is {level}, and counsel should confirm the tax basis before the next audit."
        ),
        "claim": "Records may not be kept beyond their purpose {token}.",
        "condition": "A tax duty changes this only if it covers these records.",
        "reason": "The exposure turns on the tax carve-out.",
        "action": "1. Counsel confirms the tax basis for the seven-year period before the next audit.",
    },
    "ru": {
        "title": "справка для руководителя",
        "question": "Как долго клиент может хранить записи о клиентах?",
        "main": (
            "Клиент может хранить записи о клиентах, пока сохраняется цель, ради которой их собрали. "
            "Риск в целом {level}, юрист должен подтвердить налоговое основание до следующей проверки."
        ),
        "claim": "Записи нельзя хранить дольше, чем этого требует цель {token}.",
        "condition": "Налоговая обязанность меняет вывод, только если она распространяется на эти записи.",
        "reason": "Риск зависит от налогового исключения.",
        "action": "1. Юрист подтверждает налоговое основание семилетнего срока до следующей проверки.",
    },
}
"""The words of the fixture brief per memo language; labels, headings and verdicts come from the pack."""


def derived_brief(memo: str, language: str) -> str:
    """A brief built from `brief_lint.memo_facts`: one block per leaf, its first token, its verdict."""
    facts = brief_lint.memo_facts(memo, language)
    document = lint.parse_draft(memo, lint.grammar(language))
    prose = PROSE[language]

    def t(key: str) -> str:
        return i18n.t(language, key)

    def level(verdict: str) -> str:
        return t(f"memo.risk.levels.{verdict}")

    blocks = []
    for section_id, verdict in facts["leaves"].items():
        token = next(row for row in document["src_tokens"] if row["section_id"] == section_id)
        shown = f"[[src:{token['id']} {token['pinpoint']}]]" if token["pinpoint"] else f"[[src:{token['id']}]]"
        unconfirmed = f" ({t('memo.brief.unconfirmed')})" if verdict == brief_lint.UNDETERMINED else ""
        blocks += [
            f"### {facts['headings'][section_id]}",
            "",
            f"<!-- from §{section_id} -->",
            "",
            f"{prose['claim'].format(token=shown)} {prose['condition']}{unconfirmed}",
            "",
            f"{t('memo.risk.label')}: {level(verdict)}. {prose['reason']}",
            "",
        ]
    overall = max(facts["leaves"].values(), key=lambda key: brief_lint.VERDICT_RANK.get(key, 0))
    first = next(iter(facts["headings"].values()))
    lines = [
        f"# {first}: {prose['title']}",
        "",
        f"**{t('memo.brief.date_label')}:** 2026-09-24",
        "",
        f"**{t('memo.brief.jurisdictions_label')}:** EU",
        "",
        f"**{t('memo.brief.question_label')}:** {prose['question']}",
        "",
        "<!-- omitted -->",  # D-229: every leaf is kept, so the omitted list is empty
        "",
        f"## {t('memo.brief.sections.main')}",
        "",
        prose["main"].format(level=level(overall)),
        "",
        f"## {t('memo.brief.sections.conclusions')}",
        "",
        *blocks,
        f"## {t('memo.brief.sections.actions')}",
        "",
        prose["action"],
        "",
    ]
    return "\n".join(lines)


def docx_xml(path: Path) -> str:
    return zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")


def docx_text(path: Path) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", docx_xml(path)))


class BriefEndToEndTest(unittest.TestCase):
    """The whole `/memoforge:brief` loop over a finished task, in the memo's own language."""

    def drive(self, finished: FinishedTask, text: str) -> tuple[dict, list[tuple[str, list[str]]]]:
        """Act on every action as the skill does; returns the `done` action and the dispatch trail."""
        action = finished.next()
        trail: list[tuple[str, list[str]]] = []
        for _ in range(20):
            if action["kind"] == "done":
                return action, trail
            self.assertEqual("dispatch", action["kind"], action)
            # The fields the skill passes through or reports with (skills/brief/SKILL.md).
            for key in ("run_id", "step_id", "attempt", "agents", "chat_line"):
                self.assertIn(key, action)
            for agent in action["agents"]:
                for key in ("slot", "subagent_type", "model", "description", "prompt"):
                    self.assertIn(key, agent)
            slots = [agent["slot"] for agent in action["agents"]]
            trail.append((finished.run()["step"], slots))
            if slots == ["writer"]:
                finished.write_fixture_writer(action, text)
            else:
                # D-228: both brief reviewers run on Opus.
                self.assertEqual({"opus"}, {agent["model"] for agent in action["agents"]})
                finished.write_fixture_reviews(action)
            for slot in slots:  # every slot is reported before the next `next`
                answer = finished.report(action, slot=slot, status="ok")
                self.assertEqual((True, "ok"), (answer["accepted"], answer["status"]), answer)
            action = finished.next()
        self.fail("the brief run did not finish")

    def build(self, language: str, ui_language: str) -> tuple[FinishedTask, dict, str]:
        finished = FinishedTask(self, language=language, ui_language=ui_language)
        state = finished.state()
        memo = docx.select_draft(state, finished.W)["path"].read_text(encoding="utf-8-sig")
        text = derived_brief(memo, language)
        self.assertEqual([], brief_lint.lint_brief(text, memo, language=language), "the fixture brief lints clean")
        self.assertEqual([], brief_lint.parse_brief(text, language)["omitted"])
        before = finished.task_files()
        real_validate = validate.validate_path

        def spy(*args, **kwargs):  # the driver's own check, with the footnote map of its render
            self.checks.append(real_validate(*args, **kwargs))
            return self.checks[-1]

        self.checks: list[dict] = []
        with mock.patch.object(validate, "validate_path", side_effect=spy):
            done, trail = self.drive(finished, text)
        self.assertEqual(before, finished.task_files(), "the brief wrote a task file")
        self.assertEqual([("write", ["writer"]), ("review", ["fidelity", "form"])], trail)
        return finished, done, text

    def assert_clean_docx(self, finished: FinishedTask, done: dict, language: str, ui_language: str) -> str:
        run = finished.run()
        path = finished.W / "brief" / "brief.docx"
        self.assertEqual(("done", "clean", "docx", False), (done["kind"], done["outcome"], done["format"],
                                                             done["present"]))
        self.assertEqual(str(path), done["path"])
        self.assertEqual(i18n.t(ui_language, "ui.brief.done", path=done["path"]), done["text"])
        self.assertEqual(("clean", "clean", [], "brief/brief.docx"),
                         (run["verdict"], run["outcome"], run["outcome_reasons"], run["deliverable_path"]))
        self.assertEqual(0, run["versions"][-1]["lint_findings"])
        self.assertEqual([(True, [])], [(check["valid"], check["errors"]) for check in self.checks])
        body = docx_text(path)
        self.assertNotIn(fallback.label("sources_heading", language), body)
        self.assertNotIn(fallback.label("appendix_heading", language), body)
        self.assertNotIn("[[src:", body)
        self.assertNotIn("from §", body)
        self.assertNotIn("omitted", body)
        self.assertIn("5(1)(e))", body, "the token became an inline citation")
        for kind in ("main", "conclusions", "actions"):
            self.assertIn(i18n.t(language, f"memo.brief.sections.{kind}"), body)
        # The template's header lines are three paragraphs, not one line joined by the renderer.
        paragraphs = [html.unescape(re.sub(r"<[^>]+>", "", xml))
                      for xml in re.findall(r"<w:p[ >].*?</w:p>", docx_xml(path), flags=re.S)]
        holders = [next((index for index, text in enumerate(paragraphs)
                         if text.startswith(i18n.t(language, f"memo.brief.{key}") + ":")), None)
                   for key in brief_lint.HEADER_KEYS]
        self.assertNotIn(None, holders, paragraphs[:6])
        self.assertEqual(3, len(set(holders)), paragraphs[:6])
        return body

    def test_an_english_memo_gets_a_clean_brief(self):
        finished, done, _ = self.build("en", "en")
        body = self.assert_clean_docx(finished, done, "en", "en")
        self.assertIn("Risk: medium.", body)

    def test_a_russian_memo_gets_a_clean_russian_brief(self):
        finished, done, text = self.build("ru", "ru")
        body = self.assert_clean_docx(finished, done, "ru", "ru")
        risk = f"{i18n.t('ru', 'memo.risk.label')}: {i18n.t('ru', 'memo.risk.levels.medium')}."
        self.assertIn(risk, text)
        self.assertIn(risk, body, "the Cyrillic risk line passed B-05/B-08 and reached the docx")
        self.assertNotEqual(i18n.t("en", "ui.brief.done", path=done["path"]), done["text"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
