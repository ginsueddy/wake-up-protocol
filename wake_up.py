#!/usr/bin/env python3
"""Wake Up Protocol — double-clap triggers YouTube, Codex, and Claude Code."""
from __future__ import annotations

import argparse
import fcntl
import logging
import os
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser

import sounddevice as sd

YOUTUBE_URL = "https://youtu.be/xMaE6toi4mk?si=CWhHdUmFd6OdACn0"
PROJECT_DIR = "~/github/cuddy"
SAMPLE_RATE = 44100
BLOCK_SIZE = 1024
CLAP_THRESHOLD = 0.5
DOUBLE_CLAP_WINDOW = 0.5
DOUBLE_CLAP_MIN_GAP = 0.08
COOLDOWN_SECONDS = 5.0

log = logging.getLogger("wake_up")

# States for the clap detection state machine
STATE_IDLE = 0             # waiting for a loud spike
STATE_SPIKE_DETECTED = 1   # spike seen, waiting for it to drop (confirms transient)
STATE_FIRST_CLAP = 2       # first clap confirmed, waiting for second
STATE_SECOND_SPIKE = 3     # second spike seen, waiting for drop to confirm

# Max consecutive loud blocks before we reject as speech (not a clap)
MAX_LOUD_BLOCKS = 5
# Minimum crest factor (peak/RMS) to qualify as impulsive (clap-like)
MIN_CREST_FACTOR = 4.0
# Quiet threshold: must drop below this absolute level to confirm clap ended
QUIET_ABSOLUTE = 0.08
LOCK_PATH = os.path.join(tempfile.gettempdir(), "wake_up_protocol.lock")


def wake_up_actions(url: str, project_dir: str):
    """Fire all wake-up actions simultaneously."""
    log.info("WAKE UP PROTOCOL ACTIVATED")
    expanded_project_dir = os.path.abspath(os.path.expanduser(project_dir))
    applescript_project_dir = (
        expanded_project_dir.replace("\\", "\\\\").replace('"', '\\"')
    )
    try:
        terminal_profile = subprocess.check_output(
            ["defaults", "read", "com.apple.Terminal", "Startup Window Settings"],
            text=True,
        ).strip()
    except subprocess.SubprocessError:
        terminal_profile = "Basic"
    applescript_profile = terminal_profile.replace("\\", "\\\\").replace('"', '\\"')

    # Action 1: YouTube video
    log.info("opening %s", url)
    webbrowser.open(url)
    time.sleep(0.4)

    # Action 2 & 3: Codex + Claude Code in one Terminal window, two tabs
    log.info("launching Codex and Claude Code in %s", expanded_project_dir)
    applescript_lines = [
        f'set projectDir to "{applescript_project_dir}"',
        f'set profileName to "{applescript_profile}"',
        'set codexCommand to "cd " & quoted form of projectDir & " && codex"',
        'set claudeCommand to "cd " & quoted form of projectDir & " && claude"',
        'tell application "Terminal"',
        '    activate',
        '    do script codexCommand',
        '    set theWindow to front window',
        'end tell',
        'delay 0.5',
        'tell application "System Events"',
        '    tell process "Terminal"',
        '        set frontmost to true',
        '        click menu item "New Tab" of menu "Shell" of menu bar 1',
        '        click menu item profileName of menu 1 of menu item "New Tab" of menu "Shell" of menu bar 1',
        '    end tell',
        'end tell',
        'delay 0.5',
        'tell application "Terminal"',
        '    set claudeWindow to front window',
        '    do script claudeCommand in claudeWindow',
        'end tell',
    ]
    osascript_command = ["osascript"]
    for line in applescript_lines:
        osascript_command.extend(["-e", line])
    result = subprocess.run(
        osascript_command,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error("Terminal launcher failed: %s", result.stderr.strip() or result.returncode)
    elif result.stdout.strip():
        log.debug("Terminal launcher result: %s", result.stdout.strip())


def acquire_single_instance_lock():
    """Prevent multiple listeners from reacting to the same clap."""
    lock_file = open(LOCK_PATH, "w", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        raise RuntimeError(
            f"Wake Up Protocol is already running. Stop the existing process or remove {LOCK_PATH}."
        )
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


class ClapDetector:
    def __init__(self, threshold: float, cooldown: float, window: float,
                 min_gap: float, url: str, project_dir: str,
                 device: int | None, timeout: float | None = None):
        self.threshold = threshold
        self.cooldown = cooldown
        self.window = window
        self.min_gap = min_gap
        self.url = url
        self.project_dir = project_dir
        self.device = device
        self.timeout = timeout
        self._triggered = False
        self._state = STATE_IDLE
        self._spike_time = 0.0
        self._spike_crest = 0.0
        self._first_clap_time = 0.0
        self._loud_count = 0
        self._quiet_count = 0
        self._last_trigger_time = 0.0
        self._trigger_event = threading.Event()
        self._shutdown_event = threading.Event()

    def _analyze(self, indata: bytes, frames: int) -> tuple[float, float]:
        """Return (peak, crest_factor) for an audio block."""
        samples = struct.unpack(f"{frames}f", bytes(indata))
        if not samples:
            return 0.0, 0.0
        peak = max(abs(s) for s in samples)
        sum_sq = sum(s * s for s in samples)
        rms = (sum_sq / len(samples)) ** 0.5
        crest = peak / rms if rms > 0 else 0.0
        return peak, crest

    def _audio_callback(self, indata: bytes, frames: int, time_info, status):
        if status:
            log.debug("stream status: %s", status)
        now = time.monotonic()
        if now - self._last_trigger_time < self.cooldown:
            return
        peak, crest = self._analyze(indata, frames)
        loud = peak >= self.threshold
        quiet = peak < QUIET_ABSOLUTE
        QUIET_BLOCKS_NEEDED = 2

        if self._state == STATE_IDLE:
            if loud and crest >= MIN_CREST_FACTOR:
                self._spike_time = now
                self._spike_crest = crest
                self._loud_count = 1
                self._quiet_count = 0
                self._state = STATE_SPIKE_DETECTED
                log.debug("spike detected  peak=%.3f  crest=%.1f", peak, crest)
            elif loud:
                log.debug("loud but low crest (speech)  peak=%.3f  crest=%.1f", peak, crest)

        elif self._state == STATE_SPIKE_DETECTED:
            if loud:
                self._loud_count += 1
                self._quiet_count = 0
                if self._loud_count > MAX_LOUD_BLOCKS:
                    log.debug("too many loud blocks (%d) — speech. resetting",
                              self._loud_count)
                    self._state = STATE_IDLE
            elif quiet:
                self._quiet_count += 1
                if self._quiet_count >= QUIET_BLOCKS_NEEDED:
                    self._first_clap_time = self._spike_time
                    self._state = STATE_FIRST_CLAP
                    log.info("first clap confirmed  blocks=%d  crest=%.1f",
                             self._loud_count, self._spike_crest)
            else:
                self._quiet_count = 0

        elif self._state == STATE_FIRST_CLAP:
            elapsed = now - self._first_clap_time
            if elapsed > self.window:
                log.debug("first clap expired, resetting")
                if loud and crest >= MIN_CREST_FACTOR:
                    self._spike_time = now
                    self._spike_crest = crest
                    self._loud_count = 1
                    self._quiet_count = 0
                    self._state = STATE_SPIKE_DETECTED
                else:
                    self._state = STATE_IDLE
            elif loud and crest >= MIN_CREST_FACTOR and elapsed >= self.min_gap:
                self._spike_time = now
                self._spike_crest = crest
                self._loud_count = 1
                self._quiet_count = 0
                self._state = STATE_SECOND_SPIKE
                log.debug("second spike detected  gap=%.3fs  peak=%.3f  crest=%.1f",
                          elapsed, peak, crest)

        elif self._state == STATE_SECOND_SPIKE:
            elapsed = now - self._first_clap_time
            if elapsed > self.window:
                log.debug("window expired during second spike, resetting")
                self._state = STATE_IDLE
            elif loud:
                self._loud_count += 1
                self._quiet_count = 0
                if self._loud_count > MAX_LOUD_BLOCKS:
                    log.debug("second spike too long (%d blocks) — speech. resetting",
                              self._loud_count)
                    self._state = STATE_IDLE
            elif quiet:
                self._quiet_count += 1
                if self._quiet_count >= QUIET_BLOCKS_NEEDED:
                    gap = self._spike_time - self._first_clap_time
                    log.info("DOUBLE CLAP detected  gap=%.3fs", gap)
                    self._last_trigger_time = now
                    self._state = STATE_IDLE
                    self._trigger_event.set()
            else:
                self._quiet_count = 0

    def run(self):
        def _signal_handler(signum, frame):
            log.info("shutdown signal received")
            self._shutdown_event.set()

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        log.info("Wake Up Protocol armed  threshold=%.2f  cooldown=%.1fs",
                 self.threshold, self.cooldown)
        if self.timeout:
            log.info("timeout=%.0fs — will exit if no clap detected", self.timeout)
        log.info("listening for double claps... (Ctrl+C to stop)")

        start_time = time.monotonic()
        with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE,
                                dtype="float32", channels=1,
                                device=self.device,
                                callback=self._audio_callback):
            while not self._shutdown_event.is_set():
                if self._trigger_event.wait(timeout=0.1):
                    self._trigger_event.clear()
                    wake_up_actions(self.url, self.project_dir)
                    self._triggered = True
                    log.info("Wake Up Protocol complete — shutting down")
                    self._shutdown_event.set()
                elif self.timeout and (time.monotonic() - start_time) >= self.timeout:
                    log.info("timeout reached (%.0fs), no clap detected — shutting down",
                             self.timeout)
                    self._shutdown_event.set()

        log.info("Wake Up Protocol disarmed")


def calibrate(device: int | None, duration: float = 10.0):
    """Print peak amplitudes for calibration."""
    print(f"Calibrating for {duration:.0f}s — clap to see your peak amplitude...")
    max_peak = 0.0

    def callback(indata: bytes, frames: int, time_info, status):
        nonlocal max_peak
        samples = struct.unpack(f"{frames}f", bytes(indata))
        peak = max(abs(s) for s in samples) if samples else 0.0
        if peak > 0.01:
            bar = "#" * int(peak * 50)
            print(f"  peak={peak:.4f}  {bar}")
        if peak > max_peak:
            max_peak = peak

    with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE,
                            dtype="float32", channels=1,
                            device=device, callback=callback):
        time.sleep(duration)

    print(f"\nMax peak observed: {max_peak:.4f}")
    print(f"Suggested --threshold: {max(0.1, max_peak * 0.6):.2f}  (60% of max)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=CLAP_THRESHOLD,
                        help=f"peak amplitude threshold (default: {CLAP_THRESHOLD})")
    parser.add_argument("--cooldown", type=float, default=COOLDOWN_SECONDS,
                        help=f"seconds to ignore after trigger (default: {COOLDOWN_SECONDS})")
    parser.add_argument("--double-clap-window", type=float, default=DOUBLE_CLAP_WINDOW,
                        help=f"max seconds between two claps (default: {DOUBLE_CLAP_WINDOW})")
    parser.add_argument("--url", default=YOUTUBE_URL,
                        help=f"YouTube URL to open (default: {YOUTUBE_URL})")
    parser.add_argument("--project-dir", default=PROJECT_DIR,
                        help=f"project directory for Codex and Claude Code (default: {PROJECT_DIR})")
    parser.add_argument("--device", type=int, default=None,
                        help="audio input device index (default: system default)")
    parser.add_argument("--timeout", type=float, default=None,
                        help="exit after N seconds if no clap detected")
    parser.add_argument("--calibrate", action="store_true",
                        help="run calibration mode for 10s")
    parser.add_argument("--verbose", action="store_true",
                        help="enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-5s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    try:
        lock_file = acquire_single_instance_lock()
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    if args.calibrate:
        try:
            calibrate(args.device)
        finally:
            lock_file.close()
        return 0

    detector = ClapDetector(
        threshold=args.threshold,
        cooldown=args.cooldown,
        window=args.double_clap_window,
        min_gap=DOUBLE_CLAP_MIN_GAP,
        url=args.url,
        project_dir=args.project_dir,
        device=args.device,
        timeout=args.timeout,
    )
    try:
        detector.run()
    finally:
        lock_file.close()
    return 0 if detector._triggered else 2


if __name__ == "__main__":
    sys.exit(main())
