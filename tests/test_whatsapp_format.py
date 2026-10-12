from app.utils.whatsapp_format import to_whatsapp_markup


def test_bold_double_star_becomes_single():
    assert to_whatsapp_markup("**رحلتك** مؤكدة") == "*رحلتك* مؤكدة"


def test_multiple_bold_segments():
    assert to_whatsapp_markup("**من** عدن **إلى** تعز") == "*من* عدن *إلى* تعز"


def test_bold_across_newlines():
    assert to_whatsapp_markup("**السعر\n100**") == "*السعر\n100*"


def test_heading_markers_removed():
    assert to_whatsapp_markup("### رحلتك") == "رحلتك"
    assert to_whatsapp_markup("## رحلتك") == "رحلتك"
    assert to_whatsapp_markup("# رحلتك") == "رحلتك"
    assert to_whatsapp_markup("###### رحلتك") == "رحلتك"


def test_heading_and_bold_combined():
    assert to_whatsapp_markup("### **رحلتك**") == "*رحلتك*"


def test_hashtag_inside_line_preserved():
    assert to_whatsapp_markup("السعر #1") == "السعر #1"


def test_plain_text_unchanged():
    assert to_whatsapp_markup("رحلة من عدن إلى تعز") == "رحلة من عدن إلى تعز"


def test_empty_and_none():
    assert to_whatsapp_markup("") == ""
    assert to_whatsapp_markup(None) is None
