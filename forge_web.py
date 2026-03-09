"""Nova Forge Web Dashboard — serves forge.herakles.dev.

Provides:
  /          — Landing page with project description + demo
  /health    — Health check endpoint
  /api/info  — JSON with version, model support, formation count
"""

from __future__ import annotations

import json
from pathlib import Path

from flask import Flask, jsonify, render_template_string
from flask_cors import CORS

from config import MODEL_ALIASES, DEFAULT_MODELS
from formations import FORMATIONS

app = Flask(__name__)
CORS(app)

_VERSION = "0.1.0"

# ── Landing page HTML ────────────────────────────────────────────────────────

_LANDING_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nova Forge — Open-Source Agent Orchestration</title>
<style>
  :root {
    --bg: #0d1117;
    --surface: #161b22;
    --border: #30363d;
    --text: #c9d1d9;
    --accent: #58a6ff;
    --green: #3fb950;
    --orange: #d29922;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
  }
  .container { max-width: 960px; margin: 0 auto; padding: 2rem 1.5rem; }
  h1 { color: var(--accent); font-size: 2.5rem; margin-bottom: 0.5rem; }
  h2 { color: var(--accent); font-size: 1.4rem; margin: 2rem 0 0.75rem; }
  .subtitle { color: #8b949e; font-size: 1.1rem; margin-bottom: 2rem; }
  .badge {
    display: inline-block;
    padding: 0.2rem 0.6rem;
    border-radius: 12px;
    font-size: 0.75rem;
    font-weight: 600;
    margin-right: 0.5rem;
  }
  .badge-green { background: rgba(63, 185, 80, 0.15); color: var(--green); }
  .badge-orange { background: rgba(210, 153, 34, 0.15); color: var(--orange); }
  .badge-blue { background: rgba(88, 166, 255, 0.15); color: var(--accent); }
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1.5rem;
    margin-bottom: 1rem;
  }
  code {
    background: rgba(110, 118, 129, 0.2);
    padding: 0.15rem 0.4rem;
    border-radius: 4px;
    font-size: 0.9em;
  }
  pre {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1rem;
    overflow-x: auto;
    font-size: 0.85rem;
    margin: 0.75rem 0;
  }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; }
  .stat { text-align: center; }
  .stat-value { font-size: 2rem; font-weight: bold; color: var(--accent); }
  .stat-label { font-size: 0.85rem; color: #8b949e; }
  table { width: 100%; border-collapse: collapse; }
  th, td {
    padding: 0.5rem 0.75rem;
    text-align: left;
    border-bottom: 1px solid var(--border);
  }
  th { color: #8b949e; font-weight: 600; font-size: 0.85rem; text-transform: uppercase; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .footer { margin-top: 3rem; text-align: center; color: #8b949e; font-size: 0.85rem; }
</style>
</head>
<body>
<div class="container">
  <h1>Nova Forge</h1>
  <p class="subtitle">
    Open-source agent orchestration framework. V11's proven patterns, any LLM, pure Python.
    <span class="badge badge-green">v{{ version }}</span>
    <span class="badge badge-blue">Amazon Nova Hackathon</span>
  </p>

  <div class="grid">
    <div class="card stat">
      <div class="stat-value">{{ model_count }}</div>
      <div class="stat-label">Model Aliases</div>
    </div>
    <div class="card stat">
      <div class="stat-value">{{ formation_count }}</div>
      <div class="stat-label">Formations</div>
    </div>
    <div class="card stat">
      <div class="stat-value">3</div>
      <div class="stat-label">Provider Adapters</div>
    </div>
    <div class="card stat">
      <div class="stat-value">6</div>
      <div class="stat-label">Built-in Tools</div>
    </div>
  </div>

  <h2>Quick Start</h2>
  <pre><code># Plan a new project
forge plan "weather dashboard" --model nova-lite

# Build it (agents execute in parallel waves)
forge build --model gemini-flash

# Deploy to a live URL
forge deploy --domain weather.herakles.dev

# Check status
forge status</code></pre>

  <h2>Architecture</h2>
  <div class="card">
    <p>Nova Forge replaces Claude Code's closed-source agent runtime with a <strong>~300-line Python tool-use loop</strong> that works with any LLM supporting function calling.</p>
    <br>
    <p><strong>Key insight</strong>: V11 built 89 services on the Hercules platform. Nova Forge proves those patterns are model-portable — we run them with Amazon Nova and build live apps.</p>
  </div>

  <h2>Supported Models</h2>
  <table>
    <thead><tr><th>Alias</th><th>Full Model ID</th></tr></thead>
    <tbody>
    {% for alias, model_id in models %}
      <tr><td><code>{{ alias }}</code></td><td>{{ model_id }}</td></tr>
    {% endfor %}
    </tbody>
  </table>

  <h2>Formations</h2>
  <table>
    <thead><tr><th>Formation</th><th>Roles</th><th>Use Case</th></tr></thead>
    <tbody>
    {% for name, f in formations %}
      <tr>
        <td><code>{{ name }}</code></td>
        <td>{{ f.roles | length }}</td>
        <td>{{ f.description[:80] }}{% if f.description|length > 80 %}...{% endif %}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>

  <h2>How It Works</h2>
  <div class="card">
    <p><strong>Phase 1</strong>: Planning — ForgeAgent + smart model generates spec.md</p>
    <p><strong>Phase 2</strong>: Decomposition — ForgeAgent breaks spec into tasks.json</p>
    <p><strong>Phase 3</strong>: Execution — Wave-dispatched parallel agents build the project</p>
    <p><strong>Phase 4</strong>: Gate Review — LLM reviewer produces PASS/FAIL/CONDITIONAL</p>
    <p><strong>Phase 5</strong>: Deploy — Docker build, nginx config, health check</p>
  </div>

  <div class="footer">
    <p>Built for the <a href="https://devpost.com">Amazon Nova AI Hackathon</a> &middot;
    <a href="/api/info">API Info</a></p>
  </div>
</div>
</body>
</html>
"""


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(
        _LANDING_HTML,
        version=_VERSION,
        model_count=len(MODEL_ALIASES),
        formation_count=len(FORMATIONS),
        models=sorted(MODEL_ALIASES.items()),
        formations=sorted(FORMATIONS.items()),
    )


@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": _VERSION})


@app.route("/api/info")
def api_info():
    return jsonify({
        "name": "Nova Forge",
        "version": _VERSION,
        "description": "Open-source agent orchestration framework",
        "models": {
            "aliases": MODEL_ALIASES,
            "defaults": DEFAULT_MODELS,
        },
        "formations": {
            name: {
                "roles": len(f.roles),
                "waves": len(f.wave_order),
                "gate_criteria_count": len(f.gate_criteria),
            }
            for name, f in FORMATIONS.items()
        },
        "providers": ["bedrock", "openai", "anthropic"],
        "tools": ["read_file", "write_file", "edit_file", "bash", "glob_files", "grep"],
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8162, debug=True)
