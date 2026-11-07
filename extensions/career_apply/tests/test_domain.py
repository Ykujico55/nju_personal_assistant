"""Offline regressions for the independent career planning domain."""
from __future__ import annotations

import unittest

from career_apply.domain import (
    ApplicationState,
    DomainError,
    FilterSpec,
    Job,
    ResumeRef,
    application_key,
    boss_job_id,
    build_plan,
    canonical_digest,
    filter_jobs,
    normalize_boss_url,
    reconcile_state,
    state_after_import,
    transition_state,
)

URL = "https://www.zhipin.com/job_detail/job_123-opaque.html"


def job(**overrides: object) -> Job:
    value: dict[str, object] = {
        "url": URL, "title": "Python Backend Engineer", "company": "Example Ltd",
        "recruiter": "招聘负责人甲", "location": "南京·建邺区",
        "salary_text": "15-25K", "description": "Python and PostgreSQL; no invented facts.",
    }
    value.update(overrides)
    return Job.from_mapping(value)


def resume() -> ResumeRef:
    return ResumeRef(filename="resume.pdf", sha256="a" * 64, size_bytes=512)


class BossUrlTests(unittest.TestCase):
    def test_tracking_and_fragment_are_removed_without_changing_identity(self) -> None:
        tracked = URL + "?securityId=tracking&lid=old#detail"
        self.assertEqual(URL, normalize_boss_url(tracked))
        self.assertEqual("job_123-opaque", boss_job_id(tracked))
        explicit_port = URL.replace("www.zhipin.com", "WWW.ZHIPIN.COM:443")
        self.assertEqual(URL, normalize_boss_url(explicit_port))

    def test_only_exact_boss_https_job_pages_are_accepted(self) -> None:
        invalid = (
            "http://www.zhipin.com/job_detail/abc.html",
            "https://www.zhipin.com.evil.test/job_detail/abc.html",
            "https://zhipin.com/job_detail/abc.html",
            "https://user@www.zhipin.com/job_detail/abc.html",
            "https://www.zhipin.com:444/job_detail/abc.html",
            "https://www.zhipin.com/job_detail/.html",
            "https://www.zhipin.com/web/geek/job?jobId=abc",
            "https://www.zhipin.com/job_detail/abc%2fdef.html",
            "https://www.zhipin.com/job_detail/abc.html/extra",
            URL + "\n", URL + "?tracking=%0aevil", " " + URL,
        )
        for value in invalid:
            with self.subTest(url=value), self.assertRaises(DomainError):
                normalize_boss_url(value)

    def test_job_mapping_derives_id_and_preserves_known_facts(self) -> None:
        result = job(url=URL + "?lid=tracking")
        self.assertEqual("job_123-opaque", result.job_id)
        self.assertEqual(URL, result.url)
        self.assertEqual("15-25K", result.as_dict()["salary_text"])
        self.assertEqual(result, Job.from_mapping(result.as_dict()))

    def test_job_mapping_rejects_conflicting_identity_and_missing_facts(self) -> None:
        for overrides in ({"job_id": "another"}, {"title": ""}, {"company": ""},
                          {"recruiter": 123}, {"description": "bad\x00fact"}):
            with self.subTest(overrides=overrides), self.assertRaises(DomainError):
                job(**overrides)


class FilteringTests(unittest.TestCase):
    def test_filters_use_keywords_location_and_exact_company_denylist(self) -> None:
        selected = job()
        excluded = job(url=URL.replace("job_123-opaque", "excluded"), description="Python SALES")
        elsewhere = job(url=URL.replace("job_123-opaque", "elsewhere"), location="北京")
        denied = job(url=URL.replace("job_123-opaque", "denied"), company="Blocked Inc")
        spec = FilterSpec(include_keywords=("python",), exclude_keywords=("sales",),
                          locations=("南京",), company_denylist=("blocked inc",), daily_limit=5)
        self.assertEqual((selected,), filter_jobs((selected, excluded, elsewhere, denied), spec))

    def test_duplicate_tracking_urls_do_not_consume_daily_allowance(self) -> None:
        first = job()
        duplicate = job(url=URL + "?lid=other")
        second = job(url=URL.replace("job_123-opaque", "second"))
        third = job(url=URL.replace("job_123-opaque", "third"))
        self.assertEqual((first, second), filter_jobs(
            (first, duplicate, second, third), FilterSpec(daily_limit=3), already_prepared_today=1,
        ))
        self.assertEqual((), filter_jobs((first,), FilterSpec(daily_limit=1),
                                         already_prepared_today=1))

    def test_invalid_limits_and_consumed_counts_are_rejected(self) -> None:
        for limit in (0, 101, True):
            with self.subTest(limit=limit), self.assertRaises(DomainError):
                FilterSpec(daily_limit=limit)
        for count in (-1, True):
            with self.subTest(count=count), self.assertRaises(DomainError):
                filter_jobs((job(),), FilterSpec(), already_prepared_today=count)


class PlanningTests(unittest.TestCase):
    def test_unknown_platform_greeting_is_not_invented(self) -> None:
        plan = build_plan(job(), "greet")
        self.assertEqual(ApplicationState.NEEDS_USER, plan.state)
        self.assertIn("MESSAGE_REQUIRED", plan.reasons)
        self.assertEqual("", plan.message)

    def test_recruiter_is_required_for_any_outbound_plan(self) -> None:
        plan = build_plan(job(recruiter=""), "greet", message="明确的自我介绍")
        self.assertEqual(ApplicationState.NEEDS_USER, plan.state)
        self.assertIn("RECRUITER_REQUIRED", plan.reasons)

    def test_explicit_message_is_preserved_and_plan_contains_no_send_method(self) -> None:
        text = "  你好，我希望了解此岗位。\n这是本人确认的文字。  "
        plan = build_plan(job(), "message", message=text)
        self.assertEqual(ApplicationState.PREPARED, plan.state)
        self.assertEqual(text, plan.message)
        self.assertEqual(text, plan.as_dict()["message"])
        self.assertFalse(hasattr(plan, "send"))

    def test_resume_plan_requires_both_explicit_text_and_metadata(self) -> None:
        incomplete = build_plan(job(), "send_resume")
        self.assertEqual(ApplicationState.NEEDS_USER, incomplete.state)
        self.assertEqual({"MESSAGE_REQUIRED", "RESUME_REQUIRED"}, set(incomplete.reasons))
        prepared = build_plan(job(), "send_resume", message="请查收本人简历。", resume=resume())
        self.assertEqual(ApplicationState.PREPARED, prepared.state)
        self.assertEqual("a" * 64, prepared.as_dict()["resume"]["sha256"])

    def test_changed_material_changes_command_digest_but_not_application_identity(self) -> None:
        first = build_plan(job(), "greet", message="第一版")
        replay = build_plan(job(url=URL + "?lid=other"), "greet", message="第一版")
        edited = build_plan(job(), "greet", message="第二版")
        changed_hr = build_plan(job(recruiter="招聘负责人乙"), "greet", message="第一版")
        self.assertEqual(first.command_digest, replay.command_digest)
        self.assertNotEqual(first.command_digest, edited.command_digest)
        self.assertNotEqual(first.command_digest, changed_hr.command_digest)
        self.assertEqual(first.dedupe_key, edited.dedupe_key)
        self.assertEqual(application_key(first.job), application_key(edited.job))

    def test_resume_metadata_is_bounded_and_has_safe_filename_and_hash(self) -> None:
        invalid = (
            {"filename": "../resume.pdf", "sha256": "a" * 64, "size_bytes": 1},
            {"filename": "resume.pdf\r\nX: injected", "sha256": "a" * 64, "size_bytes": 1},
            {"filename": "resume.pdf", "sha256": "not-a-hash", "size_bytes": 1},
            {"filename": "resume.pdf", "sha256": "a" * 64, "size_bytes": True},
            {"filename": "resume.pdf", "sha256": "a" * 64, "size_bytes": 0},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(DomainError):
                ResumeRef(**value)

    def test_unknown_action_and_invalid_message_are_rejected(self) -> None:
        for action, message in (("apply_everywhere", "text"), ("greet", "bad\x00text")):
            with self.subTest(action=action), self.assertRaises(DomainError):
                build_plan(job(), action, message=message)

    def test_canonical_digest_is_order_independent_and_rejects_nan(self) -> None:
        self.assertEqual(canonical_digest({"b": 2, "a": "职位"}),
                         canonical_digest({"a": "职位", "b": 2}))
        with self.assertRaises(DomainError):
            canonical_digest({"a": float("nan")})


class ApplicationStateTests(unittest.TestCase):
    def test_unknown_cannot_be_reset_or_retried_by_ordinary_transition(self) -> None:
        for target in (ApplicationState.PREPARED, ApplicationState.IMPORTED,
                       ApplicationState.FAILED, ApplicationState.RESUME_SENT):
            with self.subTest(target=target), self.assertRaises(DomainError):
                transition_state(ApplicationState.UNKNOWN, target)
        self.assertEqual(ApplicationState.UNKNOWN,
                         transition_state(ApplicationState.UNKNOWN, ApplicationState.UNKNOWN))

    def test_import_preserves_existing_outcomes_including_unknown(self) -> None:
        for state in ApplicationState:
            self.assertEqual(state, state_after_import(state))
        self.assertEqual(ApplicationState.IMPORTED, state_after_import(None))

    def test_execution_and_reply_states_are_distinct(self) -> None:
        state = transition_state(ApplicationState.PREPARED, ApplicationState.EXECUTING)
        state = transition_state(state, ApplicationState.GREETING_SENT)
        self.assertEqual(ApplicationState.WAITING_REPLY,
                         transition_state(state, ApplicationState.WAITING_REPLY))
        with self.assertRaises(DomainError):
            transition_state(ApplicationState.RESUME_SENT, ApplicationState.IMPORTED)

    def test_unknown_requires_explicit_evidence_to_reconcile(self) -> None:
        with self.assertRaises(DomainError):
            reconcile_state(ApplicationState.UNKNOWN, ApplicationState.RESUME_SENT,
                            evidence_reference="")
        self.assertEqual(ApplicationState.RESUME_SENT,
                         reconcile_state(ApplicationState.UNKNOWN, ApplicationState.RESUME_SENT,
                                         evidence_reference="manual-check:receipt-1"))
        with self.assertRaises(DomainError):
            reconcile_state(ApplicationState.PREPARED, ApplicationState.RESUME_SENT,
                            evidence_reference="manual-check:receipt-1")


if __name__ == "__main__":
    unittest.main()
