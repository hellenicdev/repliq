from app.utils.text import normalize_text, tokenize


def test_normalize_lowercase_and_punctuation():
    assert normalize_text("Hello, WORLD! It's a test.") == "hello world its a test"


def test_normalize_apostrophes_dropped():
    assert normalize_text("don't you're") == "dont youre"


def test_tokenize():
    assert tokenize("I don't know what you're talking about") == [
        "i", "dont", "know", "what", "youre", "talking", "about",
    ]


def test_tokenize_empty():
    assert tokenize("  ???  ") == []