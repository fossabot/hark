# hark 😇

[![PyPI version](https://img.shields.io/pypi/v/hark-cli)](https://pypi.org/project/hark-cli/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

> 100% offline, Whisper-powered voice notes from your terminal

**Use cases:** Voice prompts for LLMs (`hark | llm`), meeting minutes, quick voice journaling

## Features

- 🎙️ **Record** - Press space to start, Ctrl+C to stop
- 🔊 **Multi-source** - Capture microphone, system audio, or both
- ✨ **Transcribe** - Powered by [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- 🔒 **Local** - 100% offline, no cloud required
- 📄 **Flexible** - Output as plain text, markdown, or SRT subtitles

## Installation

```bash
pipx install hark-cli
```

### System Dependencies

**Ubuntu/Debian:**

```bash
sudo apt install portaudio19-dev
```

**macOS:**

```bash
brew install portaudio
```

### Optional: Vulkan Acceleration

For GPU-accelerated transcription via Vulkan (AMD/Intel GPUs):

**Ubuntu/Debian:**

```bash
sudo apt install libvulkan1 vulkan-tools mesa-vulkan-drivers
```

Then set the device in your config or use `--device vulkan`.

## Quick Start

```bash
# Record and print to stdout
hark

# Save to file
hark notes.txt

# Use larger model for better accuracy
hark --model large-v3 meeting.md

# Transcribe in German
hark --lang de notes.txt

# Output as SRT subtitles
hark --format srt captions.srt

# Capture system audio (e.g., online meetings)
hark --input speaker meeting.txt

# Capture both microphone and system audio (stereo: L=mic, R=speaker)
hark --input both conversation.txt
```

## Configuration

Hark uses a YAML config file at `~/.config/hark/config.yaml`. CLI flags override config file settings.

```yaml
# ~/.config/hark/config.yaml
recording:
  sample_rate: 16000
  channels: 1 # Use 2 for --input both
  max_duration: 600
  input_source: mic # mic, speaker, or both

whisper:
  model: base # tiny, base, small, medium, large, large-v2, large-v3
  language: auto # auto, en, de, fr, es, ...
  device: auto # auto, cpu, cuda, vulkan

preprocessing:
  noise_reduction:
    enabled: true
    strength: 0.5 # 0.0-1.0
  normalization:
    enabled: true
  silence_trimming:
    enabled: true

output:
  format: plain # plain, markdown, srt
  timestamps: false
```

## Audio Input Sources

Hark supports three input modes via `--input` or `recording.input_source`:

| Mode | Description |
|------|-------------|
| `mic` | Microphone only (default) |
| `speaker` | System audio only (loopback capture) |
| `both` | Microphone + system audio as stereo (L=mic, R=speaker) |

### System Audio Capture (Linux)

System audio capture uses PulseAudio/PipeWire monitor sources. To verify your system supports it:

```bash
pactl list sources | grep -i monitor
```

You should see output like:
```
Name: alsa_output.pci-0000_00_1f.3.analog-stereo.monitor
Description: Monitor of Built-in Audio
```

### Use Cases

- **Online meetings**: Use `--input speaker` to transcribe remote participants
- **Conversations**: Use `--input both` to capture both sides for future diarization
- **Voice notes**: Use `--input mic` (default) for personal dictation

## Development

```bash
git clone https://github.com/FPurchess/hark.git
cd hark
uv sync --extra test
uv run pre-commit install
uv run pytest
```

## License

[AGPLv3](LICENSE)
