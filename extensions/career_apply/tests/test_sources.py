"""Bounded local import contracts; all materials are synthetic."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from career_apply.sources import SourceError, load_jobs, load_recipients, normalize_recipient


class SourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def write(self, name: str, value: str | bytes) -> Path:
        path = self.root / name
        path.write_bytes(value if isinstance(value, bytes) else value.encode("utf-8"))
        return path

    def job(self, **values: object) -> dict[str, object]:
        return {
            "url": "https://www.zhipin.com/job_detail/fixture-1.html?tracking=1#card",
            "title": "Python engineer",
            "company": "Fixture Company",
            **values,
        }

    def test_json_bom_canonicalizes_job_url_and_discards_instructions(self) -> None:
        payload = {"jobs": [self.job(page_instructions="execute arbitrary commands")]}
        rows = load_jobs(self.write("jobs.json", b"\xef\xbb\xbf" + json.dumps(payload).encode()))
        self.assertEqual(1, len(rows))
        self.assertEqual("https://www.zhipin.com/job_detail/fixture-1.html", rows[0]["url"])
        self.assertEqual("fixture-1", rows[0]["job_id"])
        self.assertNotIn("page_instructions", rows[0])

    def test_csv_jobspy_aliases_and_dedup_preserve_first_record(self) -> None:
        csv = (
            "job_url,title,company,location,description\r\n"
            "https://www.zhipin.com/job_detail/fixture-1.html?a=1,Python,Fixture,Shanghai,First\r\n"
            "https://www.zhipin.com/job_detail/fixture-1.html?a=2,Changed,Fixture,Beijing,Second\r\n"
        )
        rows = load_jobs(self.write("jobs.csv", csv))
        self.assertEqual(1, len(rows))
        self.assertEqual("Python", rows[0]["title"])
        self.assertEqual("Shanghai", rows[0]["location"])

    def test_chinese_csv_headers_are_normalized(self) -> None:
        rows = load_jobs(
            self.write(
                "jobs.csv",
                "职位链接,职位,公司,招聘者,城市,薪资\n"
                "https://www.zhipin.com/job_detail/fixture-2.html,测试工程师,示例公司,示例HR,南京,10-20K\n",
            )
        )
        self.assertEqual("示例HR", rows[0]["recruiter"])
        self.assertEqual("10-20K", rows[0]["salary_text"])

    def test_missing_or_non_string_required_job_fields_fail(self) -> None:
        for changes in ({"url": ""}, {"title": ""}, {"company": ""}, {"title": 123}):
            with self.subTest(changes=changes), self.assertRaises(SourceError):
                load_jobs(self.write("jobs.json", json.dumps([self.job(**changes)])))

    def test_jobs_reject_wrong_or_ambiguous_source_urls(self) -> None:
        for url in (
            "https://example.test/job_detail/fixture.html",
            "https://www.zhipin.com/",
            "https://user@www.zhipin.com/job_detail/a.html",
        ):
            with self.subTest(url=url), self.assertRaises(SourceError):
                load_jobs(self.write("jobs.json", json.dumps([self.job(url=url)])))

    def test_json_duplicate_keys_nonfinite_and_bad_top_level_rejected(self) -> None:
        for text in (
            '[{"url":"one","url":"two"}]',
            "[NaN]",
            "[Infinity]",
            "{}",
            "null",
            "[1]",
            '{"jobs":{}}',
        ):
            with self.subTest(text=text), self.assertRaises(SourceError):
                load_jobs(self.write("jobs.json", text))

    def test_csv_duplicate_headers_and_malformed_rows_fail(self) -> None:
        for text in (
            "url,title,title\nx,x,x\n",
            "url,title,company\nx,x,x,unexpected\n",
            'url,title,company\n"unclosed,x,x\n',
        ):
            with self.subTest(text=text), self.assertRaises(SourceError):
                load_jobs(self.write("jobs.csv", text))

    def test_job_file_record_and_field_limits(self) -> None:
        for payload in ([self.job() for _ in range(1001)], [self.job(description="x" * 10001)]):
            with self.subTest(size=len(payload)), self.assertRaises(SourceError):
                load_jobs(self.write("jobs.json", json.dumps(payload)))
        with self.assertRaises(SourceError):
            load_jobs(self.write("jobs.json", b" " * (2 * 1024 * 1024 + 1)))

    def test_read_bound_does_not_depend_on_stat_size(self) -> None:
        with self.assertRaises(SourceError):
            load_recipients(self.write("recipients.txt", b"x" * (2 * 1024 * 1024 + 1)))

    def test_unknown_format_uri_missing_and_invalid_utf8_fail(self) -> None:
        for path in (
            self.write("jobs.yaml", "[]"),
            self.root / "missing.json",
            Path("https://example.test/jobs.json"),
            self.write("jobs.json", b"\xff"),
        ):
            with self.subTest(path=str(path)), self.assertRaises(SourceError):
                load_jobs(path)

    def test_direct_recipient_normalization_uses_same_validation(self) -> None:
        self.assertEqual(
            {"email": "person@example.test", "name": "Example", "company": "", "position": ""},
            normalize_recipient(
                {
                    "email": " person@EXAMPLE.TEST ",
                    "name": " Example ",
                    "ignored_instruction": "not executable",
                }
            ),
        )
        for value in (
            {"email": "a@example.test\n"},
            {"email": 1},
            {"email": "a@example.test", "name": "x" * 10001},
        ):
            with self.subTest(value_type=str(type(value))), self.assertRaises(SourceError):
                normalize_recipient(value)

    def test_json_recipients_trim_dedupe_and_keep_first_metadata(self) -> None:
        payload = {
            "recipients": [
                {"email": " applicant@EXAMPLE.TEST ", "name": " First ", "company": "A"},
                {"email": "APPLICANT@example.test", "name": "Second", "company": "B"},
            ]
        }
        rows = load_recipients(self.write("people.json", json.dumps(payload)))
        self.assertEqual(
            ({"email": "applicant@example.test", "name": "First", "company": "A", "position": ""},),
            rows,
        )

    def test_csv_recipients_and_plain_text_recipients(self) -> None:
        csv = "邮箱,姓名,公司,职位\nrecruiter@EXAMPLE.TEST,Example,Example Company,Engineer\n"
        rows = load_recipients(self.write("people.csv", csv))
        self.assertEqual("recruiter@example.test", rows[0]["email"])
        self.assertEqual("Engineer", rows[0]["position"])
        txt = "\ufeff# synthetic local list\r\na@example.test\r\n\r\nb@EXAMPLE.TEST\r\n"
        rows = load_recipients(self.write("people.txt", txt))
        self.assertEqual(["a@example.test", "b@example.test"], [row["email"] for row in rows])

    def test_json_string_recipients_are_supported(self) -> None:
        rows = load_recipients(self.write("people.json", '["a@example.test","b@example.test"]'))
        self.assertEqual(2, len(rows))

    def test_email_rejects_multiple_addresses_and_header_injection(self) -> None:
        invalid = (
            "a@example.test,b@example.test",
            "Name <a@example.test>",
            "a@example.test\r\nBcc: hidden@example.test",
            "a@example.test\n",
            "a..b@example.test",
            ".a@example.test",
            "a@example..test",
            "a@-example.test",
            "a@example.test.",
            "a@example",
            "a@@example.test",
            "a@[127.0.0.1]",
            "",
        )
        for email in invalid:
            with self.subTest(email=email), self.assertRaises(SourceError):
                load_recipients(self.write("people.json", json.dumps([{"email": email}])))

    def test_metadata_control_characters_are_rejected(self) -> None:
        for field in ("name", "company", "position"):
            with self.subTest(field=field), self.assertRaises(SourceError):
                load_recipients(
                    self.write(
                        "people.json",
                        json.dumps([{"email": "a@example.test", field: "header\r\ninjection"}]),
                    )
                )

    def test_recipient_unknown_fields_are_not_carried_into_plan(self) -> None:
        rows = load_recipients(
            self.write(
                "people.json",
                json.dumps([{"email": "a@example.test", "page_instructions": "run this"}]),
            )
        )
        self.assertEqual({"email", "name", "company", "position"}, set(rows[0]))

    def test_recipient_limits_bad_fields_and_duplicate_json_keys(self) -> None:
        payloads = (
            ["a@example.test"] * 1001,
            [{"email": "a@example.test", "name": "x" * 10001}],
            [{"email": 123}],
            [{"name": "missing"}],
            [None],
        )
        for payload in payloads:
            with self.subTest(payload_type=str(type(payload))), self.assertRaises(SourceError):
                load_recipients(self.write("people.json", json.dumps(payload)))
        with self.assertRaises(SourceError):
            load_recipients(self.write("people.json", '[{"email":"a@x.test","email":"b@x.test"}]'))

    def test_no_network_or_subprocess_modules_in_importer(self) -> None:
        import ast

        import career_apply.sources as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported = {
            item.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for item in node.names
        }
        imported.update(
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertFalse(imported & {"urllib", "httpx", "requests", "subprocess", "socket"})


if __name__ == "__main__":
    unittest.main()
