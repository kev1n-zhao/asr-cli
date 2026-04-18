from asr_cli.cli import build_srt_cues_from_units


def test_sentence_boundary_flushes_before_length_limit() -> None:
    units = [
        {"start": 0.0, "end": 0.2, "text": "今天", "sentence_end": False},
        {"start": 0.2, "end": 0.4, "text": "天气", "sentence_end": False},
        {"start": 0.4, "end": 0.6, "text": "不错。", "sentence_end": True},
        {"start": 0.6, "end": 0.8, "text": "我们", "sentence_end": False},
        {"start": 0.8, "end": 1.0, "text": "出发", "sentence_end": False},
    ]

    cues = build_srt_cues_from_units(units, max_chars_per_line=12)

    assert [cue["text"] for cue in cues] == ["今天天气不错。", "我们出发"]


def test_length_limit_still_wins_over_sentence_boundary() -> None:
    units = [
        {"start": 0.0, "end": 0.2, "text": "这是", "sentence_end": False},
        {"start": 0.2, "end": 0.4, "text": "一个", "sentence_end": False},
        {"start": 0.4, "end": 0.6, "text": "很长", "sentence_end": False},
        {"start": 0.6, "end": 0.8, "text": "的句子", "sentence_end": False},
        {"start": 0.8, "end": 1.0, "text": "终于结束。", "sentence_end": True},
    ]

    cues = build_srt_cues_from_units(units, max_chars_per_line=8)

    assert [cue["text"] for cue in cues] == ["这是一个很长", "的句子终于结束。"]


def test_punctuation_heuristic_is_used_without_sentence_metadata() -> None:
    units = [
        {"start": 0.0, "end": 0.2, "text": "Hello", "sentence_end": False},
        {"start": 0.2, "end": 0.4, "text": "world.", "sentence_end": False},
        {"start": 0.4, "end": 0.6, "text": "Next", "sentence_end": False},
        {"start": 0.6, "end": 0.8, "text": "line", "sentence_end": False},
    ]

    cues = build_srt_cues_from_units(units, max_chars_per_line=20)

    assert [cue["text"] for cue in cues] == ["Hello world.", "Next line"]


def test_cjk_tokens_are_not_joined_with_ascii_spacing_rules() -> None:
    units = [
        {"start": 0.0, "end": 0.2, "text": "今天", "sentence_end": False},
        {"start": 0.2, "end": 0.4, "text": "天气", "sentence_end": False},
        {"start": 0.4, "end": 0.6, "text": "不错。", "sentence_end": True},
    ]

    cues = build_srt_cues_from_units(units, max_chars_per_line=20)

    assert [cue["text"] for cue in cues] == ["今天天气不错。"]
