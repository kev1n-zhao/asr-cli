from __future__ import annotations

import argparse
import json
import shutil
import re
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import Any


MODEL_ALIASES = {
    "glm": "mlx-community/GLM-ASR-Nano-2512-4bit",
    "zai-org/glm-asr-nano-2512": "mlx-community/GLM-ASR-Nano-2512-4bit",
    "zai-org/GLM-ASR-Nano-2512": "mlx-community/GLM-ASR-Nano-2512-4bit",
    "aligner": "mlx-community/Qwen3-ForcedAligner-0.6B-8bit",
    "qwen/qwen3-forcedaligner-0.6b": "mlx-community/Qwen3-ForcedAligner-0.6B-8bit",
    "Qwen/Qwen3-ForcedAligner-0.6B": "mlx-community/Qwen3-ForcedAligner-0.6B-8bit",
    "qwen": "mlx-community/Qwen3-ASR-0.6B-4bit",
    "qwen/qwen3-asr-0.6b": "mlx-community/Qwen3-ASR-0.6B-4bit",
    "Qwen/Qwen3-ASR-0.6B": "mlx-community/Qwen3-ASR-0.6B-4bit",
}

FORMAT_CHOICES = ("txt", "json", "srt", "vtt")
FFMPEG_INPUT_SUFFIXES = {
    ".aac",
    ".aiff",
    ".avi",
    ".m4a",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".ogg",
    ".opus",
    ".webm",
    ".wma",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asr-cli",
        description=(
            "ASR on Apple Silicon via MLX. Supports GLM-ASR and Qwen3-ASR "
            "through mlx-audio-compatible checkpoints."
        ),
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    transcribe_parser = subparsers.add_parser("transcribe", help="Transcribe audio or video")
    transcribe_parser.add_argument(
        "audio", nargs="+", help="Audio or video file(s) to transcribe"
    )
    transcribe_parser.add_argument(
        "--model",
        default="Qwen/Qwen3-ASR-0.6B",
        help=(
            "Model alias, official HF repo, MLX-community repo, or local path. "
            "Examples: Qwen/Qwen3-ASR-0.6B, zai-org/GLM-ASR-Nano-2512, "
            "mlx-community/Qwen3-ASR-0.6B-4bit."
        ),
    )
    transcribe_parser.add_argument(
        "--resolved-model",
        action="store_true",
        help="Print the MLX model repo/path that will actually be loaded and exit",
    )
    transcribe_parser.add_argument(
        "--list-models",
        action="store_true",
        help="List built-in model aliases and exit",
    )
    transcribe_parser.add_argument(
        "--output",
        "-o",
        default=".",
        help=(
            "Output directory or filename prefix. "
            "If this is a directory or ends with '/', each input uses its stem."
        ),
    )
    transcribe_parser.add_argument(
        "--format",
        "-f",
        default="txt",
        choices=FORMAT_CHOICES,
        help="Output format",
    )
    transcribe_parser.add_argument(
        "--language", default=None, help="Language hint, e.g. en or zh"
    )
    transcribe_parser.add_argument(
        "--context",
        default=None,
        help="Optional hotwords or context string passed to the backend when supported",
    )
    transcribe_parser.add_argument(
        "--chunk-duration",
        type=float,
        default=None,
        help="Chunk duration in seconds when supported by the selected model",
    )
    transcribe_parser.add_argument(
        "--frame-threshold",
        type=int,
        default=None,
        help="Frame threshold for timestamp/segmentation heuristics when supported",
    )
    transcribe_parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Maximum generated tokens when supported by the selected model",
    )
    transcribe_parser.add_argument(
        "--max-chars-per-line",
        type=int,
        default=None,
        help="Maximum characters per line in SRT output",
    )
    transcribe_parser.add_argument(
        "--aligner-model",
        default="Qwen/Qwen3-ForcedAligner-0.6B",
        help=(
            "Forced aligner model used to obtain exact word timings for SRT splitting "
            "when the transcription model does not return word-level timestamps."
        ),
    )
    transcribe_parser.add_argument(
        "--stream",
        action="store_true",
        help="Use streaming generation when supported by the selected model",
    )
    transcribe_parser.add_argument(
        "--gen-kwargs",
        default=None,
        help='Extra backend kwargs as JSON, e.g. \'{"min_chunk_duration": 1.0}\'',
    )
    transcribe_parser.add_argument(
        "--verbose", action="store_true", help="Verbose backend output"
    )

    rectify_parser = subparsers.add_parser(
        "rectify",
        help="Correct wrong subtitle words with Grok while preserving cue timing",
    )
    rectify_parser.add_argument("srt", nargs="+", help="SRT file(s) to correct")
    rectify_parser.add_argument(
        "--wait-seconds",
        type=int,
        default=90,
        help="Maximum seconds to wait for Grok to return corrected SRT",
    )
    rectify_parser.add_argument(
        "--new-chat",
        action="store_true",
        help="Start a new Gemini chat before sending the prompt",
    )
    rectify_parser.add_argument(
        "--verbose", action="store_true", help="Verbose OpenCLI output"
    )

    all_parser = subparsers.add_parser(
        "all",
        help="Transcribe with Qwen 0.6B to SRT, then rectify it with Grok",
    )
    all_parser.add_argument("input", help="Input audio or video file")
    all_parser.add_argument(
        "--model",
        default="Qwen/Qwen3-ASR-0.6B",
        help=(
            "Model alias, official HF repo, MLX-community repo, or local path. "
            "Defaults to Qwen 0.6B for the combined flow."
        ),
    )
    all_parser.add_argument(
        "--output",
        "-o",
        default=".",
        help=(
            "Output directory or filename prefix for the intermediate SRT. "
            "If this is a directory or ends with '/', the input stem is used."
        ),
    )
    all_parser.add_argument(
        "--max-chars-per-line",
        type=int,
        default=None,
        help="Maximum characters per line in SRT output",
    )
    all_parser.add_argument(
        "--aligner-model",
        default="Qwen/Qwen3-ForcedAligner-0.6B",
        help=(
            "Forced aligner model used to obtain exact word timings for SRT splitting "
            "when the transcription model does not return word-level timestamps."
        ),
    )
    all_parser.add_argument(
        "--language", default=None, help="Language hint, e.g. en or zh"
    )
    all_parser.add_argument(
        "--context",
        default=None,
        help="Optional hotwords or context string passed to the transcription backend",
    )
    all_parser.add_argument(
        "--chunk-duration",
        type=float,
        default=None,
        help="Chunk duration in seconds when supported by the selected model",
    )
    all_parser.add_argument(
        "--frame-threshold",
        type=int,
        default=None,
        help="Frame threshold for timestamp/segmentation heuristics when supported",
    )
    all_parser.add_argument(
        "--max-tokens",
        type=int,
        default=None,
        help="Maximum generated tokens when supported by the selected model",
    )
    all_parser.add_argument(
        "--stream",
        action="store_true",
        help="Use streaming generation when supported by the selected model",
    )
    all_parser.add_argument(
        "--gen-kwargs",
        default=None,
        help='Extra backend kwargs as JSON, e.g. \'{"min_chunk_duration": 1.0}\'',
    )
    all_parser.add_argument(
        "--wait-seconds",
        type=int,
        default=90,
        help="Maximum seconds to wait for Grok to return corrected SRT",
    )
    all_parser.add_argument(
        "--new-chat",
        action="store_true",
        help="Start a new Gemini chat before sending the prompt",
    )
    all_parser.add_argument(
        "--verbose", action="store_true", help="Verbose output"
    )
    return parser


def normalize_model(model: str) -> str:
    path = Path(model).expanduser()
    if path.exists():
        return str(path)
    return MODEL_ALIASES.get(model, MODEL_ALIASES.get(model.lower(), model))


def parse_gen_kwargs(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid --gen-kwargs JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("--gen-kwargs must decode to a JSON object")
    return parsed


def print_model_aliases() -> None:
    print("Built-in model aliases:")
    print("  qwen -> mlx-community/Qwen3-ASR-0.6B-4bit")
    print("  Qwen/Qwen3-ASR-0.6B -> mlx-community/Qwen3-ASR-0.6B-4bit")
    print("  glm -> mlx-community/GLM-ASR-Nano-2512-4bit")
    print("  zai-org/GLM-ASR-Nano-2512 -> mlx-community/GLM-ASR-Nano-2512-4bit")


def resolve_output_prefix(audio_path: Path, output_arg: str, multiple_inputs: bool) -> str:
    output_path = Path(output_arg).expanduser()
    if output_arg in {".", "./"}:
        return str(audio_path.parent / audio_path.stem)

    treat_as_dir = (
        output_arg.endswith(("/", "\\"))
        or output_path.exists() and output_path.is_dir()
        or multiple_inputs
    )
    if treat_as_dir:
        return str(output_path / audio_path.stem)
    if output_path.suffix:
        return str(output_path.with_suffix(""))
    return str(output_path)


def uniquify_output_prefix(output_prefix: str, output_format: str) -> str:
    output_path = Path(output_prefix)
    candidate = output_path
    counter = 2
    while candidate.with_suffix(f".{output_format}").exists():
        candidate = output_path.with_name(f"{output_path.name}-{counter}")
        counter += 1
    return str(candidate)


def uniquify_path(path: Path) -> Path:
    candidate = path
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
        counter += 1
    return candidate


def load_backend():
    from mlx_audio.stt.generate import generate_transcription
    from mlx_audio.stt.utils import load_model

    return load_model, generate_transcription


SRT_TIMESTAMP_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+(?P<end>\d{2}:\d{2}:\d{2},\d{3})"
)


def format_srt_timestamp(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}".replace(".", ",")


STRONG_BREAK_CHARS = set("。！？!?；;")


def visible_length(text: str) -> int:
    return sum(1 for char in text if not char.isspace())


def combine_cue_text(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    if left[-1].isalnum() and right[0].isalnum():
        return f"{left} {right}"
    return f"{left}{right}"


def strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_srt_entries(content: str) -> list[dict[str, str]]:
    normalized = content.replace("\r\n", "\n").strip()
    if not normalized:
        return []

    entries: list[dict[str, str]] = []
    for block in re.split(r"\n\s*\n", normalized):
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3:
            raise SystemExit("Invalid SRT: each cue must contain index, timeline, and text.")
        match = SRT_TIMESTAMP_RE.fullmatch(lines[1].strip())
        if match is None:
            raise SystemExit(f"Invalid SRT timeline: {lines[1]}")
        entries.append(
            {
                "index": lines[0].strip(),
                "start": match.group("start"),
                "end": match.group("end"),
                "text": "\n".join(lines[2:]).strip(),
            }
        )
    return entries


def render_srt_entries(entries: list[dict[str, str]]) -> str:
    blocks = []
    for entry in entries:
        blocks.append(
            "\n".join(
                [
                    entry["index"],
                    f"{entry['start']} --> {entry['end']}",
                    entry["text"],
                ]
            )
        )
    return "\n\n".join(blocks) + "\n"


def corrected_srt_path(input_path: Path) -> Path:
    return uniquify_path(input_path.with_name(f"{input_path.stem}.correct{input_path.suffix}"))


def run_opencli(args: list[str], verbose: bool = False) -> str:
    try:
        result = subprocess.run(
            ["opencli", *args],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SystemExit("opencli is not installed or not on PATH.") from exc
    if result.returncode != 0:
        raise SystemExit((result.stderr or result.stdout).strip() or "opencli command failed")
    if verbose and result.stdout.strip():
        print(result.stdout, file=sys.stderr)
    return result.stdout


def ask_gemini(prompt: str, wait_seconds: int, new_chat: bool, verbose: bool) -> str:
    args = ["gemini", "ask", "--format", "json", "--timeout", str(wait_seconds)]
    if new_chat:
        args.extend(["--new", "true"])
    if verbose:
        args.append("--verbose")
    args.append(prompt)
    raw = run_opencli(args, verbose=False).strip()
    if verbose and raw:
        print(raw, file=sys.stderr)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Failed to parse Gemini response JSON: {raw}") from exc

    if isinstance(parsed, list) and parsed:
        first = parsed[0]
        if isinstance(first, dict) and "response" in first:
            return str(first["response"])
    if isinstance(parsed, dict) and "response" in parsed:
        return str(parsed["response"])
    raise SystemExit(f"Unexpected Gemini response JSON shape: {raw}")


def build_rectify_prompt(srt_text: str) -> str:
    return (
        "Correct wrong subtitle words based on the overall context of this SRT.\n"
        "Keep every cue index and every timeline line exactly unchanged.\n"
        "Only fix mistaken words in subtitle text.\n"
        "Return only valid SRT, no explanation, no markdown fences.\n\n"
        f"{srt_text}"
    )


def print_labeled_block(label: str, content: str) -> None:
    print(f"=== {label} ===", file=sys.stderr)
    print(content, file=sys.stderr)
    print(file=sys.stderr)


def correct_srt_with_gemini(
    srt_text: str,
    wait_seconds: int,
    new_chat: bool,
    verbose: bool,
) -> str:
    prompt = build_rectify_prompt(srt_text)
    print_labeled_block("SRT INPUT", srt_text)
    print_labeled_block("GEMINI PROMPT", prompt)
    response = ask_gemini(
        prompt=prompt,
        wait_seconds=wait_seconds,
        new_chat=new_chat,
        verbose=verbose,
    )
    candidate = strip_code_fences(response)
    print_labeled_block("GEMINI RESPONSE", candidate)
    return candidate


def rectify_file(args: argparse.Namespace) -> int:
    for raw_srt in args.srt:
        srt_path = Path(raw_srt).expanduser()
        if not srt_path.exists():
            raise SystemExit(f"SRT file not found: {srt_path}")
        if srt_path.suffix.lower() != ".srt":
            raise SystemExit(f"Expected an .srt file: {srt_path}")

        original_text = srt_path.read_text(encoding="utf-8")
        original_entries = parse_srt_entries(original_text)
        corrected_raw = correct_srt_with_gemini(
            srt_text=original_text,
            wait_seconds=args.wait_seconds,
            new_chat=args.new_chat,
            verbose=args.verbose,
        )
        corrected_entries = parse_srt_entries(corrected_raw)
        if len(corrected_entries) != len(original_entries):
            raise SystemExit(
                "Grok returned a different number of SRT cues; refusing to change timing layout."
            )

        merged_entries: list[dict[str, str]] = []
        for original, corrected in zip(original_entries, corrected_entries, strict=True):
            merged_entries.append(
                {
                    "index": original["index"],
                    "start": original["start"],
                    "end": original["end"],
                    "text": corrected["text"],
                }
            )

        output_path = corrected_srt_path(srt_path)
        output_path.write_text(render_srt_entries(merged_entries), encoding="utf-8")
        print(output_path)
    return 0


def extract_sentence_tokens(sentence: Any) -> list[dict[str, float | str]]:
    tokens = getattr(sentence, "tokens", None)
    if not tokens:
        return []

    extracted: list[dict[str, float | str]] = []
    for token in tokens:
        text = getattr(token, "text", "").strip()
        start = getattr(token, "start", None)
        end = getattr(token, "end", None)
        if not text or start is None or end is None:
            continue
        extracted.append({"start": float(start), "end": float(end), "text": text})
    return extracted


def extract_precise_timed_units(result: Any) -> list[dict[str, float | str]]:
    units: list[dict[str, float | str]] = []

    if hasattr(result, "sentences") and result.sentences is not None:
        for sentence in result.sentences:
            sentence_tokens = extract_sentence_tokens(sentence)
            if sentence_tokens:
                units.extend(sentence_tokens)
        if units:
            return units

    if hasattr(result, "segments") and result.segments is not None:
        for segment in result.segments:
            words = segment.get("words") or []
            if words:
                for word in words:
                    text = (word.get("word") or word.get("text") or "").strip()
                    start = word.get("start")
                    end = word.get("end")
                    if not text or start is None or end is None:
                        continue
                    units.append({"start": float(start), "end": float(end), "text": text})
        return units

    return units


def extract_aligned_units(alignment_result: Any) -> list[dict[str, float | str]]:
    extracted: list[dict[str, float | str]] = []
    for item in alignment_result:
        text = getattr(item, "text", "").strip()
        start = getattr(item, "start_time", None)
        end = getattr(item, "end_time", None)
        if not text or start is None or end is None:
            continue
        extracted.append({"start": float(start), "end": float(end), "text": text})
    return extracted


def align_transcript_words(
    load_model: Any,
    aligner_model_name: str,
    audio_path: str,
    transcript_text: str,
    language: str | None,
    verbose: bool,
) -> list[dict[str, float | str]]:
    if not transcript_text.strip():
        return []

    aligner = load_model(normalize_model(aligner_model_name))
    kwargs: dict[str, Any] = {"text": transcript_text}
    if language is not None:
        kwargs["language"] = language
    alignment_result = aligner.generate(audio_path, verbose=verbose, **kwargs)
    return extract_aligned_units(alignment_result)


def build_srt_cues_from_units(
    units: list[dict[str, float | str]],
    max_chars_per_line: int,
) -> list[dict[str, float | str]]:
    cues: list[dict[str, float | str]] = []
    current_units: list[dict[str, float | str]] = []
    current_text = ""

    def flush() -> None:
        nonlocal current_units, current_text
        if not current_units:
            return
        cues.append(
            {
                "start": float(current_units[0]["start"]),
                "end": float(current_units[-1]["end"]),
                "text": current_text,
            }
        )
        current_units = []
        current_text = ""

    for unit in units:
        text = str(unit["text"]).strip()
        if not text:
            continue

        candidate_text = combine_cue_text(current_text, text)
        if current_units and visible_length(candidate_text) > max_chars_per_line:
            flush()
            candidate_text = text

        current_units.append(unit)
        current_text = candidate_text

        if visible_length(current_text) >= max_chars_per_line or text[-1] in STRONG_BREAK_CHARS:
            flush()

    flush()
    return cues


def build_exact_srt_cues(
    result: Any,
    load_model: Any,
    backend_audio: str,
    max_chars_per_line: int,
    aligner_model_name: str,
    language: str | None,
    verbose: bool,
) -> list[dict[str, float | str]]:
    units = extract_precise_timed_units(result)
    if not units:
        units = align_transcript_words(
            load_model=load_model,
            aligner_model_name=aligner_model_name,
            audio_path=backend_audio,
            transcript_text=getattr(result, "text", ""),
            language=language,
            verbose=verbose,
        )
    if not units:
        return []
    return build_srt_cues_from_units(units, max_chars_per_line)


def rewrite_srt_with_line_limit(
    result: Any,
    output_prefix: str,
    load_model: Any,
    backend_audio: str,
    max_chars_per_line: int,
    aligner_model_name: str,
    language: str | None,
    verbose: bool,
) -> None:
    cues = build_exact_srt_cues(
        result=result,
        load_model=load_model,
        backend_audio=backend_audio,
        max_chars_per_line=max_chars_per_line,
        aligner_model_name=aligner_model_name,
        language=language,
        verbose=verbose,
    )
    if not cues:
        raise SystemExit(
            "Exact SRT splitting requested, but no word-level timestamps were available "
            "and forced alignment did not return aligned words."
        )

    output_path = Path(f"{output_prefix}.srt")
    with output_path.open("w", encoding="utf-8") as handle:
        for index, cue in enumerate(cues, start=1):
            start = max(float(cue["start"]), 0.0)
            end = max(float(cue["end"]), start + 0.001)
            handle.write(f"{index}\n")
            handle.write(
                f"{format_srt_timestamp(start)} --> "
                f"{format_srt_timestamp(end)}\n"
            )
            handle.write(f"{str(cue['text']).strip()}\n\n")


def input_requires_ffmpeg(audio_path: Path) -> bool:
    return audio_path.suffix.lower() in FFMPEG_INPUT_SUFFIXES


def decode_with_ffmpeg(audio_path: Path, stack: ExitStack) -> str:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SystemExit(
            "This input format requires ffmpeg, but ffmpeg was not found on PATH."
        )

    tmp = stack.enter_context(
        tempfile.NamedTemporaryFile(prefix="asr-cli-", suffix=".wav", delete=True)
    )
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(audio_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        tmp.name,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(
            f"ffmpeg failed to decode input media:\n{result.stderr.strip()}"
        )
    return tmp.name


def transcribe_files(args: argparse.Namespace) -> list[Path]:
    if args.max_chars_per_line is not None and args.max_chars_per_line <= 0:
        raise SystemExit("--max-chars-per-line must be greater than 0")

    resolved_model = normalize_model(args.model)
    load_model, generate_transcription = load_backend()
    model = load_model(resolved_model)

    base_kwargs: dict[str, Any] = {
        "format": args.format,
        "verbose": args.verbose,
    }
    if args.language is not None:
        base_kwargs["language"] = args.language
    if args.context is not None:
        base_kwargs["context"] = args.context
    if args.chunk_duration is not None:
        base_kwargs["chunk_duration"] = args.chunk_duration
    if args.frame_threshold is not None:
        base_kwargs["frame_threshold"] = args.frame_threshold
    if args.max_tokens is not None:
        base_kwargs["max_tokens"] = args.max_tokens
    if args.stream:
        base_kwargs["stream"] = True
    base_kwargs.update(parse_gen_kwargs(args.gen_kwargs))

    multiple_inputs = len(args.audio) > 1
    written_paths: list[Path] = []
    for raw_audio in args.audio:
        audio_path = Path(raw_audio).expanduser()
        if not audio_path.exists():
            raise SystemExit(f"Input file not found: {audio_path}")

        output_prefix = uniquify_output_prefix(
            resolve_output_prefix(audio_path, args.output, multiple_inputs),
            args.format,
        )
        with ExitStack() as stack:
            backend_audio = str(audio_path)
            if input_requires_ffmpeg(audio_path):
                backend_audio = decode_with_ffmpeg(audio_path, stack)

            if args.verbose:
                print(f"Input: {audio_path}", file=sys.stderr)
                print(f"Model: {resolved_model}", file=sys.stderr)
                print(f"Output: {output_prefix}.{args.format}", file=sys.stderr)
                if backend_audio != str(audio_path):
                    print("Decode: ffmpeg -> temporary wav", file=sys.stderr)

            result = generate_transcription(
                model=model,
                audio=backend_audio,
                output_path=output_prefix,
                **base_kwargs,
            )
            if args.format == "srt" and args.max_chars_per_line is not None:
                rewrite_srt_with_line_limit(
                    result=result,
                    output_prefix=output_prefix,
                    load_model=load_model,
                    backend_audio=backend_audio,
                    max_chars_per_line=args.max_chars_per_line,
                    aligner_model_name=args.aligner_model,
                    language=args.language,
                    verbose=args.verbose,
                )
        written_paths.append(Path(f"{output_prefix}.{args.format}"))
        if not args.verbose and hasattr(result, "text"):
            print(result.text)

    return written_paths


def run_all(args: argparse.Namespace) -> int:
    transcribe_args = argparse.Namespace(
        action="transcribe",
        audio=[args.input],
        model=args.model,
        resolved_model=False,
        list_models=False,
        output=args.output,
        format="srt",
        language=args.language,
        context=args.context,
        chunk_duration=args.chunk_duration,
        frame_threshold=args.frame_threshold,
        max_tokens=args.max_tokens,
        max_chars_per_line=args.max_chars_per_line,
        aligner_model=args.aligner_model,
        stream=args.stream,
        gen_kwargs=args.gen_kwargs,
        verbose=args.verbose,
    )
    written_paths = transcribe_files(transcribe_args)
    if len(written_paths) != 1:
        raise SystemExit("The all action expected exactly one generated SRT file.")

    rectify_args = argparse.Namespace(
        action="rectify",
        srt=[str(written_paths[0])],
        wait_seconds=args.wait_seconds,
        new_chat=args.new_chat,
        verbose=args.verbose,
    )
    return rectify_file(rectify_args)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.action == "rectify":
        return rectify_file(args)
    if args.action == "all":
        return run_all(args)

    if args.list_models:
        print_model_aliases()
        return 0

    resolved_model = normalize_model(args.model)
    if args.resolved_model:
        print(resolved_model)
        return 0

    transcribe_files(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
