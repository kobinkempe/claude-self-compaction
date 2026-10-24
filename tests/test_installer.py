"""Installer/contract tests confined to temporary directories; no live Claude operations."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

setup = module("installer", ROOT / "install.py")
sc = module("runtime", ROOT / "selfcompact.py")


class InstallerChecks(unittest.TestCase):
    def test_custom_policy_matches_rendered_skill_and_hook_advice(self):
        p = setup.default_policy(200000)
        p["checkpoint_advice"]["100000"] = "Finish the current test batch first."
        p["additional_instructions"] = "Record exact file paths and pending experiments."
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "profile with spaces"
            result = setup.install(target, p, "authorized-relay", True)
            conf = json.loads(Path(result["config"]).read_text())
            skill = Path(result["skill"]).read_text(encoding="utf-8")
            self.assertEqual(conf["checkpoints"], [50000, 100000, 130000, 160000, 180000])
            self.assertIn("100,000 tokens | Finish the current test batch first.", skill)
            self.assertIn("Finish the current test batch first.", sc.checkpoint_note(100000, 101000, conf))
            self.assertIn(p["additional_instructions"], sc.checkpoint_note(100000, 101000, conf))
            self.assertNotIn("<absolute-path-to-this-repository>", skill)
            self.assertTrue(result["enabled"])

    def test_reinstall_preserves_unrelated_settings_config_and_backs_up(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d)
            (target / "selfcompact").mkdir()
            original = {"theme": "dark", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo keep-me"}]}]}}
            (target / "settings.json").write_text(json.dumps(original))
            (target / "selfcompact/config.json").write_text(json.dumps({"unrelated": "retain"}))
            setup.install(target, setup.default_policy(), "relay", True)
            result = setup.install(target, setup.default_policy(200000), "relay", True)
            s = json.loads((target / "settings.json").read_text())
            self.assertEqual(s["theme"], "dark")
            self.assertEqual(len(s["hooks"]["Stop"]), 2)
            self.assertEqual(s["hooks"]["Stop"][0], original["hooks"]["Stop"][0])
            self.assertEqual(json.loads(Path(result["config"]).read_text())["unrelated"], "retain")
            self.assertTrue((Path(result["backup"]) / "SKILL.md").exists())

    def test_missing_relay_installs_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            result = setup.install(Path(d), setup.default_policy(), "", True)
            self.assertFalse(result["enabled"])
            self.assertIn("I authorize this chat", (Path(d) / "selfcompact/RELAY_SETUP.txt").read_text())

    def test_invalid_checkpoint_and_invalid_json_leave_existing_files_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d)
            settings = target / "settings.json"
            settings.write_text('{"theme":"dark"}')
            p = setup.default_policy(); p["checkpoints"][1] = 1000001
            with self.assertRaises(ValueError): setup.install(target, p, "relay", True)
            self.assertEqual(settings.read_text(), '{"theme":"dark"}')
            settings.write_text('invalid settings')
            with self.assertRaises(ValueError): setup.install(target, setup.default_policy(), "relay", True)
            self.assertEqual(settings.read_text(), 'invalid settings')
            self.assertFalse((target / "skills").exists())

    def test_disabled_setup_cannot_request_or_dispatch_a_worker(self):
        with patch.object(sc, "config", return_value={"enabled": False}), patch.object(sc, "session_arg", side_effect=AssertionError("must stop before session operations")):
            with self.assertRaises(SystemExit): sc.cmd_request(None)
            sc.cmd_hook_stop(None)

    def test_manual_copy_without_onboarding_cannot_request_compaction(self):
        with patch.object(sc, "config", return_value={}), patch.object(sc, "session_arg", side_effect=AssertionError("must stop before session operations")):
            with self.assertRaises(SystemExit): sc.cmd_request(None)
            sc.cmd_hook_stop(None)

    def test_headless_cli_keeps_the_selected_login_profile(self):
        with patch.object(sc, "CONFIG_DIR", Path("C:/synthetic profile")), patch.dict(sc.os.environ, {"CLAUDE_PID": "fake", "ANTHROPIC_API_KEY": "synthetic-secret"}):
            env = sc.headless_env()
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(Path("C:/synthetic profile")))
        self.assertNotIn("CLAUDE_PID", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_disabled_setup_is_explained_at_session_start(self):
        out = io.StringIO()
        with patch.object(sc, "config", return_value={"enabled": False}), patch.object(sc, "read_hook_event", return_value={"session_id": "fake", "source": "startup"}), patch.dict(sc.os.environ, {"CLAUDE_CODE_HOST_SESSION_ID": "fake-host"}), contextlib.redirect_stdout(out):
            sc.cmd_hook_session_start(None)
        note = json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]
        self.assertIn("BOTH Claude Desktop and the Claude Code CLI", note)
        self.assertIn("relay", note)

    def test_wizard_can_cancel_without_writing(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "new-profile"
            with patch("builtins.input", side_effect=["", "n", "", "n"]), contextlib.redirect_stdout(io.StringIO()):
                setup.wizard(target)
            self.assertFalse(target.exists())

    def test_extracted_install_and_reconfigure_run_offline(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "test profile"
            env = dict(setup.os.environ, CLAUDE_CONFIG_DIR=str(target))
            first = subprocess.run([sys.executable, str(ROOT / "install.py"), "--config-dir", str(target)], input="\nn\n\ny\n", capture_output=True, text=True, env=env, timeout=20)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertIn("DISABLED", first.stdout)
            installed = target / "selfcompact/tool/install.py"
            second = subprocess.run([sys.executable, str(installed), "--configure"], input="200000\nn\nrelay-test\ny\ny\n", capture_output=True, text=True, env=env, timeout=20)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            conf = json.loads((target / "selfcompact/config.json").read_text())
            self.assertTrue(conf["enabled"])
            self.assertEqual(conf["checkpoints"][-1], 180000)
            skill = (target / "skills/self-compact/SKILL.md").read_text(encoding="utf-8")
            self.assertIn("180,000 tokens", skill)


if __name__ == "__main__": unittest.main()
