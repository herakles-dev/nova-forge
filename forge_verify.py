"""Nova Forge Build Verifier — runtime verification with browser automation.

After agents build a project, the verifier:
  1. Starts the app (via PreviewManager)
  2. Runs syntax/import checks on generated code
  3. Launches a headless browser to verify the UI loads
  4. Tests API endpoints respond correctly
  5. Captures screenshots as evidence
  6. Returns a structured verdict

Integrated into the build pipeline as a post-build gate that runs
AFTER the LLM gate review. The LLM reviews code quality; this verifier
tests that the app actually works at runtime.
"""

from __future__ import annotations

import asyncio
import json
import logging
import py_compile
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("forge.verify")

__all__ = ["BuildVerifier", "VerifyResult", "Check"]


# ── Result types ────────────────────────────────────────────────────────────

@dataclass
class Check:
    """A single verification check."""
    name: str
    passed: bool
    detail: str = ""
    evidence_path: str | None = None   # screenshot path


@dataclass
class VerifyResult:
    """Aggregated verification result."""
    status: str = "pass"  # "pass" | "fail" | "partial"
    checks: list[Check] = field(default_factory=list)
    screenshot_dir: str | None = None

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def failed(self) -> int:
        return sum(1 for c in self.checks if not c.passed)

    @property
    def summary(self) -> str:
        return f"{self.passed}/{len(self.checks)} checks passed"

    def add(self, name: str, passed: bool, detail: str = "", evidence: str | None = None):
        self.checks.append(Check(name=name, passed=passed, detail=detail, evidence_path=evidence))
        # Update status
        if not passed:
            self.status = "fail" if self.failed > len(self.checks) // 2 else "partial"


# ── Build Verifier ──────────────────────────────────────────────────────────

class BuildVerifier:
    """Runtime verification of generated projects.

    Uses three verification layers:
      L1: Static — syntax checks, import resolution
      L2: Server — app starts, ports respond
      L3: Browser — UI loads, elements present, API responds
    """

    def __init__(self, project_path: Path, spec_text: str = ""):
        self.project_path = Path(project_path).resolve()
        self.spec_text = spec_text
        self._screenshot_dir = self.project_path / ".forge" / "verify"

    async def verify(self, tasks: list[Any] = None) -> VerifyResult:
        """Run all verification layers. Returns structured result.

        Args:
            tasks: Optional list of completed task objects for context.
        """
        result = VerifyResult(screenshot_dir=str(self._screenshot_dir))

        # L1: Static checks
        self._check_syntax(result)
        self._check_imports(result)
        self._check_file_references(result)

        # L2: Server check — start the app
        server_ok, port, preview_mgr = await self._check_server(result)

        # L2.5: Root route check — GET / must return 200
        if server_ok and port:
            status = await self._check_http_status(port, "/")
            if 200 <= status < 400:
                result.add("root_route", True, f"GET / returned {status}")
            else:
                detail = self._diagnose_root_404(status)
                result.add("root_route", False, detail)

        # L3: Browser checks (only if server started)
        if server_ok and port:
            await self._check_browser(result, port, tasks)

        # L4: Functional tests (only if server started and L3 passed)
        if server_ok and port:
            await self._verify_functional(result, port, tasks)

        # Cleanup server
        if preview_mgr:
            preview_mgr.stop()

        return result

    # ── L1: Static checks ───────────────────────────────────────────────

    def _check_syntax(self, result: VerifyResult) -> None:
        """Verify all Python files compile without syntax errors."""
        py_files = sorted(self.project_path.glob("**/*.py"))
        py_files = [f for f in py_files if not _skip_path(f, self.project_path)]

        if not py_files:
            return

        errors = []
        for f in py_files:
            try:
                py_compile.compile(str(f), doraise=True)
            except py_compile.PyCompileError as e:
                errors.append(f"{f.name}: {e}")

        if errors:
            result.add("python_syntax", False, f"{len(errors)} file(s) with syntax errors: {'; '.join(errors[:3])}")
        else:
            result.add("python_syntax", True, f"{len(py_files)} Python files OK")

    def _check_imports(self, result: VerifyResult) -> None:
        """Verify that intra-project imports resolve to existing files and names."""
        py_files = sorted(self.project_path.glob("*.py"))
        py_files = [f for f in py_files if not _skip_path(f, self.project_path)]

        if not py_files:
            return

        # Build a map of available modules: stem -> Path
        available_modules = {f.stem: f for f in py_files}

        broken = []
        broken_names = []
        for f in py_files:
            try:
                src = f.read_text()
            except Exception:
                continue
            for match in re.finditer(r'^\s*(?:from|import)\s+(\w+)', src, re.MULTILINE):
                mod = match.group(1)
                # Only check intra-project imports (not stdlib/pip)
                if mod in available_modules or mod in _STDLIB_MODULES:
                    continue
                # Check if it might be an installed package
                try:
                    __import__(mod)
                except ImportError:
                    broken.append(f"{f.name} → {mod}")

            # Check that specific imported names exist in intra-project modules
            for match in re.finditer(r'from\s+(\w+)\s+import\s+(.+?)(?:\s*#.*)?$', src, re.MULTILINE):
                mod, names_str = match.group(1), match.group(2)
                if mod not in available_modules:
                    continue
                imported_names = [n.strip().split(' as ')[0].strip() for n in names_str.split(',')]
                defined = _get_module_exports(available_modules[mod])
                for name in imported_names:
                    if name and name != '*' and name not in defined:
                        broken_names.append(f"{f.name}: '{name}' not in {mod}.py")

        all_broken = broken + broken_names
        if all_broken:
            result.add("imports", False, f"Unresolved imports: {'; '.join(all_broken[:8])}")
        else:
            result.add("imports", True, "All intra-project imports resolve")

    def _check_file_references(self, result: VerifyResult) -> None:
        """Verify that code file references (templates, static files) resolve to actual files."""
        mismatches = scan_file_references(self.project_path)
        py_files = sorted(self.project_path.glob("**/*.py"))
        py_files = [f for f in py_files if not _skip_path(f, self.project_path)]

        if mismatches:
            result.add("file_references", False, "; ".join(mismatches[:5]))
        elif py_files:
            result.add("file_references", True, "All file references resolve")

    def _diagnose_root_404(self, status: int) -> str:
        """Diagnose why GET / failed and suggest the fix."""
        if status == 404:
            # Check for common misplacements
            has_templates_index = (self.project_path / "templates" / "index.html").exists()
            has_static_index = (self.project_path / "static" / "index.html").exists()

            # Read app.py to see what the route does
            app_py = self.project_path / "app.py"
            route_uses = ""
            if app_py.exists():
                try:
                    src = app_py.read_text()
                    if "send_static_file" in src and has_templates_index and not has_static_index:
                        return (
                            f"GET / returned 404: app.py uses send_static_file() "
                            f"but index.html is in templates/ not static/"
                        )
                    if "render_template" in src and has_static_index and not has_templates_index:
                        return (
                            f"GET / returned 404: app.py uses render_template() "
                            f"but index.html is in static/ not templates/"
                        )
                except Exception:
                    pass

            if has_templates_index or has_static_index:
                return f"GET / returned 404: index.html exists but route is misconfigured"
            return f"GET / returned 404: no root route or missing index.html"
        elif status == 500:
            return f"GET / returned 500: server error — check app.py for runtime bugs"
        elif status == 0:
            return "GET / failed: no HTTP response from server"
        else:
            return f"GET / returned {status}"

    # ── L2: Server check ────────────────────────────────────────────────

    async def _check_server(self, result: VerifyResult) -> tuple[bool, int | None, Any]:
        """Start the app server and verify it responds."""
        from forge_preview import PreviewManager, PreviewError, detect_stack

        si = detect_stack(self.project_path)
        if si.kind == "unknown":
            result.add("server_start", False,
                       "No servable entry point detected. Supported: Streamlit, FastAPI, "
                       "Flask, Django, Next.js, Vite, Node.js, Go, Rust, Rails, PHP, Docker, Static.")
            return False, None, None

        mgr = PreviewManager(self.project_path)
        try:
            # Start server only (we don't need the tunnel for verification)
            mgr._start_server_only(si)
            port = si.port

            # Verify HTTP response
            ok = await self._check_http(port)
            if ok:
                result.add("server_start", True, f"{si.kind} app listening on port {port}")
            else:
                result.add("server_start", False, f"Server started but port {port} not responding to HTTP")
                return False, None, mgr

            return True, port, mgr

        except PreviewError as e:
            result.add("server_start", False, str(e))
            return False, None, None

    async def _check_http(self, port: int, path: str = "/", timeout: float = 5.0) -> bool:
        """Send a simple HTTP GET and check for any response (server is alive)."""
        import urllib.request
        import urllib.error

        url = f"http://localhost:{port}{path}"
        try:
            loop = asyncio.get_running_loop()
            req = urllib.request.Request(url, headers={"User-Agent": "NovaForge-Verify/1.0"})
            resp = await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(req, timeout=timeout)
            )
            return 200 <= resp.status < 400
        except urllib.error.HTTPError as e:
            # 4xx/5xx is still a running server
            return e.code < 500
        except Exception:
            return False

    async def _check_http_status(self, port: int, path: str = "/", timeout: float = 5.0) -> int:
        """Send HTTP GET and return the actual status code (0 on connection error)."""
        import urllib.request
        import urllib.error

        url = f"http://localhost:{port}{path}"
        try:
            loop = asyncio.get_running_loop()
            req = urllib.request.Request(url, headers={"User-Agent": "NovaForge-Verify/1.0"})
            resp = await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(req, timeout=timeout)
            )
            return resp.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:
            return 0

    # ── L3: Browser checks ──────────────────────────────────────────────

    async def _check_browser(self, result: VerifyResult, port: int, tasks: list[Any] = None) -> None:
        """Launch headless browser to verify UI and API."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            result.add("browser", False, "Playwright not installed — skipping browser checks")
            return

        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

        base_url = f"http://localhost:{port}"

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    ignore_https_errors=True,
                )
                page = await context.new_page()

                # Check 1: Page loads
                await self._verify_page_loads(page, base_url, result)

                # Check 2: No console errors
                await self._verify_no_critical_errors(page, base_url, result)

                # Check 3: UI elements present (from spec)
                await self._verify_ui_elements(page, base_url, result)

                # Check 4: API endpoints (if detected)
                await self._verify_api_endpoints(page, base_url, result)

                # Check 5: Form interaction (if forms exist)
                await self._verify_forms(page, base_url, result)

                # Final screenshot
                screenshot_path = str(self._screenshot_dir / "final.png")
                await page.screenshot(path=screenshot_path, full_page=True)

                await browser.close()

        except Exception as e:
            result.add("browser", False, f"Browser automation failed: {e}")

    async def _verify_page_loads(self, page: Any, base_url: str, result: VerifyResult) -> None:
        """Verify the main page loads without network errors."""
        errors = []
        page.on("pageerror", lambda err: errors.append(str(err)))

        try:
            resp = await page.goto(base_url, wait_until="domcontentloaded", timeout=10000)
            status = resp.status if resp else 0

            if 200 <= status < 400:
                title = await page.title()
                screenshot_path = str(self._screenshot_dir / "page_load.png")
                await page.screenshot(path=screenshot_path)
                result.add("page_loads", True, f"Status {status}, title: '{title}'", screenshot_path)
            else:
                result.add("page_loads", False, f"HTTP {status}")
        except Exception as e:
            result.add("page_loads", False, f"Navigation failed: {e}")

    async def _verify_no_critical_errors(self, page: Any, base_url: str, result: VerifyResult) -> None:
        """Check for critical JavaScript errors in console."""
        console_errors = []

        def on_console(msg):
            if msg.type == "error":
                text = msg.text
                # Ignore common benign errors
                if not any(skip in text for skip in ("favicon.ico", "manifest.json")):
                    console_errors.append(text)

        page.on("console", on_console)

        # Reload to capture errors from fresh load
        try:
            await page.reload(wait_until="networkidle", timeout=10000)
        except Exception:
            await page.reload(wait_until="domcontentloaded", timeout=10000)

        # Wait a moment for async JS
        await page.wait_for_timeout(1000)

        page.remove_listener("console", on_console)

        if console_errors:
            detail = "; ".join(console_errors[:3])
            result.add("js_errors", False, f"{len(console_errors)} console error(s): {detail}")
        else:
            result.add("js_errors", True, "No critical JS console errors")

    async def _verify_ui_elements(self, page: Any, base_url: str, result: VerifyResult) -> None:
        """Verify expected UI elements exist based on spec keywords."""
        checks = []

        # Extract expected UI elements from spec
        expected = _extract_ui_expectations(self.spec_text)

        if not expected:
            # Fallback: check for basic elements any web app should have
            expected = [
                ("heading", "h1, h2, h3, [role='heading']"),
                ("interactive", "button, input, a[href], select, textarea"),
            ]

        for name, selector in expected:
            try:
                count = await page.locator(selector).count()
                if count > 0:
                    checks.append((name, True, f"{count} element(s) found"))
                else:
                    checks.append((name, False, f"No elements matching: {selector}"))
            except Exception as e:
                checks.append((name, False, f"Selector error: {e}"))

        passed_count = sum(1 for _, ok, _ in checks if ok)
        if passed_count == len(checks):
            result.add("ui_elements", True, f"All {len(checks)} UI checks passed")
        else:
            failed_details = [f"{n}: {d}" for n, ok, d in checks if not ok]
            result.add("ui_elements", False, f"{len(checks) - passed_count} missing: {'; '.join(failed_details[:3])}")

    async def _verify_api_endpoints(self, page: Any, base_url: str, result: VerifyResult) -> None:
        """Test API endpoints detected from the spec or source code."""
        endpoints = _extract_api_endpoints(self.spec_text, self.project_path)

        if not endpoints:
            return  # No API endpoints to test

        passed = 0
        failed_details = []

        for method, path in endpoints[:8]:  # Cap at 8 endpoints
            url = f"{base_url}{path}"
            try:
                if method.upper() == "GET":
                    resp = await page.request.get(url)
                elif method.upper() == "POST":
                    resp = await page.request.post(url, data="{}", headers={"Content-Type": "application/json"})
                else:
                    continue

                # 2xx, 3xx, or even 4xx (validation error) means the endpoint exists
                if resp.status < 500:
                    passed += 1
                else:
                    failed_details.append(f"{method} {path} → {resp.status}")
            except Exception as e:
                failed_details.append(f"{method} {path} → {e}")

        total = passed + len(failed_details)
        if failed_details:
            result.add("api_endpoints", False, f"{passed}/{total} OK; failures: {'; '.join(failed_details[:3])}")
        else:
            result.add("api_endpoints", True, f"{passed}/{total} API endpoints responding")

    async def _verify_forms(self, page: Any, base_url: str, result: VerifyResult) -> None:
        """Test that forms are interactive (can type + submit without crash)."""
        try:
            forms = await page.locator("form").count()
            if forms == 0:
                return  # No forms to test

            # Try to interact with the first form
            first_form = page.locator("form").first

            # Find text inputs in the form
            inputs = first_form.locator("input[type='text'], input:not([type]), textarea")
            input_count = await inputs.count()

            if input_count > 0:
                # Type test data into the first input
                first_input = inputs.first
                await first_input.fill("Nova Forge Test")

                # Try to submit (click submit button if exists)
                submit_btn = first_form.locator("button[type='submit'], input[type='submit'], button:not([type])")
                submit_count = await submit_btn.count()

                if submit_count > 0:
                    # Use request interception to track responses
                    responses = []

                    def capture_response(resp):
                        try:
                            responses.append(resp.status)
                        except Exception:
                            pass

                    page.on("response", capture_response)

                    await submit_btn.first.click()
                    await page.wait_for_timeout(2000)

                    page.remove_listener("response", capture_response)

                    server_errors = [s for s in responses if isinstance(s, int) and s >= 500]
                    if server_errors:
                        screenshot_path = str(self._screenshot_dir / "form_error.png")
                        await page.screenshot(path=screenshot_path)
                        result.add("form_interaction", False, f"Form submit caused server error(s): {server_errors}", screenshot_path)
                    else:
                        screenshot_path = str(self._screenshot_dir / "form_submit.png")
                        await page.screenshot(path=screenshot_path)
                        result.add("form_interaction", True, f"Form submit OK ({len(responses)} responses)", screenshot_path)
                else:
                    result.add("form_interaction", True, f"Form has {input_count} inputs (no submit button to test)")
            else:
                result.add("form_interaction", True, f"{forms} form(s) found (no text inputs)")

        except Exception as e:
            result.add("form_interaction", False, f"Form test error: {e}")


    # ── L4: Functional tests ────────────────────────────────────────────

    async def _verify_functional(self, result: VerifyResult, port: int, tasks: list[Any] = None) -> None:
        """L4 functional verification: test that buttons work, forms submit, data round-trips.

        Goes beyond L3 (elements exist) to verify actual behavior:
        1. Click every button → verify DOM changes
        2. Fill and submit forms → verify data appears
        3. POST data via API → GET it back → verify persistence
        4. Check JS event listeners are wired to elements
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return  # Playwright not available, skip L4

        base_url = f"http://localhost:{port}"
        functional_failures: list[str] = []

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    ignore_https_errors=True,
                )
                page = await context.new_page()

                try:
                    await page.goto(base_url, wait_until="domcontentloaded", timeout=10000)
                except Exception:
                    result.add("functional_test", False, "Could not load page for L4 tests")
                    await browser.close()
                    return

                # Wait for JS to initialize
                await page.wait_for_timeout(1000)

                # ── Test 1: Click every button and verify DOM changes ──────
                buttons = page.locator("button, input[type='submit'], [role='button']")
                btn_count = await buttons.count()
                btn_clicks_ok = 0
                btn_click_failures = []

                for i in range(min(btn_count, 10)):  # Cap at 10 buttons
                    try:
                        btn = buttons.nth(i)
                        # Skip hidden or disabled buttons
                        if not await btn.is_visible() or not await btn.is_enabled():
                            continue

                        btn_text = (await btn.text_content() or "").strip()[:30]
                        if not btn_text:
                            btn_text = await btn.get_attribute("aria-label") or f"button[{i}]"

                        # Snapshot DOM before click
                        dom_before = await page.evaluate("document.body.innerHTML.length")

                        # Listen for network activity
                        responses_before = []

                        def capture(resp):
                            try:
                                responses_before.append(resp.url)
                            except Exception:
                                pass

                        page.on("response", capture)

                        try:
                            await btn.click(timeout=3000)
                            await page.wait_for_timeout(1500)
                        except Exception:
                            pass  # Button click may navigate or cause dialog

                        page.remove_listener("response", capture)

                        # Check: did something change?
                        dom_after = await page.evaluate("document.body.innerHTML.length")
                        network_fired = len(responses_before) > 0

                        if dom_after != dom_before or network_fired:
                            btn_clicks_ok += 1
                        else:
                            btn_click_failures.append(f"'{btn_text}' — no DOM change or network activity")

                        # Navigate back to main page if button caused navigation
                        current_url = page.url
                        if not current_url.startswith(base_url) or current_url != base_url + "/":
                            try:
                                await page.goto(base_url, wait_until="domcontentloaded", timeout=5000)
                                await page.wait_for_timeout(500)
                            except Exception:
                                pass
                    except Exception:
                        continue

                if btn_count > 0:
                    tested = btn_clicks_ok + len(btn_click_failures)
                    if btn_click_failures:
                        detail = f"{btn_clicks_ok}/{tested} buttons responsive; dead: {'; '.join(btn_click_failures[:3])}"
                        functional_failures.append(detail)
                        result.add("button_functionality", False, detail)
                    elif tested > 0:
                        result.add("button_functionality", True, f"{btn_clicks_ok}/{tested} buttons trigger DOM/network changes")

                # ── Test 2: Form data round-trip ───────────────────────────
                forms = page.locator("form")
                form_count = await forms.count()

                if form_count > 0:
                    try:
                        first_form = forms.first
                        inputs = first_form.locator("input[type='text'], input:not([type]), textarea")
                        input_count = await inputs.count()

                        if input_count > 0:
                            test_value = f"NovaForge-L4-{int(time.time())}"
                            await inputs.first.fill(test_value)

                            # Submit via button or Enter
                            submit = first_form.locator("button[type='submit'], input[type='submit'], button:not([type])")
                            submit_count = await submit.count()

                            if submit_count > 0:
                                dom_before = await page.content()
                                await submit.first.click()
                                await page.wait_for_timeout(2000)
                                dom_after = await page.content()

                                # Check if our test data appears in the page
                                if test_value in dom_after:
                                    result.add("form_roundtrip", True, f"Form submit: data '{test_value[:20]}' appears in page")
                                elif dom_before != dom_after:
                                    result.add("form_roundtrip", True, "Form submit: DOM changed (data may use different format)")
                                else:
                                    detail = f"Form submit: no visible change after submitting '{test_value[:20]}'"
                                    functional_failures.append(detail)
                                    result.add("form_roundtrip", False, detail)
                    except Exception as e:
                        result.add("form_roundtrip", False, f"Form test error: {e}")

                # ── Test 3: API data round-trip (POST then GET) ────────────
                api_endpoints = _extract_api_endpoints(self.spec_text, self.project_path)
                post_endpoints = [(m, p) for m, p in api_endpoints if m.upper() == "POST"]
                get_endpoints = [(m, p) for m, p in api_endpoints if m.upper() == "GET"]

                if post_endpoints and get_endpoints:
                    try:
                        post_method, post_path = post_endpoints[0]
                        get_method, get_path = get_endpoints[0]

                        # POST test data
                        test_data = {"title": f"L4-test-{int(time.time())}", "name": "L4 test item"}
                        post_resp = await page.request.post(
                            f"{base_url}{post_path}",
                            data=json.dumps(test_data),
                            headers={"Content-Type": "application/json"},
                        )

                        if post_resp.status < 400:
                            # GET and verify data persisted
                            await page.wait_for_timeout(500)
                            get_resp = await page.request.get(f"{base_url}{get_path}")
                            if get_resp.status < 400:
                                try:
                                    body = await get_resp.text()
                                    if "L4-test" in body or "L4 test" in body:
                                        result.add("api_roundtrip", True,
                                                    f"POST {post_path} → GET {get_path}: data persisted")
                                    else:
                                        detail = f"POST {post_path} OK but data not found in GET {get_path}"
                                        functional_failures.append(detail)
                                        result.add("api_roundtrip", False, detail)
                                except Exception:
                                    result.add("api_roundtrip", False, f"Could not read GET {get_path} response")
                            else:
                                result.add("api_roundtrip", False, f"GET {get_path} returned {get_resp.status}")
                        else:
                            result.add("api_roundtrip", False,
                                        f"POST {post_path} returned {post_resp.status}")
                    except Exception as e:
                        result.add("api_roundtrip", False, f"API round-trip error: {e}")

                # ── Test 4: Check JS event listeners are wired ─────────────
                try:
                    unwired = await page.evaluate("""() => {
                        const interactive = document.querySelectorAll('button, [role="button"], input[type="submit"]');
                        const unwired = [];
                        for (const el of interactive) {
                            if (!el.disabled && el.offsetParent !== null) {
                                // Check for inline handlers or known framework bindings
                                const hasOnclick = el.onclick !== null;
                                const hasInlineHandler = el.hasAttribute('onclick');
                                const hasAriaAction = el.hasAttribute('data-action');
                                // getEventListeners is Chrome DevTools only, so we check indirect signals
                                const text = (el.textContent || '').trim();
                                if (!hasOnclick && !hasInlineHandler && !hasAriaAction && text) {
                                    unwired.push(text.substring(0, 30));
                                }
                            }
                        }
                        return unwired;
                    }""")

                    if unwired and len(unwired) > 0:
                        # This is a heuristic — addEventListener won't be caught by onclick check
                        # So only flag if >50% of buttons appear unwired
                        total_btns = await page.locator("button, [role='button']").count()
                        if len(unwired) > total_btns * 0.5 and total_btns > 1:
                            detail = f"{len(unwired)}/{total_btns} buttons may lack event handlers: {', '.join(unwired[:3])}"
                            result.add("js_handlers", False, detail)
                        else:
                            result.add("js_handlers", True, f"Event handler check passed ({total_btns} buttons)")
                    else:
                        result.add("js_handlers", True, "All interactive elements appear wired")
                except Exception:
                    pass  # JS evaluation failure is non-critical

                # Take L4 screenshot
                try:
                    self._screenshot_dir.mkdir(parents=True, exist_ok=True)
                    screenshot_path = str(self._screenshot_dir / "l4_functional.png")
                    await page.screenshot(path=screenshot_path, full_page=True)
                except Exception:
                    pass

                await browser.close()

        except Exception as e:
            result.add("functional_test", False, f"L4 functional test error: {e}")

        # Summary check
        if functional_failures:
            result.add("functional_summary", False,
                        f"{len(functional_failures)} functional issue(s): {'; '.join(functional_failures[:3])}")
        elif any(c.name.startswith(("button_", "form_", "api_")) for c in result.checks):
            result.add("functional_summary", True, "L4 functional tests passed")


# ── Helpers ─────────────────────────────────────────────────────────────────

_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".forge", ".venv", "venv", "artifacts"})


def _skip_path(path: Path, root: Path) -> bool:
    """Check if path should be skipped during scanning."""
    try:
        rel = path.relative_to(root)
        return any(part in _SKIP_DIRS for part in rel.parts)
    except ValueError:
        return True


def _get_module_exports(py_file: Path) -> set[str]:
    """Extract all public names defined in a Python module using AST."""
    try:
        import ast as _ast
        tree = _ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
        names: set[str] = set()
        for node in _ast.iter_child_nodes(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                names.add(node.name)
            elif isinstance(node, _ast.ClassDef):
                names.add(node.name)
            elif isinstance(node, _ast.Assign):
                for target in node.targets:
                    if isinstance(target, _ast.Name):
                        names.add(target.id)
        return names
    except Exception:
        return set()


# Common stdlib modules (partial list — enough to avoid false positives)
_STDLIB_MODULES = frozenset({
    "os", "sys", "re", "json", "time", "datetime", "hashlib", "pathlib",
    "subprocess", "threading", "asyncio", "logging", "unittest", "typing",
    "collections", "functools", "itertools", "io", "math", "random",
    "socket", "http", "urllib", "shutil", "tempfile", "glob", "csv",
    "sqlite3", "abc", "dataclasses", "enum", "copy", "traceback",
    "contextlib", "signal", "atexit", "weakref", "textwrap", "string",
    "struct", "base64", "uuid", "secrets", "hmac", "argparse", "pprint",
    "py_compile", "importlib", "inspect", "ast", "dis", "pickle",
    "shelve", "marshal", "dbm", "gzip", "zipfile", "tarfile",
})


def _extract_ui_expectations(spec_text: str) -> list[tuple[str, str]]:
    """Extract expected UI elements from spec text via keyword matching."""
    if not spec_text:
        return []

    expectations = []
    spec_lower = spec_text.lower()

    # Map spec keywords to CSS selectors
    keyword_map = {
        "form": ("form", "form"),
        "button": ("buttons", "button"),
        "input": ("text_inputs", "input[type='text'], input:not([type]), textarea"),
        "table": ("table", "table, div[id*='list'], div[class*='list'], #task-list"),
        "list": ("list", "ul, ol, [role='list'], [id*='list'], [class*='list'], #task-list"),
        "navigation": ("nav", "nav, [role='navigation']"),
        "header": ("header", "header, h1, h2"),
        "task": ("task_elements", ".task, [data-task], li, tr, div[id*='task'], #task-list, section"),
        "dropdown": ("dropdown", "select, [role='combobox'], [role='listbox']"),
        "delete": ("delete_button", "button:has-text('delete'), button:has-text('Delete'), button:has-text('Remove'), [aria-label*='delete'], [class*='delete']"),
        "search": ("search", "input[type='search'], [role='search'], input[placeholder*='search' i]"),
    }

    for keyword, (name, selector) in keyword_map.items():
        if keyword in spec_lower:
            expectations.append((name, selector))

    return expectations


def _extract_api_endpoints(spec_text: str, project_path: Path) -> list[tuple[str, str]]:
    """Extract API endpoints from spec text and source code."""
    endpoints = []

    # From spec: match patterns like "GET /api/tasks" or "POST /api/users"
    if spec_text:
        for match in re.finditer(r'(GET|POST|PUT|DELETE|PATCH)\s+(/\S+)', spec_text):
            method = match.group(1)
            path = match.group(2).rstrip(")")
            # Skip paths with <param> for now — just test parameterless ones
            if "<" not in path and "{" not in path:
                endpoints.append((method, path))

    # From source: look for Flask/Express route decorators
    if not endpoints:
        for py_file in project_path.glob("*.py"):
            try:
                src = py_file.read_text()
                for match in re.finditer(
                    r'@\w+\.route\(\s*["\']([^"\']+)["\'].*?methods\s*=\s*\[([^\]]+)\]',
                    src
                ):
                    path = match.group(1)
                    methods = match.group(2)
                    if "<" not in path:
                        for m in re.findall(r"['\"](\w+)['\"]", methods):
                            endpoints.append((m, path))

                # Also match simple @app.get / @app.post
                for match in re.finditer(r'@\w+\.(get|post|put|delete)\(\s*["\']([^"\']+)["\']', src):
                    method = match.group(1).upper()
                    path = match.group(2)
                    if "<" not in path:
                        endpoints.append((method, path))
            except Exception:
                continue

    return endpoints


# ── Public file reference scanner ────────────────────────────────────────────

def scan_file_references(project_path: Path) -> list[str]:
    """Scan project for cross-file reference mismatches.

    Returns a list of human-readable issue descriptions. Empty list = no issues.
    Used by both BuildVerifier and the build pipeline's integration check.
    """
    project_path = Path(project_path)
    py_files = sorted(project_path.glob("**/*.py"))
    py_files = [f for f in py_files if not _skip_path(f, project_path)]

    issues = []
    for f in py_files:
        try:
            src = f.read_text()
        except Exception:
            continue

        # Flask: render_template('x') → must exist in templates/
        for m in re.finditer(r"render_template\(\s*['\"]([^'\"]+)['\"]", src):
            ref = m.group(1)
            target = project_path / "templates" / ref
            if not target.exists():
                alt = project_path / "static" / ref
                if alt.exists():
                    issues.append(f"{f.name}: render_template('{ref}') but file is in static/, not templates/")
                else:
                    issues.append(f"{f.name}: render_template('{ref}') but templates/{ref} missing")

        # Flask: send_static_file('x') → must exist in static/
        for m in re.finditer(r"send_static_file\(\s*['\"]([^'\"]+)['\"]", src):
            ref = m.group(1)
            target = project_path / "static" / ref
            if not target.exists():
                alt = project_path / "templates" / ref
                if alt.exists():
                    issues.append(f"{f.name}: send_static_file('{ref}') but file is in templates/, not static/")
                else:
                    issues.append(f"{f.name}: send_static_file('{ref}') but static/{ref} missing")

        # url_for('static', filename='x') → must exist in static/
        for m in re.finditer(r"url_for\(\s*['\"]static['\"]\s*,\s*filename\s*=\s*['\"]([^'\"]+)['\"]", src):
            ref = m.group(1)
            target = project_path / "static" / ref
            if not target.exists():
                issues.append(f"{f.name}: url_for static '{ref}' but static/{ref} missing")

        # app.render_template() — common mistake (it's a standalone function)
        for m in re.finditer(r"app\.render_template\(", src):
            issues.append(f"{f.name}: app.render_template() is invalid — use render_template() (standalone function from flask)")

    # Check HTML templates for broken static references
    html_files = list(project_path.glob("templates/**/*.html")) + list(project_path.glob("static/**/*.html"))
    for f in html_files:
        try:
            src = f.read_text()
        except Exception:
            continue
        for m in re.finditer(r"url_for\(\s*['\"]static['\"]\s*,\s*filename\s*=\s*['\"]([^'\"]+)['\"]", src):
            ref = m.group(1)
            target = project_path / "static" / ref
            if not target.exists():
                issues.append(f"{f.name}: references static/{ref} but file missing")

    # Check ALL HTML files for broken <script src>, <link href>, <img src>
    _SKIP_PREFIXES = ("http://", "https://", "//", "data:", "#")
    _JINJA_MARKERS = ("{{", "{%")
    for html_f in project_path.glob("**/*.html"):
        if _skip_path(html_f, project_path):
            continue
        try:
            html_src = html_f.read_text(errors="replace")
        except Exception:
            continue
        html_dir = html_f.parent

        # <script src="X">
        for m in re.finditer(r'<script[^>]+src=["\']([^"\']+)["\']', html_src):
            ref = m.group(1)
            if any(ref.startswith(p) for p in _SKIP_PREFIXES) or any(mk in ref for mk in _JINJA_MARKERS):
                continue
            target = (html_dir / ref).resolve()
            static_fallback = project_path / "static" / ref
            if not target.exists() and not static_fallback.exists():
                issues.append(f"{html_f.name}: <script src=\"{ref}\"> but file not found")

        # <link ... href="X"> (CSS)
        for m in re.finditer(r'<link[^>]+href=["\']([^"\']+)["\']', html_src):
            ref = m.group(1)
            if any(ref.startswith(p) for p in _SKIP_PREFIXES) or any(mk in ref for mk in _JINJA_MARKERS):
                continue
            target = (html_dir / ref).resolve()
            static_fallback = project_path / "static" / ref
            if not target.exists() and not static_fallback.exists():
                issues.append(f"{html_f.name}: <link href=\"{ref}\"> but file not found")

        # <img src="X">
        for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', html_src):
            ref = m.group(1)
            if any(ref.startswith(p) for p in _SKIP_PREFIXES) or any(mk in ref for mk in _JINJA_MARKERS):
                continue
            target = (html_dir / ref).resolve()
            static_fallback = project_path / "static" / ref
            if not target.exists() and not static_fallback.exists():
                issues.append(f"{html_f.name}: <img src=\"{ref}\"> but file not found")

    # Also check for HTML/JS ID mismatches
    id_issues = scan_id_mismatches(project_path)
    issues.extend(id_issues)

    return issues


def scan_id_mismatches(project_path: Path) -> list[str]:
    """Detect HTML element IDs referenced in JS but missing from HTML, and vice versa.

    Parses HTML for id="..." attributes, JS for getElementById/querySelector("#...").
    Reports mismatches with fuzzy-match suggestions.
    """
    from difflib import get_close_matches

    project_path = Path(project_path)
    issues: list[str] = []

    # Collect HTML IDs from all HTML files
    html_ids: set[str] = set()
    html_files = (
        list(project_path.glob("**/*.html"))
    )
    html_files = [f for f in html_files if not _skip_path(f, project_path)]
    for f in html_files:
        try:
            src = f.read_text()
        except Exception:
            continue
        for m in re.finditer(r'\bid=["\']([^"\']+)["\']', src):
            html_ids.add(m.group(1))

    if not html_ids:
        return issues  # No HTML IDs to cross-reference

    # Collect JS ID references from all JS files and inline scripts
    # Track (filename, id) so issues include the source file for scoping
    js_refs: dict[str, set[str]] = {}  # id -> set of filenames
    js_files = list(project_path.glob("**/*.js"))
    js_files = [f for f in js_files if not _skip_path(f, project_path)]

    # Also extract inline <script> blocks from HTML
    js_sources: list[tuple[str, str]] = []  # (relative_path, source)
    for f in js_files:
        try:
            rel = str(f.relative_to(project_path))
            js_sources.append((rel, f.read_text()))
        except Exception:
            continue
    for f in html_files:
        try:
            src = f.read_text()
            rel = str(f.relative_to(project_path))
        except Exception:
            continue
        for m in re.finditer(r'<script[^>]*>(.*?)</script>', src, re.DOTALL):
            js_sources.append((rel, m.group(1)))

    for fname, src in js_sources:
        # getElementById("id")
        for m in re.finditer(r'getElementById\(\s*["\']([^"\']+)["\']\s*\)', src):
            js_refs.setdefault(m.group(1), set()).add(fname)
        # querySelector("#id") / querySelectorAll("#id")
        for m in re.finditer(r'querySelector(?:All)?\(\s*["\']#([^"\'.\s\[]+)["\']\s*\)', src):
            js_refs.setdefault(m.group(1), set()).add(fname)

    # Find JS references to IDs that don't exist in HTML
    for ref_id in sorted(js_refs):
        if ref_id not in html_ids:
            suggestion = ""
            close = get_close_matches(ref_id, list(html_ids), n=1, cutoff=0.6)
            if close:
                suggestion = f" (did you mean '{close[0]}'?)"
            sources = ", ".join(sorted(js_refs[ref_id]))
            issues.append(f"{sources}: references id='{ref_id}' but no HTML element has this ID{suggestion}")

    return issues
