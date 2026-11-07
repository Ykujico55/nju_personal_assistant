from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.db = self.root / "local.sqlite3"
        self.jobs = self.root / "jobs.json"
        self.jobs.write_text(json.dumps([{
            "url": "https://www.zhipin.com/job_detail/fixture1.html?tracking=1",
            "title": "Python开发", "company": "Fixture", "recruiter": "测试HR",
            "location": "南京",
        }]), encoding="utf-8")
        self.resume = self.root / "resume.pdf"
        self.resume.write_bytes(b"%PDF-1.4\nSynthetic resume fixture\n%%EOF")

    def invoke(self, *args, expected=0):
        result = subprocess.run(
            [sys.executable, "-Xutf8", "-B", str(ROOT / "run.py"),
             "--database", str(self.db), *map(str, args)],
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_import_filter_and_versioned_boss_plan_are_persistent(self):
        self.assertEqual(1, self.invoke("jobs", "import", self.jobs)["imported"])
        selected = self.invoke("jobs", "list", "--include", "Python", "--city", "南京")
        self.assertEqual(1, len(selected["items"]))
        first = self.invoke("plan", "boss", "fixture1", "--action", "message",
                            "--message", "合成自检")
        self.assertEqual(1, first["version"])
        self.assertFalse(first["plan"]["execution_available"])
        self.assertEqual(first, self.invoke("plans", "show", first["id"]))
        self.invoke("jobs", "import", self.jobs)
        self.assertEqual(first, self.invoke("plans", "show", first["id"]))
        conflict = self.invoke("plan", "boss", "fixture1", "--action", "message",
                               "--message", "更新", expected=2)
        self.assertEqual("PLAN_CONFLICT", conflict["error"]["code"])
        updated = self.invoke("plan", "boss", "fixture1", "--action", "message",
                              "--message", "更新", "--expected-version", "1")
        self.assertEqual(2, updated["version"])
        history = self.invoke("plans", "show", first["id"], "--history")["history"]
        self.assertEqual([1, 2], [item["version"] for item in history])
        self.assertEqual("合成自检", history[0]["plan"]["message"])

    def test_local_recipient_list_builds_individual_mail_plans(self):
        recipients = self.root / "recipients.csv"
        recipients.write_text("email,name,position\na@example.test,甲,开发\n"
                              "b@example.test,乙,测试\n", encoding="utf-8")
        self.assertEqual(2, self.invoke("recipients", "import", recipients)["imported"])
        self.assertEqual(2, len(self.invoke("recipients", "list")["items"]))
        result = self.invoke("plan", "email", "--subject", "应聘${position}",
                             "--body", "您好${name}，这是自检。", "--resume", self.resume)
        self.assertEqual(2, len(result["items"]))
        self.assertEqual("应聘开发", result["items"][0]["plan"]["subject"])
        self.assertTrue(all(not x["plan"]["execution_available"] for x in result["items"]))

    def test_export_does_not_overwrite_an_existing_file(self):
        destination = self.root / "export.json"
        self.invoke("plans", "export", destination)
        original = destination.read_bytes()
        self.invoke("plans", "export", destination, expected=2)
        self.assertEqual(original, destination.read_bytes())
        self.assertEqual([], json.loads(original)["items"])

    def test_bad_command_does_not_insert_a_plan(self):
        self.invoke("plan", "boss", "missing", "--message", "test", expected=2)
        self.assertEqual([], self.invoke("plans", "list")["items"])

    def test_help_has_no_database_side_effect(self):
        result = subprocess.run([sys.executable, str(ROOT / "run.py"),
                                 "--database", str(self.db), "--help"],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(self.db.exists())

    def test_invalid_local_database_has_a_structured_error(self):
        self.db.write_bytes(b"invalid SQLite fixture")
        result = self.invoke("plans", "list", expected=2)
        self.assertEqual("DATABASE_ERROR", result["error"]["code"])
