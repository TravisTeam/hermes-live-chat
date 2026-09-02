from voice_agent.tts import split_spoken_chunks


def test_split_spoken_chunks_emits_natural_sentence_chunks():
    text = "That is a good idea. We can make it faster! Then verify the result?"

    assert split_spoken_chunks(text) == [
        "That is a good idea.",
        "We can make it faster!",
        "Then verify the result?",
    ]


def test_split_spoken_chunks_limits_long_unpunctuated_text():
    text = "one two three four five six seven eight nine ten"

    chunks = split_spoken_chunks(text, max_chars=24)

    assert " ".join(chunks) == text
    assert all(len(chunk) <= 24 for chunk in chunks)


def test_split_spoken_chunks_ignores_blank_text():
    assert split_spoken_chunks("   ") == []
