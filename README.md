# hark 😇

[![PyPI version](https://img.shields.io/pypi/v/hark-cli)](https://pypi.org/project/hark-cli/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

> 100% offline, Whisper-powered voice notes from your terminal

**Use cases:** Voice prompts for LLMs (`hark | llm`), meeting minutes, quick voice journaling

## Features

- 🎙️ **Record** - Press space to start, Ctrl+C to stop
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
```

## Configuration

Hark uses a YAML config file at `~/.config/hark/config.yaml`. CLI flags override config file settings.

```yaml
# ~/.config/hark/config.yaml
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
