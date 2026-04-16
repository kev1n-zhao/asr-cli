# asr-cli

CLI for Apple Silicon ASR on macOS using `mlx-audio`, with:
- `transcribe` for audio/video transcription
- `rectify` for SRT correction using `opencli gemini ask`
- `all` for transcribe-then-rectify in one command
- `fcpxml` export for Final Cut Pro subtitle import

## Actions

### `transcribe`

Generate transcript output from an input media file.

```bash
asr-cli transcribe input.mp4 --format srt --max-chars-per-line 24
```

By default, output is written next to the input file. If the target output file
already exists, a numeric suffix is added.

You can also export Final Cut Pro subtitles directly:

```bash
asr-cli transcribe input.mp4 --format fcpxml --max-chars-per-line 24
```

This writes an `.fcpxml` project containing caption elements with transparent
subtitle backgrounds so the file can be imported into Final Cut Pro.

### `rectify`

Read an existing `.srt`, send the full content to Gemini through OpenCLI, and
write a corrected `.correct.srt` while preserving the original cue indices and
timeline segments.

```bash
asr-cli rectify input.srt --wait-seconds 90 --new-chat
```

This action uses `opencli gemini ask`.

### `all`

Run the full flow in one command:
1. transcribe the input file with `Qwen/Qwen3-ASR-0.6B` to SRT
2. rectify that generated SRT with Gemini

```bash
asr-cli all input.mp4 --max-chars-per-line 24 --new-chat
```
