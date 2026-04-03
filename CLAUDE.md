# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

macOS automation tool: double-clap (audio detection) or system wake/unlock triggers a dev environment launch — opens a YouTube video, then starts Codex and Claude Code in separate Terminal.app tabs pointed at `~/github/cuddy`.

## Architecture

Two-layer design:

1. **wake_trigger.swift** — Compiled Swift daemon registered as a macOS LaunchAgent. Listens for `NSWorkspace.didWakeNotification` and `com.apple.screenIsUnlocked` events. On trigger, spawns `wake_up.py --timeout 120` with 10-second debounce. Checks a lock file to avoid duplicate spawns.

2. **wake_up.py** — Python audio listener with a 4-state clap detection state machine (`IDLE → SPIKE_DETECTED → FIRST_CLAP → SECOND_SPIKE`). Uses sounddevice for real-time audio. On double-clap detection, runs AppleScript via `osascript` to open browser + Terminal tabs. Single-instance enforcement via `/tmp/wake_up_protocol.lock`.

**install.sh** compiles the Swift source, symlinks the plist to `~/Library/LaunchAgents/`, and loads it with `launchctl`.

## Commands

```bash
# Setup
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Calibrate mic threshold
.venv/bin/python3 wake_up.py --calibrate

# Run with verbose logging
.venv/bin/python3 wake_up.py --threshold 0.5 --verbose

# Install/uninstall LaunchAgent
./install.sh install
./install.sh uninstall
```

## Key Detection Parameters

- Sample rate: 44,100 Hz, block size: 1,024 (~23ms per block)
- Crest factor threshold: 4.0 (peak/RMS ratio — distinguishes impulsive sounds from speech)
- Transient max blocks: 5 (~115ms — clap spikes must drop off within this window)
- Quiet confirmation: 2 consecutive blocks below 0.08 amplitude
- Design philosophy: prefer false negatives over false positives
