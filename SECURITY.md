# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Nova Forge, please report it responsibly:

1. **Do not** open a public GitHub issue for security vulnerabilities
2. Email the maintainers with details of the vulnerability
3. Include steps to reproduce, impact assessment, and suggested fix if possible

We will acknowledge receipt within 48 hours and provide a timeline for resolution.

## Security Model

Nova Forge executes LLM-generated code via agent tools (bash, write_file, etc.). This is inherent to AI agent frameworks.

**Built-in protections:**
- **PathSandbox** — Restricts file access to project directory
- **RiskClassifier** — Categorizes operations as LOW/MEDIUM/HIGH risk
- **Autonomy System (A0-A5)** — Gates dangerous operations by trust level
- **Circuit Breaker** — Disables tools after repeated failures
- **127.0.0.1 binding** — All servers and containers bind to localhost only

**Known limitations:**
- Bash command injection via LLM output is possible (industry-standard for agent frameworks)
- RiskClassifier uses regex-based detection (not a security boundary)
- A4/A5 autonomy levels allow unrestricted operations

**Recommendation:** Run Nova Forge in a sandboxed environment (container, VM) for untrusted inputs.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.5.x   | Yes       |
