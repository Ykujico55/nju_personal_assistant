from __future__ import annotations

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from career_apply.domain import Job
from career_apply.planning import boss_plan, mail_plans, read_resume


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.resume = self.root / "resume.pdf"
        self.resume.write_bytes(b"%PDF-1.4\nsynthetic resume fixture\n%%EOF")

    def test_resume_is_bounded_hashed_metadata_without_absolute_path(self):
        resume = read_resume(self.resume)
        self.assertEqual(hashlib.sha256(self.resume.read_bytes()).hexdigest(), resume.sha256)
        self.assertEqual("resume.pdf", resume.filename)
        self.assertNotIn(str(self.root), str(resume.as_dict()))

    def test_invalid_resume_and_remote_paths_are_rejected(self):
        bad = self.root / "bad.pdf"
        bad.write_bytes(b"this is not a pdf")
        for path in (bad, Path("https://example.test/resume.pdf")):
            with self.subTest(path=path), self.assertRaises(ValueError):
                read_resume(path)

    def test_boss_plan_never_exposes_an_execution_path(self):
        job = Job.from_mapping({"url": "https://www.zhipin.com/job_detail/fixture1.html",
                                "title": "Python", "company": "Fixture", "recruiter": "HR"})
        plan = boss_plan(job, "message", message="公开自检文字")
        self.assertFalse(plan["execution_available"])
        self.assertEqual("PREPARED", plan["state"])
        self.assertEqual("boss", plan["platform"])

    def test_mail_list_templates_are_exact_and_individually_bound(self):
        plans = mail_plans([
            {"email": "a@example.test", "name": "甲", "position": "开发"},
            {"email": "b@example.test", "name": "乙", "position": "测试"},
        ], "应聘${position}", "您好${name}，这是合成自检。", read_resume(self.resume))
        self.assertEqual(2, len(plans))
        self.assertEqual("应聘开发", plans[0]["subject"])
        self.assertEqual("您好乙，这是合成自检。", plans[1]["body"])
        self.assertNotEqual(plans[0]["dedupe_key"], plans[1]["dedupe_key"])
        self.assertTrue(all(not plan["execution_available"] for plan in plans))

    def test_duplicate_recipients_make_one_plan_and_no_resume_is_explicit(self):
        plans = mail_plans([{"email": "a@example.test"}, {"email": "A@EXAMPLE.TEST"}],
                           "Synthetic", "Fixture", None)
        self.assertEqual(1, len(plans))
        self.assertEqual("NEEDS_USER", plans[0]["state"])
        self.assertIn("RESUME_REQUIRED", plans[0]["reasons"])

    def test_bad_headers_unknown_templates_and_multiple_targets_are_rejected(self):
        for recipient, subject, body in (
            ({"email": "a@example.test"}, "hello\r\nBcc: x@example.test", "body"),
            ({"email": "a@example.test"}, "${private_field}", "body"),
            ({"email": "a@example.test,b@example.test"}, "hi", "body"),
            ({"email": "a@example.test"}, "hi", ""),
        ):
            with self.subTest(subject=subject), self.assertRaises(ValueError):
                mail_plans([recipient], subject, body, None)

    def test_text_and_docx_resume_metadata_use_the_actual_file(self):
        text = self.root / "resume.txt"
        text.write_text("合成自检简历", encoding="utf-8")
        self.assertEqual("text/plain", read_resume(text).media_type)
        docx = self.root / "resume.docx"
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<document/>")
        self.assertEqual("application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                         read_resume(docx).media_type)
        broken = self.root / "broken.docx"
        with zipfile.ZipFile(broken, "w") as archive:
            archive.writestr("unrelated.txt", "fixture")
        with self.assertRaises(ValueError):
            read_resume(broken)

    def test_oversized_and_binary_text_resume_are_rejected(self):
        binary = self.root / "binary.txt"
        binary.write_bytes(b"binary\x00text")
        with self.assertRaises(ValueError):
            read_resume(binary)
        oversized = self.root / "large.pdf"
        with oversized.open("wb") as output:
            output.write(b"%PDF-1.4")
            output.seek(8 * 1024 * 1024)
            output.write(b"x")
        with self.assertRaises(ValueError):
            read_resume(oversized)

    def test_mail_template_cannot_access_python_attributes(self):
        with self.assertRaises(ValueError):
            mail_plans([{"email": "a@example.test", "name": "Fixture"}],
                       "${name.__class__}", "body", None)
