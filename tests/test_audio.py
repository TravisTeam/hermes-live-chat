from pathlib import Path

from voice_agent.audio import convert_to_wav


def test_convert_to_wav_raises_for_bad_audio(tmp_path: Path):
    bad = tmp_path / 'bad.webm'
    bad.write_bytes(b'not audio')
    try:
        convert_to_wav(bad, tmp_path / 'out.wav')
    except RuntimeError as exc:
        assert 'ffmpeg conversion failed' in str(exc)
    else:
        raise AssertionError('expected conversion to fail')
