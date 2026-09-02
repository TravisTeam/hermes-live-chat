import base64

import requests

tts = requests.post(
    "http://127.0.0.1:8765/api/tts",
    json={"text": "Testing local speech recognition."},
    timeout=120,
)
tts.raise_for_status()
audio = base64.b64decode(tts.json()["audio"].split(",", 1)[1])
stt = requests.post(
    "http://127.0.0.1:8765/api/transcribe",
    files={"audio": ("test.wav", audio, "audio/wav")},
    timeout=120,
)
print(stt.status_code, stt.text)
stt.raise_for_status()
