from asr_cli.cli import (
    infer_whisper_processor_repo,
    is_official_whisper_model,
    official_whisper_runtime_model,
    normalize_model,
)


def test_official_whisper_repo_name_stays_visible_and_maps_at_runtime() -> None:
    expected = "openai/whisper-large-v3-turbo"

    assert normalize_model(expected) == expected
    assert is_official_whisper_model(expected)
    assert official_whisper_runtime_model(expected) == "turbo"


def test_existing_aliases_still_resolve() -> None:
    assert normalize_model("qwen") == "mlx-community/Qwen3-ASR-0.6B-4bit"
    assert normalize_model("glm") == "mlx-community/GLM-ASR-Nano-2512-4bit"


def test_whisper_processor_repo_is_inferred_for_mlx_model() -> None:
    assert (
        infer_whisper_processor_repo("mlx-community/whisper-large-v3-turbo")
        == "openai/whisper-large-v3-turbo"
    )
