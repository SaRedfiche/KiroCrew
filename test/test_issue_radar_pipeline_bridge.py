"""Tests for the pipeline stage bridge.

The bridge's whole claim is that an event log which records WHAT HAPPENED can be
replayed into the ledger that records WHERE EACH ITEM IS, without the source
pipeline changing and without the board changing. These tests pin that claim at
its three load-bearing points: the table is validated before anything is
written, an unnamed event is reported rather than guessed at, and the phase an
item ends on is the last stage it reached.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kiro_crew.apps.builtins.issue_radar.backend import crew_store, pipeline_bridge

OWNER, REPO = "acme", "demo-repo"


def _log(*records: dict) -> list[dict]:
    return list(records)


class TestStageTableValidation(unittest.TestCase):
    """A bad table is refused BEFORE any write, not at the offending event."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_the_shipped_table_is_valid(self):
        pipeline_bridge.validate_stage_table(pipeline_bridge.GH_AUTOFIX_STAGES)

    def test_an_unknown_phase_is_refused(self):
        with self.assertRaises(pipeline_bridge.BridgeError):
            pipeline_bridge.validate_stage_table({"x": ("not-a-phase", "claim")})

    def test_an_unknown_event_kind_is_refused(self):
        # The ledger's kind vocabulary is closed; a bridge must not widen it.
        with self.assertRaises(pipeline_bridge.BridgeError):
            pipeline_bridge.validate_stage_table({"x": ("claimed", "not-a-kind")})

    def test_an_empty_table_is_refused(self):
        with self.assertRaises(pipeline_bridge.BridgeError):
            pipeline_bridge.validate_stage_table({})

    def test_a_bad_table_writes_nothing(self):
        """The refusal must come before the first crew exists.

        A replay that dies halfway leaves a half-populated board, and half is
        indistinguishable from a real pipeline that stopped there.
        """
        with self.assertRaises(pipeline_bridge.BridgeError):
            pipeline_bridge.replay(
                OWNER,
                REPO,
                _log({"event": "scan", "issue": 1}),
                pipeline="p",
                table={"scan": ("nope", "claim")},
                root=self.root,
            )
        self.assertEqual(crew_store.fold_fabric(OWNER, REPO, self.root), [])


class TestReplayLandsOnTheLastStage(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def _fold_one(self, number: int) -> dict:
        items = [
            it for it in crew_store.fold_fabric(OWNER, REPO, self.root) if it["number"] == number
        ]
        self.assertEqual(len(items), 1, f"expected exactly one lane for #{number}")
        return items[0]

    def test_an_item_ends_on_the_last_stage_it_reached(self):
        summary = pipeline_bridge.replay(
            OWNER,
            REPO,
            _log(
                {"event": "scan", "issue": 42},
                {"event": "triage", "issue": 42},
                {"event": "implement_start", "issue": 42},
                {"event": "pr_opened", "issue": 42},
            ),
            pipeline="gh-autofix",
            table=pipeline_bridge.GH_AUTOFIX_STAGES,
            root=self.root,
        )
        self.assertEqual(summary["applied"], 4)
        self.assertEqual(summary["items"], 1)
        self.assertEqual(self._fold_one(42)["phase"], "awaiting-ci")

    def test_a_review_round_trip_ends_left_of_where_it_has_been(self):
        """The live phase is the record's, never the furthest column touched.

        An item that reached awaiting-merge and then failed is at handed-back,
        which sits OFF the spine — the board must not leave it parked at the
        right-most column it once occupied.
        """
        pipeline_bridge.replay(
            OWNER,
            REPO,
            _log(
                {"event": "implement_start", "issue": 7},
                {"event": "pr_opened", "issue": 7},
                {"event": "pr_green", "issue": 7},
                {"event": "implement_fail", "issue": 7},
            ),
            pipeline="gh-autofix",
            table=pipeline_bridge.GH_AUTOFIX_STAGES,
            root=self.root,
        )
        self.assertEqual(self._fold_one(7)["phase"], "handed-back")

    def test_concurrent_editing_items_are_not_refused(self):
        """Two items in implementing at once is what the source pipeline does.

        The store's editing cap is per crew, so the bridge's one-crew-per-item
        mapping must let this through. A refusal here would mean the bridge
        cannot represent a pipeline that dispatches in parallel — which is every
        pipeline worth drawing.
        """
        summary = pipeline_bridge.replay(
            OWNER,
            REPO,
            _log(
                {"event": "implement_start", "issue": 100},
                {"event": "implement_start", "issue": 101},
                {"event": "implement_start", "issue": 102},
            ),
            pipeline="gh-autofix",
            table=pipeline_bridge.GH_AUTOFIX_STAGES,
            root=self.root,
        )
        self.assertEqual(summary["refused"], [])
        self.assertEqual(summary["applied"], 3)
        for number in (100, 101, 102):
            self.assertEqual(self._fold_one(number)["phase"], "implementing")

    def test_an_unnamed_event_is_counted_not_guessed(self):
        summary = pipeline_bridge.replay(
            OWNER,
            REPO,
            _log(
                {"event": "scan", "issue": 5},
                {"event": "cleanup", "issue": 5},
                {"event": "cleanup", "issue": 5},
                {"event": "some_future_event", "issue": 5},
            ),
            pipeline="gh-autofix",
            table=pipeline_bridge.GH_AUTOFIX_STAGES,
            root=self.root,
        )
        self.assertEqual(summary["skipped_kinds"], {"cleanup": 2, "some_future_event": 1})
        # cleanup is housekeeping AFTER a terminal phase; binding it to a phase
        # would move a finished lane, so the item stays where scan put it.
        self.assertEqual(self._fold_one(5)["phase"], "selected")

    def test_an_event_about_no_item_is_counted_and_applied_to_nothing(self):
        summary = pipeline_bridge.replay(
            OWNER,
            REPO,
            _log({"event": "scan"}, {"event": "scan", "issue": 9}),
            pipeline="gh-autofix",
            table=pipeline_bridge.GH_AUTOFIX_STAGES,
            root=self.root,
        )
        self.assertEqual(summary["events_without_item"], 1)
        self.assertEqual(summary["items"], 1)

    def test_the_pr_family_key_is_read_too(self):
        """The two script families disagree on the key; reading both is what
        lets one table serve both without either script changing."""
        summary = pipeline_bridge.replay(
            OWNER,
            REPO,
            _log({"event": "pr_opened", "pr": 555}),
            pipeline="pr-drive",
            table=pipeline_bridge.GH_AUTOFIX_STAGES,
            root=self.root,
        )
        self.assertEqual(summary["applied"], 1)
        self.assertEqual(self._fold_one(555)["phase"], "awaiting-ci")

    def test_a_string_number_is_accepted_but_a_bool_is_not(self):
        summary = pipeline_bridge.replay(
            OWNER,
            REPO,
            _log({"event": "scan", "issue": "#77"}, {"event": "scan", "issue": True}),
            pipeline="gh-autofix",
            table=pipeline_bridge.GH_AUTOFIX_STAGES,
            root=self.root,
        )
        self.assertEqual(summary["items"], 1)
        self.assertEqual(summary["events_without_item"], 1)
        self.assertEqual(self._fold_one(77)["phase"], "selected")


class TestReadEventLog(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_a_missing_log_is_empty_not_an_error(self):
        self.assertEqual(pipeline_bridge.read_event_log(self.root / "nope.jsonl"), ([], 0))

    def test_a_torn_tail_does_not_hide_the_history_in_front_of_it(self):
        """The log is append-only and written live, so a truncated final line is
        an expected state rather than corruption."""
        path = self.root / "audit.jsonl"
        good = json.dumps({"event": "scan", "issue": 1})
        path.write_text(good + "\n" + '{"event": "scan", "iss', encoding="utf-8")
        self.assertEqual(pipeline_bridge.read_event_log(path), ([{"event": "scan", "issue": 1}], 1))

    def test_a_non_object_line_is_skipped(self):
        path = self.root / "audit.jsonl"
        path.write_text('[1,2]\n"str"\n17\n{"event":"scan","issue":3}\n', encoding="utf-8")
        self.assertEqual(pipeline_bridge.read_event_log(path), ([{"event": "scan", "issue": 3}], 3))


class TestABridgeCrewIsInert(unittest.TestCase):
    """A bridge crew is a DRAWING SURFACE and must never be drivable.

    ``crew_store``'s defaults are ``enabled: True, unattended: True,
    auto_merge: True, auto_resolve_conflicts: True``, and ``crew_runtime`` drives
    any crew for which ``unattended and is_live(crew)`` holds. So a crew created
    with only a name is a live worker allowed to merge and resolve conflicts on
    the repository, and replaying a log of N items would mint N of them. These
    tests pin the flags off at the point of creation, because "the caller
    remembers to pass them" is not a safety property.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def _crews(self) -> list[dict]:
        return crew_store.list_crews(OWNER, REPO, self.root)

    def test_replay_mints_only_inert_crews(self):
        pipeline_bridge.replay(
            OWNER,
            REPO,
            _log(
                {"event": "scan", "issue": 1},
                {"event": "implement_start", "issue": 2},
            ),
            pipeline="gh-autofix",
            table=pipeline_bridge.GH_AUTOFIX_STAGES,
            root=self.root,
        )
        crews = self._crews()
        self.assertEqual(len(crews), 2)
        for crew in crews:
            self.assertFalse(crew["enabled"], f"{crew['name']} is enabled")
            self.assertFalse(crew["unattended"], f"{crew['name']} is unattended")
            self.assertFalse(crew["auto_merge"], f"{crew['name']} may auto-merge")
            self.assertFalse(
                crew["auto_resolve_conflicts"], f"{crew['name']} may resolve conflicts"
            )

    def test_the_live_recorder_mints_only_inert_crews(self):
        pipeline_bridge.record_stage(
            OWNER,
            REPO,
            42,
            "implement_start",
            pipeline="gh-autofix",
            table=pipeline_bridge.GH_AUTOFIX_STAGES,
            root=self.root,
        )
        crew = self._crews()[0]
        self.assertFalse(crew["enabled"])
        self.assertFalse(crew["unattended"])

    def test_the_store_defaults_are_still_the_dangerous_ones(self):
        """Pins WHY the flags are passed, so this does not look like ceremony.

        If the store's defaults ever flip to off, this fails and whoever changed
        them learns that the bridge's explicit flags became redundant — rather
        than the bridge silently keeping a guard nobody understands.
        """
        self.assertTrue(crew_store._DEFAULT_CREW["unattended"])
        self.assertTrue(crew_store._DEFAULT_CREW["enabled"])
        self.assertTrue(crew_store._DEFAULT_CREW["auto_merge"])


class TestLedgerTextIsRedacted(unittest.TestCase):
    """The source log is arbitrary external input and the ledger line is served
    back to the dashboard, so the text path is an egress site."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_redaction_runs_before_truncation(self):
        """Truncating first can cut a token in half and leave a fragment the
        redactor no longer recognises, storing part of the secret."""
        calls: list[str] = []

        def fake_redact(text: str) -> str:
            calls.append(text)
            return text.replace("SECRET", "[redacted]")

        with mock.patch.object(pipeline_bridge, "redact_via_context", fake_redact):
            out = pipeline_bridge._safe_text("x" * 260 + "SECRET", "fallback")

        # The redactor saw the WHOLE string, not a pre-truncated prefix.
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].endswith("SECRET"))
        self.assertEqual(len(out), 200)

    def test_a_credential_in_event_detail_does_not_reach_the_ledger(self):
        def fake_redact(text: str) -> str:
            return text.replace("ghp_realtokenvalue", "[redacted]")

        with mock.patch.object(pipeline_bridge, "redact_via_context", fake_redact):
            pipeline_bridge.replay(
                OWNER,
                REPO,
                _log(
                    {
                        "event": "pr_opened",
                        "issue": 9,
                        "detail": "pushed to https://ghp_realtokenvalue@github.com/x/y",
                    }
                ),
                pipeline="gh-autofix",
                table=pipeline_bridge.GH_AUTOFIX_STAGES,
                root=self.root,
            )
        blob = crew_store.events_path(OWNER, REPO, self.root).read_text(encoding="utf-8")
        self.assertNotIn("ghp_realtokenvalue", blob)
        self.assertIn("[redacted]", blob)

    def test_an_empty_detail_falls_back_to_the_event_name(self):
        out = pipeline_bridge._safe_text("", "implement_start")
        self.assertEqual(out, "implement_start")


class TestCoverageReportAndEntryPoint(unittest.TestCase):
    """The two surfaces First Principles flagged as untested.

    `coverage_report` is what turns a mis-written table from a silently stalled
    lane into a list, and `main` is the only way an operator populates a board
    today -- so both earn a test rather than being dropped.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_coverage_splits_mapped_from_unmapped_by_volume(self):
        cov = pipeline_bridge.coverage_report(
            _log(
                {"event": "scan", "issue": 1},
                {"event": "scan", "issue": 2},
                {"event": "housekeeping_noise", "issue": 3},
                {"event": "housekeeping_noise", "issue": 4},
                {"event": "housekeeping_noise", "issue": 5},
                {"event": "implement_start", "issue": 6},
            ),
            pipeline_bridge.GH_AUTOFIX_STAGES,
        )
        self.assertEqual(cov["mapped"], {"scan": 2, "implement_start": 1})
        self.assertEqual(cov["unmapped"], {"housekeeping_noise": 3})
        self.assertEqual(cov["mapped_events"], 3)
        self.assertEqual(cov["unmapped_events"], 3)

    def test_unmapped_is_ordered_by_volume(self):
        # The highest-count unmapped name is the one worth reading first: either
        # the most important gap, or the clearest evidence it is housekeeping.
        cov = pipeline_bridge.coverage_report(
            _log(
                {"event": "rare_unmapped", "issue": 1},
                *[{"event": "loud_unmapped", "issue": n} for n in range(5)],
            ),
            pipeline_bridge.GH_AUTOFIX_STAGES,
        )
        self.assertEqual(list(cov["unmapped"]), ["loud_unmapped", "rare_unmapped"])

    def test_dry_run_reports_and_writes_nothing(self):
        log = self.root / "audit.jsonl"
        log.write_text(
            json.dumps({"event": "implement_start", "issue": 3}) + "\n", encoding="utf-8"
        )
        data = self.root / "data"
        rc = pipeline_bridge.main(
            [
                str(log),
                "--owner",
                OWNER,
                "--repo",
                REPO,
                "--pipeline",
                "p",
                "--root",
                str(data),
                "--dry-run",
            ]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(crew_store.fold_fabric(OWNER, REPO, data), [])

    def test_the_entry_point_populates_the_board(self):
        log = self.root / "audit.jsonl"
        log.write_text(
            "\n".join(
                json.dumps(r)
                for r in (
                    {"event": "scan", "issue": 11},
                    {"event": "implement_start", "issue": 11},
                    {"event": "pr_opened", "issue": 12},
                )
            )
            + "\n",
            encoding="utf-8",
        )
        data = self.root / "data"
        rc = pipeline_bridge.main(
            [
                str(log),
                "--owner",
                OWNER,
                "--repo",
                REPO,
                "--pipeline",
                "p",
                "--root",
                str(data),
            ]
        )
        self.assertEqual(rc, 0)
        lanes = {it["number"]: it["phase"] for it in crew_store.fold_fabric(OWNER, REPO, data)}
        self.assertEqual(lanes, {11: "implementing", 12: "awaiting-ci"})

    def test_an_empty_log_is_a_nonzero_exit(self):
        empty = self.root / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        rc = pipeline_bridge.main([str(empty), "--owner", OWNER, "--repo", REPO, "--pipeline", "p"])
        self.assertEqual(rc, 1)


class TestTheBridgeDoesNotTakeOverForeignCrews(unittest.TestCase):
    """Reuse is keyed on the stamp, never on the name.

    A name-only match would adopt a crew a human happens to have called
    ``<pipeline>#<number>`` and overwrite its work item and ledger.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_a_same_named_human_crew_is_refused_not_adopted(self):
        human = crew_store.create_crew(
            OWNER, REPO, {"name": "gh-autofix#42", "extra_prompt": "mine"}, self.root
        )
        with self.assertRaises(pipeline_bridge.BridgeError):
            pipeline_bridge.record_stage(
                OWNER,
                REPO,
                42,
                "implement_start",
                pipeline="gh-autofix",
                table=pipeline_bridge.GH_AUTOFIX_STAGES,
                root=self.root,
            )
        # Untouched: same id, still no work item, still the human's prompt.
        after = [c for c in crew_store.list_crews(OWNER, REPO, self.root)]
        self.assertEqual([c["id"] for c in after], [human["id"]])
        self.assertEqual(after[0]["extra_prompt"], "mine")
        self.assertEqual(crew_store.fold_fabric(OWNER, REPO, self.root), [])

    def test_the_bridge_reuses_its_own_stamped_crew(self):
        for _ in range(2):
            pipeline_bridge.record_stage(
                OWNER,
                REPO,
                7,
                "implement_start",
                pipeline="gh-autofix",
                table=pipeline_bridge.GH_AUTOFIX_STAGES,
                root=self.root,
            )
        crews = crew_store.list_crews(OWNER, REPO, self.root)
        self.assertEqual(len(crews), 1, "a second call must not mint a duplicate lane")
        self.assertIn(pipeline_bridge._BRIDGE_LABEL, crews[0]["labels"])


class TestTheLogReadGoesThroughTheCentralGate(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_a_refused_path_reads_as_empty(self):
        """The gate returning None must degrade to "no events", never raise."""
        with mock.patch.object(pipeline_bridge.hooks, "safe_read_file_bytes", lambda raw: None):
            self.assertEqual(pipeline_bridge.read_event_log(Path("/anything")), ([], 0))

    def test_the_read_is_routed_through_the_gate_not_path_read_text(self):
        """Pins the bypass shut: the operator supplies this path, so a read that
        skips the centralized is_sensitive_path enforcement is a bypass even on a
        machine the operator owns."""
        seen: list[str] = []

        def fake(raw: str) -> bytes:
            seen.append(raw)
            return b'{"event": "scan", "issue": 4}\n'

        with mock.patch.object(pipeline_bridge.hooks, "safe_read_file_bytes", fake):
            out, _torn = pipeline_bridge.read_event_log(Path("/some/audit.jsonl"))
        self.assertEqual(seen, ["/some/audit.jsonl"])
        self.assertEqual(out, [{"event": "scan", "issue": 4}])


class TestHostileAndOversizedInput(unittest.TestCase):
    """The source log is written by whatever owns it, so treat it as hostile."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_an_oversized_log_is_a_named_error_not_a_traceback(self):
        """An append-only log grows without bound, so every long-lived pipeline
        reaches the reader's size cap. `safe_read_file_bytes` RAISES there and
        catches only OSError, so an uncaught FileTooLargeError would surface as a
        traceback to whoever ran the script."""

        def boom(raw: str) -> bytes:
            raise pipeline_bridge.hooks.FileTooLargeError("File exceeds 50 MB safety cap")

        with mock.patch.object(pipeline_bridge.hooks, "safe_read_file_bytes", boom):
            with self.assertRaises(pipeline_bridge.BridgeError) as ctx:
                pipeline_bridge.read_event_log(Path("/big/audit.jsonl"))
        self.assertIn("too large", str(ctx.exception))

    def test_an_absurd_item_number_is_rejected(self):
        # int() on external text is unbounded in Python; a lane numbered 10**400
        # is not an item the board can mean anything about.
        summary = pipeline_bridge.replay(
            OWNER,
            REPO,
            _log(
                {"event": "scan", "issue": "9" * 400},
                {"event": "scan", "issue": 0},
                {"event": "scan", "issue": -5},
                {"event": "scan", "issue": 4242},
            ),
            pipeline="gh-autofix",
            table=pipeline_bridge.GH_AUTOFIX_STAGES,
            root=self.root,
        )
        self.assertEqual(summary["items"], 1)
        self.assertEqual(summary["events_without_item"], 3)
        lanes = [it["number"] for it in crew_store.fold_fabric(OWNER, REPO, self.root)]
        self.assertEqual(lanes, [4242])

    def test_a_terminal_escape_in_an_event_name_is_shown_not_interpreted(self):
        """`main` prints unmapped names, so a name carrying ANSI/OSC escapes would
        let whatever wrote the log drive the terminal of whoever inspects it."""
        hostile = "\x1b]0;pwned\x07evil\x1b[31m"
        out = pipeline_bridge._printable(hostile)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x07", out)
        self.assertIn("evil", out)

    def test_the_escaping_is_bounded(self):
        self.assertLessEqual(len(pipeline_bridge._printable("n" * 5000)), 120)


class TestOneBadLineNeverAbortsAReplay(unittest.TestCase):
    """Every rejection path must degrade, because aborting mid-loop leaves the
    board half-populated -- indistinguishable from a pipeline that stopped there,
    which is the failure this module exists to avoid."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_a_unicode_digit_that_int_rejects_does_not_abort(self):
        """`"\u2460".isdigit()` is True but `int()` raises on it, so an isdigit
        guard lets the crash through. isdecimal() is exactly int()'s domain."""
        summary = pipeline_bridge.replay(
            OWNER,
            REPO,
            _log(
                {"event": "scan", "issue": 1},
                {"event": "scan", "issue": "\u2460"},
                {"event": "scan", "issue": "\u00b2"},
                {"event": "scan", "issue": 2},
            ),
            pipeline="gh-autofix",
            table=pipeline_bridge.GH_AUTOFIX_STAGES,
            root=self.root,
        )
        self.assertEqual(summary["items"], 2)
        self.assertEqual(summary["events_without_item"], 2)

    def test_an_overlong_digit_string_does_not_abort(self):
        """int() raises on a digit string past CPython's conversion limit, so a
        value-only bound runs too late -- the length must be checked first."""
        summary = pipeline_bridge.replay(
            OWNER,
            REPO,
            _log(
                {"event": "scan", "issue": "9" * 5000},
                {"event": "scan", "issue": 77},
            ),
            pipeline="gh-autofix",
            table=pipeline_bridge.GH_AUTOFIX_STAGES,
            root=self.root,
        )
        self.assertEqual(summary["items"], 1)
        self.assertEqual(summary["events_without_item"], 1)

    def test_a_crew_collision_refuses_one_item_and_keeps_going(self):
        crew_store.create_crew(OWNER, REPO, {"name": "gh-autofix#2"}, self.root)
        summary = pipeline_bridge.replay(
            OWNER,
            REPO,
            _log(
                {"event": "implement_start", "issue": 1},
                {"event": "implement_start", "issue": 2},
                {"event": "implement_start", "issue": 3},
            ),
            pipeline="gh-autofix",
            table=pipeline_bridge.GH_AUTOFIX_STAGES,
            root=self.root,
        )
        self.assertEqual(len(summary["refused"]), 1)
        self.assertIn("#2", summary["refused"][0])
        # The items either side of the collision still landed.
        lanes = sorted(it["number"] for it in crew_store.fold_fabric(OWNER, REPO, self.root))
        self.assertEqual(lanes, [1, 3])

    def test_the_command_reports_an_oversized_log_and_exits_nonzero(self):
        def boom(raw: str) -> bytes:
            raise pipeline_bridge.hooks.FileTooLargeError("File exceeds 50 MB safety cap")

        with mock.patch.object(pipeline_bridge.hooks, "safe_read_file_bytes", boom):
            rc = pipeline_bridge.main(
                ["/big.jsonl", "--owner", OWNER, "--repo", REPO, "--pipeline", "p"]
            )
        self.assertEqual(rc, 1)


class TestReplayRefusesAPopulatedRoot(unittest.TestCase):
    """Enforced, not merely documented.

    Replay appends and the store stamps its own clock, so a second pass over a
    grown log gives historical transitions fresh timestamps: duplicate and
    backward timeline segments, every dwell reading wrong.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def _replay(self, number: int):
        return pipeline_bridge.replay(
            OWNER,
            REPO,
            _log({"event": "implement_start", "issue": number}),
            pipeline="gh-autofix",
            table=pipeline_bridge.GH_AUTOFIX_STAGES,
            root=self.root,
        )

    def test_a_second_replay_into_the_same_root_is_refused(self):
        self._replay(1)
        with self.assertRaises(pipeline_bridge.BridgeError) as ctx:
            self._replay(2)
        self.assertIn("already exist", str(ctx.exception))
        # Refused BEFORE writing: the second item never landed.
        lanes = [it["number"] for it in crew_store.fold_fabric(OWNER, REPO, self.root)]
        self.assertEqual(lanes, [1])

    def test_a_second_pipeline_may_be_drawn_on_the_same_board(self):
        """The precondition stops the SAME log replaying twice -- not a DIFFERENT
        pipeline sharing the repo's board, which is the point of a board that
        folds every crew in the repo."""
        self._replay(1)
        summary = pipeline_bridge.replay(
            OWNER,
            REPO,
            _log({"event": "implement_start", "issue": 2}),
            pipeline="pr-drive",
            table=pipeline_bridge.GH_AUTOFIX_STAGES,
            root=self.root,
        )
        self.assertEqual(summary["refused"], [])
        lanes = sorted(it["number"] for it in crew_store.fold_fabric(OWNER, REPO, self.root))
        self.assertEqual(lanes, [1, 2])

    def test_record_stage_is_not_gated_by_that_precondition(self):
        """The live path is incremental by design -- one event as it happens is
        the correct way to keep adding to a populated root."""
        self._replay(1)
        pipeline_bridge.record_stage(
            OWNER,
            REPO,
            9,
            "implement_start",
            pipeline="gh-autofix",
            table=pipeline_bridge.GH_AUTOFIX_STAGES,
            root=self.root,
        )
        lanes = sorted(it["number"] for it in crew_store.fold_fabric(OWNER, REPO, self.root))
        self.assertEqual(lanes, [1, 9])


class TestTornLinesAreCountedNotSilent(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_an_interior_torn_line_is_counted(self):
        """An append interrupted mid-write leaves a line with no newline, and the
        NEXT append concatenates onto it -- so one malformed INTERIOR line can
        swallow a real transition. It is skipped (refusing the whole log over one
        corrupt byte would be a worse trade) but counted, so the loss is visible."""
        path = self.root / "audit.jsonl"
        good = json.dumps({"event": "scan", "issue": 1})
        torn = '{"event": "implement_start", "iss{"event": "pr_opened", "issue": 2}'
        later = json.dumps({"event": "scan", "issue": 3})
        path.write_text(f"{good}\n{torn}\n{later}\n", encoding="utf-8")

        events, unparseable = pipeline_bridge.read_event_log(path)
        self.assertEqual(unparseable, 1)
        self.assertEqual([e["issue"] for e in events], [1, 3])

    def test_an_oversized_integer_token_is_counted_not_fatal(self):
        """Verified, not assumed: a bare integer token past CPython's int/str
        conversion limit makes json.loads raise a PLAIN ValueError from its
        internal int() -- JSONDecodeError is only a subclass, so a narrow clause
        misses it and the whole read dies over one line."""
        path = self.root / "audit.jsonl"
        good = json.dumps({"event": "scan", "issue": 1})
        huge = '{"event": "scan", "issue": ' + "9" * 5000 + "}"
        path.write_text(f"{good}\n{huge}\n{good}\n", encoding="utf-8")

        events, unparseable = pipeline_bridge.read_event_log(path)
        self.assertEqual(unparseable, 1)
        self.assertEqual(len(events), 2)

    def test_deeply_nested_json_is_counted_not_fatal(self):
        """Deeply nested JSON raises RecursionError, which is not a ValueError at
        all, so it would escape even a broadened ValueError clause."""
        path = self.root / "audit.jsonl"
        good = json.dumps({"event": "scan", "issue": 1})
        bomb = "[" * 20000 + "]" * 20000
        path.write_text(f"{good}\n{bomb}\n", encoding="utf-8")

        events, unparseable = pipeline_bridge.read_event_log(path)
        self.assertEqual(unparseable, 1)
        self.assertEqual(len(events), 1)

    def test_a_non_object_line_counts_as_unparseable_too(self):
        path = self.root / "audit.jsonl"
        path.write_text('[1,2]\n"str"\n17\n{"event":"scan","issue":3}\n', encoding="utf-8")
        events, unparseable = pipeline_bridge.read_event_log(path)
        self.assertEqual(unparseable, 3)
        self.assertEqual(len(events), 1)


class TestNoRefusalEscapesTheCommand(unittest.TestCase):
    """The CLASS fix, not one instance of it.

    Three separate review rounds found a new raise escaping `main` uncaught -- the
    reader's size cap, the crew-name collision, the populated-root precondition --
    because a per-raise-site catch must be remembered every time a refusal is
    added. `main` now wraps its whole body in one guard, so these tests assert the
    property (no BridgeError reaches the operator as a traceback) rather than
    re-testing each site.
    """

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.log = self.root / "audit.jsonl"
        self.log.write_text(
            json.dumps({"event": "implement_start", "issue": 5}) + "\n", encoding="utf-8"
        )

    def _main(self):
        return pipeline_bridge.main(
            [
                str(self.log),
                "--owner",
                OWNER,
                "--repo",
                REPO,
                "--pipeline",
                "p",
                "--root",
                str(self.root / "data"),
            ]
        )

    def test_a_populated_root_is_reported_not_raised(self):
        self.assertEqual(self._main(), 0)
        self.assertEqual(self._main(), 1)

    def test_a_partial_replay_exits_nonzero(self):
        """A wrapper script reads the exit code, not the output -- reporting
        success for an incomplete board is how a missing lane goes unnoticed."""
        data = self.root / "data"
        # A foreign crew on one of the items forces exactly one refusal.
        crew_store.create_crew(OWNER, REPO, {"name": "p#5"}, data)
        rc = self._main()
        self.assertEqual(rc, 1)

    def test_a_raise_from_anywhere_in_the_flow_is_caught(self):
        """Pins the guard's REACH: a refusal invented at an arbitrary depth still
        exits nonzero rather than surfacing as a traceback."""

        def boom(*a, **k):
            raise pipeline_bridge.BridgeError("some future refusal")

        with mock.patch.object(pipeline_bridge, "coverage_report", boom):
            self.assertEqual(self._main(), 1)


if __name__ == "__main__":
    unittest.main()
