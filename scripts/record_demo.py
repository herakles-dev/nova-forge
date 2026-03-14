#!/usr/bin/env python3
"""Record a real Nova Forge build session as an asciinema .cast file.

Uses pexpect to automate a forge_cli.py session inside asciinema rec.
The recording captures Rich output (progress bars, colored panels, spinners)
from a real build with actual API calls.

Usage:
    source ~/.secrets/hercules.env
    python3 scripts/record_demo.py [--output web/demo.cast] [--model nova-lite]
"""

import argparse
import os
import sys
import time

import pexpect

FORGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUTPUT = os.path.join(FORGE_DIR, "web", "demo.cast")

PROMPT_TEXT = "Build an expense tracker with categories, monthly charts, CSV export, and a responsive UI"

# How long to wait for various stages (seconds)
TIMEOUT_STARTUP = 30
TIMEOUT_BUILD = 300  # builds can take a while with API calls
TIMEOUT_SHORT = 15


def record_demo(output_path: str, model: str = "nova-lite"):
    """Run a full demo recording."""
    print(f"[record_demo] Output: {output_path}")
    print(f"[record_demo] Model: {model}")
    print(f"[record_demo] Forge dir: {FORGE_DIR}")

    # Verify credentials
    if not os.environ.get("AWS_ACCESS_KEY_ID") and not os.environ.get("OPENROUTER_API_KEY"):
        print("[record_demo] ERROR: No API credentials found. Run: source ~/.secrets/hercules.env")
        sys.exit(1)

    # Build the asciinema command that wraps forge_cli.py
    cast_cmd = (
        f"asciinema rec --cols 100 --rows 30 --overwrite "
        f"--command 'python3 {os.path.join(FORGE_DIR, 'forge_cli.py')}' "
        f"{output_path}"
    )

    print(f"[record_demo] Starting: {cast_cmd}")
    child = pexpect.spawn(
        "/bin/bash", ["-c", cast_cmd],
        cwd=FORGE_DIR,
        encoding="utf-8",
        timeout=TIMEOUT_STARTUP,
        dimensions=(30, 100),
        env=os.environ.copy(),
    )

    # Log everything to stdout for debugging
    child.logfile_read = sys.stdout

    try:
        # Wait for the forge shell prompt (> character) or existing project menu
        print("\n[record_demo] Waiting for forge shell prompt...")
        child.expect([r"[>❯]", r"forge\s*>", r"Nova Forge", r"already have a project"], timeout=TIMEOUT_STARTUP)
        time.sleep(1.0)

        # Handle "You already have a project" menu if it appears
        if "already have a project" in (child.after or ""):
            print("\n[record_demo] Existing project detected, selecting 'Start fresh'...")
            child.sendline("1")  # Select "Start fresh"
            time.sleep(2.0)
            child.expect([r"[>❯]", r"forge\s*>", r"New project"], timeout=TIMEOUT_SHORT)
            time.sleep(0.5)

        # Set the model first
        print(f"\n[record_demo] Setting model to {model}...")
        child.sendline(f"/model {model}")
        time.sleep(2.0)

        # Wait for prompt again
        child.expect([r"[>❯]", r"forge\s*>", r"Model set"], timeout=TIMEOUT_SHORT)
        time.sleep(0.5)

        # Type the build description (character by character for realistic effect)
        print(f"\n[record_demo] Typing build description...")
        _type_slowly(child, PROMPT_TEXT, delay=0.04)
        time.sleep(0.3)
        child.sendline("")  # Press Enter

        # Wait for interview/planning phase
        print("\n[record_demo] Waiting for planning phase...")
        # The guided build path may ask about stack or go straight to planning
        # May also get another existing project prompt
        idx = child.expect(
            [
                r"Stack",              # Interview question
                r"Planning",           # Direct to planning
                r"Generating",         # Alternative planning indicator
                r"spec\.md",           # Spec generation
                r"already have",       # Existing project prompt again
                pexpect.TIMEOUT,
            ],
            timeout=TIMEOUT_BUILD,
        )

        if idx == 0:
            # Answer stack question - just press Enter for default
            time.sleep(1.0)
            child.sendline("")
        elif idx == 4:
            # Handle existing project prompt
            child.sendline("1")
            time.sleep(2.0)

        # Wait for build completion
        print("\n[record_demo] Waiting for build completion...")
        child.expect(
            [
                r"PASS",
                r"Built in",
                r"Build complete",
                r"All tasks completed",
                r"tasks.*completed",
                r"[Cc]omplete",
            ],
            timeout=TIMEOUT_BUILD,
        )
        print("\n[record_demo] Build completed!")
        time.sleep(2.0)

        # Type /quit to exit cleanly
        print("\n[record_demo] Exiting...")
        child.sendline("/quit")
        time.sleep(1.0)

        # Wait for process to finish
        child.expect(pexpect.EOF, timeout=TIMEOUT_SHORT)

    except pexpect.TIMEOUT:
        print("\n[record_demo] WARNING: Timed out waiting for expected output")
        print("[record_demo] Sending /quit to clean up...")
        try:
            child.sendline("/quit")
            child.expect(pexpect.EOF, timeout=10)
        except Exception:
            child.close(force=True)

    except pexpect.EOF:
        print("\n[record_demo] Process ended")

    finally:
        if child.isalive():
            child.close(force=True)

    # Verify output
    if os.path.exists(output_path):
        size = os.path.getsize(output_path)
        print(f"\n[record_demo] Recording saved: {output_path} ({size:,} bytes)")
        return True
    else:
        print(f"\n[record_demo] ERROR: Output file not created: {output_path}")
        return False


def _type_slowly(child, text: str, delay: float = 0.04):
    """Type text character by character for realistic terminal effect."""
    for char in text:
        child.send(char)
        time.sleep(delay)


def main():
    parser = argparse.ArgumentParser(description="Record Nova Forge demo session")
    parser.add_argument("--output", "-o", default=DEFAULT_OUTPUT, help="Output .cast file path")
    parser.add_argument("--model", "-m", default="nova-lite", help="Model to use for build")
    args = parser.parse_args()

    success = record_demo(args.output, args.model)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
