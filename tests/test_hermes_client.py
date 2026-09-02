from voice_agent.hermes_client import _safe_session_name, load_voice_profiles, profile_by_id


def test_default_voice_profiles_are_hermes_profiles():
    profiles = load_voice_profiles()
    assert [p.id for p in profiles] == ["hermes_current"]
    assert profile_by_id("hermes_current").hermes_profile == "default"
    assert profile_by_id("deepseek_current").id == "hermes_current"


def test_session_name_is_safe_and_stable():
    assert _safe_session_name("phone/a b") == "voice-web-phoneab"
