"""Conductor agent installer + bundled acceptance evaluator.

The installer test mirrors the research-agent installer test's shape: stub the
agents dir and ``build_agent_config``, run the installer, assert on the JSON it
wrote. The evaluator tests run the real script over stdin/stdout — it is the
deterministic half of the conductor's patrol, so its verdict vocabulary is
pinned here.
"""

import json
import subprocess
import sys
from pathlib import Path

from kiro_crew import agent

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "kiro_crew"
    / "builtin_skills"
    / "conductor"
    / "scripts"
    / "accept_eval.py"
)


class TestConductorInstaller:
    def _install(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent, "kiro_agents_dir_path", lambda: tmp_path)
        monkeypatch.setattr(
            agent,
            "build_agent_config",
            lambda: {
                "name": "kirocrew",
                "prompt": "file://x",
                "mcpServers": {
                    "kirocrew-core": {"command": "/resolved/kirocrew", "args": ["mcp-core"]},
                    "builder-mcp": {"command": "/x/builder", "args": []},
                },
                "tools": ["fs_write", "@kirocrew-core"],
                "allowedTools": ["@kirocrew-core"],
            },
        )
        monkeypatch.setattr(
            agent,
            "_kirocrew_mcp_invocation",
            lambda sub: ("/resolved/kirocrew", [sub]),
        )
        agent._install_conductor_agent()
        return json.loads((tmp_path / "kirocrew-conductor.json").read_text(encoding="utf-8"))

    def test_identity_and_charter(self, tmp_path, monkeypatch):
        data = self._install(tmp_path, monkeypatch)
        assert data["name"] == "kirocrew-conductor"
        assert "work item" in data["prompt"]

    def test_no_write_tool_and_dashboard_not_preapproved(self, tmp_path, monkeypatch):
        """The two deliberate security properties of the spec.

        No ``fs_write``: the conductor cannot do a work item's work itself.
        ``@kirocrew-dashboard`` reachable but NOT in ``allowedTools``: its
        calls must keep passing through the tool-call hook where the deny
        floor and governance ceiling apply.
        """
        data = self._install(tmp_path, monkeypatch)
        assert "fs_write" not in data["tools"]
        assert "@kirocrew-dashboard" in data["tools"]
        assert "@kirocrew-dashboard" not in data["allowedTools"]

    def test_mcp_surface_is_narrowed_to_core_plus_dashboard(self, tmp_path, monkeypatch):
        """Inherited servers the conductor has no charter for are dropped."""
        data = self._install(tmp_path, monkeypatch)
        assert set(data["mcpServers"]) == {"kirocrew-core", "kirocrew-dashboard"}
        assert data["mcpServers"]["kirocrew-dashboard"]["args"] == ["mcp-dashboard"]


class TestAcceptEvaluator:
    def _run(self, items):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=json.dumps({"items": items}),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        return {r["id"]: r for r in json.loads(proc.stdout)["results"]}

    def test_verdict_vocabulary_across_kinds(self, tmp_path):
        exists = tmp_path / "made"
        exists.write_text("x")
        out = self._run(
            [
                {"id": "ok", "accept": {"kind": "cmd", "argv": [sys.executable, "-c", "pass"]}},
                {
                    "id": "bad",
                    "accept": {
                        "kind": "cmd",
                        "argv": [sys.executable, "-c", "raise SystemExit(3)"],
                    },
                },
                {"id": "blocked", "accept": {"kind": "cmd", "argv": ["curl", "http://x"]}},
                {"id": "have", "accept": {"kind": "file", "path": str(exists), "exists": True}},
                {
                    "id": "miss",
                    "accept": {"kind": "file", "path": str(tmp_path / "no"), "exists": True},
                },
                {"id": "human", "accept": {"kind": "human_approval"}},
                {"id": "junk", "accept": {"kind": "wat"}},
            ]
        )
        assert out["ok"]["verdict"] == "pass"
        assert out["bad"]["verdict"] == "fail"
        # The allowlist is the boundary that keeps a model-authored spec from
        # executing arbitrary commands unattended; a refusal is surfaced, never
        # silently retried.
        assert out["blocked"]["verdict"] == "refused"
        assert out["have"]["verdict"] == "pass"
        assert out["miss"]["verdict"] == "fail"
        assert out["human"]["verdict"] == "pending"
        assert out["junk"]["verdict"] == "error"

    def test_one_bad_spec_does_not_hide_the_others(self):
        out = self._run(
            [
                {"id": "broken", "accept": {"kind": "cmd", "argv": []}},
                {"id": "fine", "accept": {"kind": "cmd", "argv": [sys.executable, "-c", "pass"]}},
            ]
        )
        assert out["broken"]["verdict"] == "error"
        assert out["fine"]["verdict"] == "pass"

    def test_malformed_stdin_is_a_clean_exit_2(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT)],
            input="not json",
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 2
