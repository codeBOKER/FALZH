from app.utils.phone import count_digits, strip_phone_numbers


class TestCountDigits:
    def test_no_digits(self):
        assert count_digits("hello world") == 0

    def test_all_digits(self):
        assert count_digits("1234567") == 7

    def test_mixed(self):
        assert count_digits("abc123def456") == 6

    def test_empty(self):
        assert count_digits("") == 0


class TestStripPhoneNumbers:
    def test_yemeni_number_with_plus(self):
        result = strip_phone_numbers("اتصل على +967712345678")
        assert result == "اتصل على [رقم الهاتف]"

    def test_yemeni_number_with_00(self):
        result = strip_phone_numbers("اتصل على 00967712345678")
        assert result == "اتصل على [رقم الهاتف]"

    def test_international_with_dashes(self):
        result = strip_phone_numbers("Call +1-234-567-8901")
        assert result == "Call [رقم الهاتف]"

    def test_international_with_spaces(self):
        result = strip_phone_numbers("Call +44 20 7946 0958")
        assert result == "Call [رقم الهاتف]"

    def test_international_with_dots(self):
        result = strip_phone_numbers("Call +49.30.1234567")
        assert result == "Call [رقم الهاتف]"

    def test_international_with_parentheses(self):
        result = strip_phone_numbers("Call +1 (234) 567-8901")
        assert result == "Call [رقم الهاتف]"

    def test_multiple_numbers(self):
        result = strip_phone_numbers("Call +967712345678 or +967776543210")
        assert result == "Call [رقم الهاتف] or [رقم الهاتف]"

    def test_no_phone_numbers(self):
        text = "رحلة من صنعاء إلى تعز"
        result = strip_phone_numbers(text)
        assert result == text

    def test_short_number_not_matched(self):
        result = strip_phone_numbers("Room 123")
        assert result == "Room 123"

    def test_empty_string(self):
        assert strip_phone_numbers("") == ""

    def test_none_returns_none(self):
        assert strip_phone_numbers(None) is None

    def test_custom_replacement(self):
        result = strip_phone_numbers("Call +967712345678", replacement="***")
        assert result == "Call ***"

    def test_number_at_start(self):
        result = strip_phone_numbers("+967712345678 اتصل")
        assert result == "[رقم الهاتف] اتصل"

    def test_number_at_end(self):
        result = strip_phone_numbers("اتصل +967712345678")
        assert result == "اتصل [رقم الهاتف]"

    def test_number_in_middle_of_text(self):
        result = strip_phone_numbers("اتصل على +967712345678 للحجز")
        assert result == "اتصل على [رقم الهاتف] للحجز"

    def test_yemeni_local_format(self):
        result = strip_phone_numbers("0712345678")
        assert result == "[رقم الهاتف]"

    def test_number_with_country_code_no_plus(self):
        result = strip_phone_numbers("967712345678")
        assert result == "[رقم الهاتف]"

    def test_long_number_with_separators(self):
        result = strip_phone_numbers("+1 (555) 123-4567 ext 890")
        assert result == "[رقم الهاتف] ext 890"

    def test_local_yemeni_9_digit(self):
        result = strip_phone_numbers("770026665")
        assert result == "[رقم الهاتف]"

    def test_local_yemeni_in_message(self):
        result = strip_phone_numbers("للتواصل على الرقم 770026665")
        assert result == "للتواصل على الرقم [رقم الهاتف]"

    def test_local_yemeni_with_dash_prefix(self):
        result = strip_phone_numbers("الرقم:-770026665")
        assert result == "الرقم:-[رقم الهاتف]"
