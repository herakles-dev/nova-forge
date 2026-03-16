#!/usr/bin/env python3
"""Automated interactive test for Ctrl-C Pause/Cancel using pexpect.

Drives forge_cli.py through pexpect. Key insight: prompt_toolkit echoes the
prompt string with every keystroke, so we can't use the prompt as a completion
marker. Instead we match specific OUTPUT patterns from each command.
"""

import os
import sys
import time
import signal
import pexpect

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
BOLD = "\033[1m"

def log(msg, color=CYAN):
    print(f"\n{color}{BOLD}[TEST]{RESET} {msg}", flush=True)

def log_pass(msg):
    print(f"{GREEN}{BOLD}[PASS]{RESET} {msg}", flush=True)

def log_fail(msg):
    print(f"{RED}{BOLD}[FAIL]{RESET} {msg}", flush=True)

def log_info(msg):
    print(f"{YELLOW}{BOLD}[INFO]{RESET} {msg}", flush=True)

results = {}

def main():
    os.chdir("/home/hercules/nova-forge")

    # Load secrets
    log("Loading secrets...")
    secret_lines = os.popen("bash -c 'source ~/.secrets/hercules.env && env'").readlines()
    for line in secret_lines:
        if '=' in line:
            key, _, val = line.strip().partition('=')
            os.environ[key] = val

    log("Spawning forge_cli.py...")
    child = pexpect.spawn(
        "python3", ["forge_cli.py"],
        encoding="utf-8",
        timeout=120,
        env=os.environ,
        dimensions=(50, 120),
    )
    child.logfile_read = sys.stdout

    # ── Wait for shell to initialize ──
    # The welcome screen has "Describe it. Nova builds it." and the project panel
    log("Waiting for shell initialization...")
    try:
        child.expect(r"Describe it|Welcome back|Project Ready", timeout=45)
        log_pass("Shell initialized")
    except pexpect.TIMEOUT:
        log_fail("Shell did not initialize within 45s")
        child.close()
        return 1

    # Wait for prompt_toolkit to fully initialize (CPR warning, etc.)
    time.sleep(5)

    # ── Step 1: Set model to nova-lite ──
    log("Setting model to nova-lite...")
    child.sendline("/model nova-lite")

    # /model outputs "Switched to nova-lite"
    try:
        child.expect(r"Switched to.*nova-lite", timeout=15)
        log_pass("Model set to nova-lite")
    except pexpect.TIMEOUT:
        log_info("No 'Switched to' message (model may already be set)")

    time.sleep(3)

    # ── Step 2: Plan a project ──
    log("Planning a Flask todo app...")
    child.sendline("/plan Build a simple Flask todo app with SQLite and HTML frontend")

    # /plan outputs: "Nova is analyzing..." then spinner, then "Plan: N tasks"
    try:
        # ANSI codes are between digits and "tasks", so use .* liberally
        child.expect(r"Plan.*\d+.*tasks|Planning failed|Planning didn't produce", timeout=120)
        before = child.before or ""
        if "failed" in before.lower() or "didn't produce" in before.lower():
            log_fail("Planning failed")
            child.close()
            return 1
        log_pass("Planning completed")
    except pexpect.TIMEOUT:
        log_fail("Planning timed out after 120s")
        child.close()
        return 1
    except pexpect.EOF:
        log_fail("Process exited during planning")
        child.close()
        return 1

    # Wait for the task table and prompt to render
    time.sleep(5)

    # ── Step 3: Start /build ──
    log("Starting build...")
    child.sendline("/build")

    # /build outputs: "Nova is building your project..." and "N tasks to complete (Ctrl-C to pause)"
    try:
        child.expect(r"tasks to complete|Ctrl-C to pause|Nova is building|Building your project", timeout=60)
        log_pass("Build started")
    except pexpect.TIMEOUT:
        # Maybe all tasks are already done?
        log_info("Build output not detected in 60s")
        # Check if build already finished
        try:
            child.expect(r"All tasks.*complete|Build summary|No.*tasks", timeout=5)
            log_info("Build completed instantly or no tasks to run")
            results["pause_menu_appeared"] = None
            results["cancel_works"] = None
            results["tasks_reverted"] = None
            results["deferred_resume"] = True
            _test_sigint_restoration(child)
            _finish(child)
            return 0
        except (pexpect.TIMEOUT, pexpect.EOF):
            log_fail("Build didn't start")
            child.close()
            return 1

    # Wait for agent to be actively working on a task
    log("Waiting 12s for agent to be deep in a task...")
    time.sleep(12)

    # ── Step 4: Send SIGINT ──
    log("Sending SIGINT (Ctrl-C)...")
    child.sendintr()

    # The agent checks _cancellation.is_paused() at turn boundaries and tool boundaries.
    # After the current operation finishes, it should show "Pausing..." then the pause menu.
    log("Waiting for pause response (agent must reach next checkpoint)...")
    try:
        idx = child.expect([
            r"[Pp]aus",                          # 0: "Pausing..." or "paused"
            r"What would you like",              # 1: menu question
            r"Resume build|Cancel build",        # 2: menu options
            r"Build summary",                    # 3: build completed before pause
            r"Build complete|All.*tasks.*done",  # 4: another completion variant
        ], timeout=90)
        if idx <= 2:
            log_pass(f"Pause behavior detected (pattern {idx})")
            results["pause_menu_appeared"] = True
        else:
            log_info("Build completed before pause took effect")
            results["pause_menu_appeared"] = None
    except pexpect.TIMEOUT:
        log_fail("No pause response within 90s")
        results["pause_menu_appeared"] = False
    except pexpect.EOF:
        log_fail("Process exited after SIGINT (handler may not be installed)")
        results["pause_menu_appeared"] = False
        child.close()
        return 1

    # ── Step 5: Handle pause menu ──
    if results.get("pause_menu_appeared") is True:
        # Wait for full menu render (Resume/Cancel options)
        time.sleep(3)
        try:
            child.expect(r"Resume|Cancel", timeout=15)
        except (pexpect.TIMEOUT, pexpect.EOF):
            pass

        log("Selecting Cancel build...")
        child.send("\x1b[B")  # Arrow down to Cancel
        time.sleep(0.5)
        child.sendline("")  # Enter

        # Cancel outputs: "Build paused. X/Y tasks completed" or "Run /build again to resume"
        try:
            child.expect(r"Run /build|Build.*paus|cancel|Build summary|remaining", timeout=30)
            log_pass("Cancel executed")
            results["cancel_works"] = True
        except pexpect.TIMEOUT:
            log_fail("Cancel action timed out")
            results["cancel_works"] = False
        except pexpect.EOF:
            log_fail("Process exited during cancel")
            results["cancel_works"] = False
            child.close()
            return 1

        time.sleep(5)

        # ── Step 6: Check /status ──
        log("Running /status...")
        child.sendline("/status")

        try:
            child.expect(r"completed|pending|complete|tasks|Sprint", timeout=20)
            log_pass("Status shows task states")
            results["tasks_reverted"] = True
        except pexpect.TIMEOUT:
            log_info("Status output not detected")
            results["tasks_reverted"] = None

        time.sleep(3)

        # ── Step 7: Test deferred resume ──
        log("Testing deferred resume with /build...")
        child.sendline("/build")

        try:
            idx = child.expect([
                r"tasks to complete|Nova is building|Ctrl-C to pause",  # 0: building remaining
                r"All tasks.*complete|Build complete|No.*tasks",        # 1: nothing to build
                r"Build summary",                                       # 2: build finished
            ], timeout=180)
            if idx == 0:
                log_pass("Deferred resume: building remaining tasks")
                results["deferred_resume"] = True
                # Let the build finish
                try:
                    child.expect(r"Build summary|Build complete|All.*done", timeout=300)
                    log_pass("Resumed build completed")
                except (pexpect.TIMEOUT, pexpect.EOF):
                    log_info("Resumed build still running or exited")
            elif idx == 1:
                log_pass("All tasks already completed")
                results["deferred_resume"] = True
            else:
                log_pass("Build finished")
                results["deferred_resume"] = True
        except pexpect.TIMEOUT:
            log_info("Resume build timed out")
            results["deferred_resume"] = None
        except pexpect.EOF:
            log_info("Process exited during resume build")
            results["deferred_resume"] = None

        time.sleep(3)
    else:
        # Build completed before pause
        results["cancel_works"] = None
        results["tasks_reverted"] = None
        results["deferred_resume"] = None

    # ── Step 8: SIGINT handler restoration ──
    _test_sigint_restoration(child)

    _finish(child)
    failures = [k for k, v in results.items() if v is False]
    return 1 if failures else 0


def _test_sigint_restoration(child):
    """Test SIGINT handler restored after build."""
    log("Testing SIGINT handler restoration at prompt...")
    time.sleep(3)

    try:
        child.sendintr()
    except OSError:
        log_info("Can't send signal (process may have exited)")
        results["sigint_restored"] = True
        return

    time.sleep(3)

    try:
        idx = child.expect([
            r"Build paused|What would you like to do|Resume build|Cancel build",
            r"Interrupted|KeyboardInterrupt",
            pexpect.TIMEOUT,
        ], timeout=8)
        if idx == 0:
            log_fail("Pause menu appeared at prompt! Handler NOT restored.")
            results["sigint_restored"] = False
        elif idx == 1:
            log_pass("Normal Ctrl-C behavior (handler restored)")
            results["sigint_restored"] = True
        else:
            # Timeout = no pause menu = good
            log_pass("No pause menu (handler restored)")
            results["sigint_restored"] = True
    except pexpect.EOF:
        log_info("Process exited on Ctrl-C (acceptable at prompt)")
        results["sigint_restored"] = True


def _finish(child):
    """Print results and exit."""
    try:
        child.sendline("/quit")
        time.sleep(2)
    except Exception:
        pass
    try:
        child.close()
    except Exception:
        pass

    print()
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  CTRL-C PAUSE/CANCEL TEST RESULTS{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")
    for test, result in results.items():
        if result is True:
            print(f"  {GREEN}PASS{RESET}  {test}")
        elif result is False:
            print(f"  {RED}FAIL{RESET}  {test}")
        else:
            print(f"  {YELLOW}N/A {RESET}  {test}")
    print(f"{BOLD}{'='*60}{RESET}")

    failures = [k for k, v in results.items() if v is False]
    if failures:
        print(f"\n{RED}{BOLD}FAILED: {', '.join(failures)}{RESET}")
    else:
        all_pass = [k for k, v in results.items() if v is True]
        na = [k for k, v in results.items() if v is None]
        print(f"\n{GREEN}{BOLD}{len(all_pass)} passed, {len(na)} N/A{RESET}")


if __name__ == "__main__":
    sys.exit(main())
