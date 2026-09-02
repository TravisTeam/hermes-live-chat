from voice_agent.config import Settings


def test_default_settings_are_local():
    s = Settings()
    assert s.llm_base_url.endswith('/v1')
    assert s.kokoro_url.startswith('http://127.0.0.1')
    assert s.stt_engine == 'canary'
    assert s.canary_model.endswith('canary-180m-flash-Q8_0.gguf')
