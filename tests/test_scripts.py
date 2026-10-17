"""Text in the newer languages: Cyrillic, Polish, Czech, Dutch and Turkish words, and Chinese, Japanese and
Korean, written without spaces between words."""

from zenith_pdf.core import finder, writing


def test_chinese_japanese_and_korean_are_read_in_pairs_of_characters():
    assert finder.tokens("旋转页面 Rotate") == ["旋转", "转页", "页面", "rotate"]
    assert finder.tokens("회전") == ["회전"]  # a Korean syllable stays whole, never taken apart into letters
    assert writing.words("页面") == ["页面"]
    index = finder.Finder([finder.Entry(key="rotate", kind="command", title="旋转页面"),
                           finder.Entry(key="save", kind="command", title="保存文件")])
    assert [entry.key for entry, _ in index.search("旋转")] == ["rotate"]
    assert [entry.key for entry, _ in index.search("页面旋转")] == ["rotate"]  # the words in another order


def test_each_language_is_told_apart():
    samples = {
        "ru": "Это документ, который мы хотим прочитать, и он очень интересный.",
        "uk": "Це документ, який ми хочемо прочитати, і він дуже цікавий.",
        "pl": "To jest dokument, który chcemy przeczytać i jest bardzo ciekawy.",
        "cs": "Toto je dokument, který chceme číst, a je to velmi zajímavé.",
        "nl": "Dit is een document dat we willen lezen en het is erg mooi.",
        "tr": "Bu bir belge ve çok güzel bir şey, ama biraz uzun.",
        "zh": "这是一个测试文档。我们来看看。",
        "ja": "これはテストです。ページを回転します。",
        "ko": "이것은 테스트입니다. 페이지를 회전합니다.",
    }
    for code, text in samples.items():
        assert writing.detect_language(text) == code, code


def test_sentences_end_with_full_width_marks_and_before_any_capital():
    assert writing.sentences("第一句话。第二句话！第三句话？") == ["第一句话。", "第二句话！", "第三句话？"]
    assert writing.sentences("Это первое. Это второе. И третье.") == ["Это первое.", "Это второе.", "И третье."]
    assert writing.sentences("Łódź jest duża. Żyje tam wielu ludzi.") == ["Łódź jest duża.", "Żyje tam wielu ludzi."]
    assert writing.sentences("See e.g. this one. Next one here.") == ["See e.g. this one.", "Next one here."]
    # A line break inside Chinese runs on with no space; Korean keeps its space between words.
    assert writing.clean_pdf_text("这是第一\n行的文字") == "这是第一行的文字"
    assert writing.clean_pdf_text("한국어\n문장") == "한국어 문장"


def test_key_points_of_chinese_text():
    text = ("机器学习是人工智能的一个分支。机器学习使用数据来训练模型。今天天气很好。"
            "模型可以从数据中学习规律。我们喜欢吃苹果。")
    assert writing.key_sentences(text, 2) == ["机器学习是人工智能的一个分支。", "机器学习使用数据来训练模型。"]


def test_tidy_leaves_chinese_punctuation_alone_and_knows_the_turkish_capital_i():
    assert [fix.reason for fix in writing.tidy("你好,世界 。", "zh")] == []
    fixes = writing.tidy("bu güzel. istanbul büyük bir şehir ve çok kalabalık.", "tr")
    assert [fix.replacement for fix in fixes if fix.reason == "Capital letter"] == ["İ"]
