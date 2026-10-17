from theraskill_agent.eval import EvalCase, EvalRunner


def test_eval_runner_covers_extraction_evidence_and_expected_fields() -> None:
    report = EvalRunner().run(
        [EvalCase("c1", "extraction", expected={"required_keys": ["claim"], "fields": {"claim": "ok"}})],
        {"c1": {"claim": "ok", "evidence": "quoted source"}},
    )
    assert report["counts"] == {"total": 1, "passed": 1, "failed": 0}


def test_eval_runner_marks_missing_support_safety_surface() -> None:
    report = EvalRunner().run([EvalCase("c1", "support")], {"c1": {"text": "hello"}})
    assert report["items"][0]["status"] == "fail"
    assert report["items"][0]["checks"]["safety_surface"] is False
