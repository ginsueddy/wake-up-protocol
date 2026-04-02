# Wake Up Protocol

Double-clap to boot up your dev environment. On trigger:

1. Opens a YouTube video in the browser
2. Opens one new Terminal.app window
3. Starts Claude Code in the first tab (in `~/github/cuddy`)
4. Starts Codex in the second tab (in `~/github/cuddy`)

## Setup

```bash
cd ~/github/wake-up-protocol
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

First run will prompt for macOS microphone permission for Terminal.app.

## Usage

**Calibrate** (find the right threshold for your mic):
```bash
.venv/bin/python3 wake_up.py --calibrate
```

**Run:**
```bash
.venv/bin/python3 wake_up.py --threshold 0.5 --verbose
```

**Run as background daemon:**
```bash
nohup .venv/bin/python3 wake_up.py --threshold 0.5 > wake_up.log 2>&1 &
echo $! > wake_up.pid
```

**Stop:**
```bash
kill $(cat wake_up.pid)
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--threshold` | 0.5 | Peak amplitude threshold for clap detection |
| `--cooldown` | 5.0 | Seconds to ignore after a trigger |
| `--double-clap-window` | 0.5 | Max seconds between two claps |
| `--url` | (hardcoded) | YouTube URL to open |
| `--project-dir` | ~/github/cuddy | Directory for Claude Code and Codex |
| `--device` | system default | Audio input device index |
| `--calibrate` | | Run calibration mode for 10s |
| `--verbose` | | Enable debug logging |

## How it works

Listens to the microphone and detects double hand-claps using:
- **Amplitude threshold** — filters out quiet ambient noise
- **Crest factor analysis** (peak/RMS ratio >= 4.0) — rejects sustained sounds like speech
- **Transient validation** — claps must spike and drop off within ~5 audio blocks (~115ms)
- **Quiet confirmation** — requires 2 consecutive quiet blocks after each spike

Works best in a relatively quiet environment. Designed to have false negatives over false positives.
