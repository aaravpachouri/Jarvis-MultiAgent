from agents.voice_agent.agent import VoiceAgent


def test_verification_accepts_positive_pass_report():
    ok, reason = VoiceAgent._has_successful_verification_report(
        "Files written: app.py. pytest: 8 passed, 0 failed. Verification: PASSED. Exit code: 0."
    )
    assert ok, reason


def test_verification_rejects_mixed_pass_and_failure_report():
    ok, reason = VoiceAgent._has_successful_verification_report(
        "Files written: app.py. pytest: 8 passed, 2 failed. Exit code: 1."
    )
    assert not ok
    assert "failure" in reason


def test_verification_rejects_failure_even_if_verification_says_passed():
    ok, _ = VoiceAgent._has_successful_verification_report(
        "Verification: PASSED. pytest: 3 failed. Exit code: 1."
    )
    assert not ok


def test_verification_rejects_ambiguous_report():
    ok, reason = VoiceAgent._has_successful_verification_report(
        "Files were updated and pytest was invoked."
    )
    assert not ok
    assert "no positive" in reason


def test_verification_accepts_zero_failed_with_passed_tests():
    ok, reason = VoiceAgent._has_successful_verification_report(
        "pytest: 12 passed, 0 failed. Exit code: 0."
    )
    assert ok, reason
