#!/usr/bin/env python3
"""Post-process an asciinema .cast file for the Nova Forge demo.

Transformations:
  1. Cap idle gaps: Any pause >2s compressed to max_idle (default 1s)
  2. Speed multiplier: Configurable overall speed (default 1.5x)
  3. Strip secrets: Remove AWS keys, API keys, tokens
  4. Total duration: Report final duration
  5. Output: Write cleaned .cast file

Usage:
    python3 scripts/postprocess_cast.py web/demo.cast
    python3 scripts/postprocess_cast.py web/demo.cast --output web/demo-clean.cast
    python3 scripts/postprocess_cast.py web/demo.cast --max-idle 1.0 --speed 1.5
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Patterns that might contain secrets
SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),                    # AWS Access Key ID
    re.compile(r"[A-Za-z0-9/+=]{40}"),                   # AWS Secret Key (40 char base64)
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),                  # OpenAI/Anthropic API keys
    re.compile(r"key-[a-zA-Z0-9]{20,}"),                 # Generic API keys
    re.compile(r"(AWS_SECRET_ACCESS_KEY|AWS_ACCESS_KEY_ID|OPENROUTER_API_KEY|ANTHROPIC_API_KEY)=[^\s]+"),
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*"),      # Bearer tokens
]


def strip_secrets(text: str) -> str:
    """Remove any potential secrets from output text."""
    result = text
    for pattern in SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def postprocess(input_path: str, output_path: str | None = None,
                max_idle: float = 1.0, speed: float = 1.5) -> dict:
    """Process a .cast file and return stats."""
    path = Path(input_path)
    if not path.exists():
        print(f"ERROR: File not found: {input_path}")
        sys.exit(1)

    lines = path.read_text().splitlines()
    if not lines:
        print("ERROR: Empty .cast file")
        sys.exit(1)

    # Parse header (first line is JSON object)
    try:
        header = json.loads(lines[0])
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid header: {e}")
        sys.exit(1)

    # Parse events (remaining lines are [timestamp, type, data])
    events = []
    for i, line in enumerate(lines[1:], start=2):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            events.append(event)
        except json.JSONDecodeError:
            print(f"WARNING: Skipping invalid line {i}: {line[:80]}")

    if not events:
        print("ERROR: No events found in .cast file")
        sys.exit(1)

    original_duration = events[-1][0] if events else 0
    stats = {
        "original_events": len(events),
        "original_duration": original_duration,
        "gaps_capped": 0,
        "secrets_found": 0,
    }

    # Pass 1: Strip secrets from output events
    for event in events:
        if len(event) >= 3 and event[1] == "o":
            cleaned = strip_secrets(event[2])
            if cleaned != event[2]:
                stats["secrets_found"] += 1
                event[2] = cleaned

    # Pass 2: Cap idle gaps and apply speed multiplier
    adjusted_events = []
    prev_time = 0.0

    for event in events:
        timestamp = event[0]
        gap = timestamp - prev_time

        # Cap long idle gaps
        if gap > max_idle:
            stats["gaps_capped"] += 1
            gap = max_idle

        # Apply speed multiplier
        gap = gap / speed

        new_time = (adjusted_events[-1][0] if adjusted_events else 0) + gap
        adjusted_events.append([round(new_time, 6)] + event[1:])
        prev_time = timestamp

    # Update header duration
    final_duration = adjusted_events[-1][0] if adjusted_events else 0
    header["duration"] = final_duration
    stats["final_duration"] = final_duration
    stats["final_events"] = len(adjusted_events)

    # Write output
    out_path = output_path or input_path
    output_lines = [json.dumps(header)]
    for event in adjusted_events:
        output_lines.append(json.dumps(event))

    Path(out_path).write_text("\n".join(output_lines) + "\n")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Post-process asciinema .cast file")
    parser.add_argument("input", help="Input .cast file path")
    parser.add_argument("--output", "-o", help="Output path (default: overwrite input)")
    parser.add_argument("--max-idle", type=float, default=1.0,
                        help="Max idle gap in seconds (default: 1.0)")
    parser.add_argument("--speed", type=float, default=1.5,
                        help="Speed multiplier (default: 1.5)")
    args = parser.parse_args()

    print(f"Processing: {args.input}")
    print(f"  Max idle: {args.max_idle}s")
    print(f"  Speed: {args.speed}x")

    stats = postprocess(args.input, args.output, args.max_idle, args.speed)

    print(f"\nResults:")
    print(f"  Events: {stats['original_events']} -> {stats['final_events']}")
    print(f"  Duration: {stats['original_duration']:.1f}s -> {stats['final_duration']:.1f}s")
    print(f"  Gaps capped: {stats['gaps_capped']}")
    print(f"  Secrets redacted: {stats['secrets_found']}")
    print(f"  Output: {args.output or args.input}")


if __name__ == "__main__":
    main()
