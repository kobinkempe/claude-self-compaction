"""Pure, synthetic checks: no live process, hooks or Claude state are touched."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("selfcompact", Path(__file__).parents[1] / "selfcompact.py")
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)

class OfflineChecks(unittest.TestCase):
    def entry(self, n=120, **extra):
        return {"type":"assistant", "message":{"model":"synthetic-model", "usage":{"input_tokens":n,"cache_read_input_tokens":40,"cache_creation_input_tokens":10}}, **extra}
    def test_context_includes_cached_input(self):
        self.assertEqual(sc.usage_total(self.entry()), 170)
    def test_sidechain_is_not_parent_context(self):
        self.assertIsNone(sc.usage_total(self.entry(isSidechain=True)))
    def test_synthetic_model_marker_is_ignored(self):
        e=self.entry();e["message"]["model"]="<synthetic>"
        self.assertIsNone(sc.usage_total(e))
    def test_latest_actual_usage_wins(self):
        self.assertEqual(sc.context_tokens([self.entry(10),self.entry(500),{"type":"user"}]),550)
    def test_tail_reader_handles_malformed_and_unrelated_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"synthetic.jsonl"
            p.write_text(json.dumps(self.entry())+'\nnot json\n'+json.dumps({"type":"user","message":{"content":"synthetic"}})+'\n',encoding="utf-8")
            self.assertEqual(sc.last_context_tokens(p),170)
    def test_session_id_cannot_escape_state_directory(self):
        with self.assertRaises(SystemExit):sc.state_dir("../outside")

if __name__ == "__main__":unittest.main()
