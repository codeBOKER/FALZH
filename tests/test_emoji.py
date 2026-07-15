from app.utils.emoji import count_emojis


def test_count_emojis_no_emojis():
    assert count_emojis("باص من صنعاء الى تعز") == 0


def test_count_emojis_single_emoji():
    assert count_emojis("باص 😊") == 1


def test_count_emojis_two_emojis():
    assert count_emojis("باص 😊😊") == 2


def test_count_emojis_three_emojis():
    assert count_emojis("باص 😊😊😊") == 3


def test_count_emojis_mixed_emojis():
    assert count_emojis("رحلة 🚌🚗") == 2


def test_count_emojis_empty_string():
    assert count_emojis("") == 0


def test_count_emojis_none():
    assert count_emojis(None) == 0


def test_count_emojis_only_emojis():
    assert count_emojis("😊😊😊😊") == 4
