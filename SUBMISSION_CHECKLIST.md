# Nova Forge — Amazon Nova AI Hackathon Submission Checklist

> Based on official rules at https://amazon-nova.devpost.com/rules
> Deadline: **March 16, 2026 at 5:00 PM PDT**

---

## 1. Devpost Account & Registration

- [ ] Registered at amazon-nova.devpost.com
- [ ] Devpost account created/logged in
- [ ] Developer classification selected: **Professional Developer**
- [ ] AWS Promotional Credits requested (deadline was Mar 13 — check if received)

## 2. Project Category

- [ ] Category selected: **Agentic AI**
  - _"Complex problem-solving with agent reasoning"_
  - Also eligible for Overall 1st/2nd prizes

## 3. Text Description (Devpost Form)

- [ ] Project name: **Nova Forge**
- [ ] Tagline filled in
- [ ] Brief summary explains what Nova Forge does
- [ ] Explains how it leverages Amazon Nova foundation models
- [ ] Covers all 3 judging criteria:
  - [ ] **Technical Implementation (60%)**: Architecture, Nova integration, system quality
  - [ ] **Enterprise/Community Impact (20%)**: Business value, community benefits, open-source
  - [ ] **Creativity/Innovation (20%)**: Novel multi-agent patterns, 3-tier prompts, formations

**Source**: Copy/adapt from `DEVPOST.md` in the repo

### Devpost Sections to Fill

| Section | Source |
|---------|--------|
| Inspiration | DEVPOST.md "Inspiration" |
| What it does | DEVPOST.md "What it does" |
| How we built it | DEVPOST.md "How we built it" |
| Challenges we ran into | DEVPOST.md "Challenges we ran into" |
| Accomplishments | DEVPOST.md "Accomplishments that we're proud of" |
| What we learned | DEVPOST.md "What we learned" |
| What's next | DEVPOST.md "What's next" |
| Built with | DEVPOST.md "Built with" |

## 4. Demo Video

### Requirements (from Official Rules)

- [ ] **Duration**: ~3 minutes (judges not required to watch beyond 3 min)
- [ ] **Shows project functioning** — must demonstrate real usage, not slides
- [ ] **Includes #AmazonNova** hashtag visible on screen
- [ ] **No copyrighted music** or third-party trademarks without permission
- [ ] **No third-party copyrighted material** without permission
- [ ] **Uploaded to YouTube, Vimeo, or Youku**
- [ ] **Publicly visible** (not unlisted/private)
- [ ] **Link pasted** into Devpost submission form

### Recommended Demo Flow (~3 min)

```
0:00 - 0:15  Title card: "Nova Forge — Describe it. Nova builds it." + #AmazonNova
0:15 - 0:30  What it is: open-source agent orchestration, 3 Nova models, 19 sprints
0:30 - 0:45  Launch forge_cli.py, show the welcome screen
0:45 - 1:30  Type "Build me an expense tracker with categories and charts"
             Show: smart planning questions → plan generation → parallel build
1:30 - 2:00  Build completes → show file tree → accept preview offer
2:00 - 2:15  Preview launches → show the working app in browser
2:15 - 2:30  Show benchmark results: all 3 Nova models at S-tier 100%
2:30 - 2:45  Quick flash: 11 formations, A0-A5 autonomy, 1670 tests, website
2:45 - 3:00  Close: "Open-source, pure Python, forge.herakles.dev" + #AmazonNova
```

## 5. Code Repository

- [ ] GitHub repo: https://github.com/herakles-dev/nova-forge
- [ ] **Public** (no access grants needed)
- [ ] If private: share with `testing@devpost.com` AND `Amazon-Nova-hackathon@amazon.com`
- [ ] Repository link pasted into Devpost form

### Repo Quality Checklist

- [x] README.md — project overview, quick start, architecture, benchmarks
- [x] LICENSE — MIT
- [x] CONTRIBUTING.md — fork workflow, conventions, how to contribute
- [x] CODE_OF_CONDUCT.md — Contributor Covenant
- [x] SECURITY.md — vulnerability reporting, security model
- [x] GUIDE.md — comprehensive user guide
- [x] .github/ISSUE_TEMPLATE/ — bug report + feature request templates
- [x] .github/PULL_REQUEST_TEMPLATE.md — PR checklist
- [x] .github/workflows/test.yml — CI (pytest on push/PR)
- [x] pyproject.toml — full [project] metadata
- [x] setup.sh — one-command install
- [x] requirements.txt — all dependencies pinned
- [x] .gitignore — no secrets, no venv, no __pycache__

## 6. Testing & Live Demo

- [ ] **Live demo URL**: https://forge.herakles.dev
- [ ] **Interactive demos**: https://forge.herakles.dev/demos/
- [ ] **User guide**: https://forge.herakles.dev/guide.html
- [ ] All links pasted into Devpost form
- [ ] **Site must remain available through end of judging** (~April 2, 2026)
- [ ] Docker container set to `restart: unless-stopped`
- [ ] No login required to view demos and website
- [ ] If login required for any feature: provide credentials in submission

## 7. Technical Compliance

- [ ] Uses **Amazon Nova** as core solution component
  - [x] Nova 2 Lite (bedrock/us.amazon.nova-2-lite-v1:0)
  - [x] Nova Pro (bedrock/us.amazon.nova-pro-v1:0)
  - [x] Nova Premier (bedrock/us.amazon.nova-premier-v1:0)
- [ ] Accessed via **AWS Bedrock** Converse API
- [ ] All materials in **English**
- [ ] Project is a **new creation** during the submission period (Feb 2 – Mar 16, 2026)
- [ ] No proprietary frameworks from sponsor (Amazon/Devpost)
- [ ] Open-source components used are license-compliant (MIT)

## 8. IP & Legal

- [ ] Submission is **original work**
- [ ] **Solely owned** — no other rights holders
- [ ] Does not violate copyright, trademark, patent, contract, or privacy rights
- [ ] Third-party open-source used is license-compliant
- [ ] No financial or preferential support from Amazon or Devpost

## 9. Bonus Prizes (Optional)

### Blog Post Prize ($200 AWS Credits — first 100)

- [ ] Publish on **builder.aws.com**
- [ ] Use **'Amazon-Nova'** tag
- [ ] Content must be **materially different** from Devpost description
- [ ] Must address **community impact** question
- [ ] Link provided on Devpost form
- [ ] Published during submission period (Feb 2 – Mar 16)

### Feedback Prize ($50 Cash — 60 winners)

- [ ] Submit via online feedback form (by Mar 18)
- [ ] Provide **actionable comments** for Nova improvement
- [ ] Examples: bug reports, UI improvements, integration suggestions
- [ ] One submission per entrant

## 10. Pre-Submit Verification

- [ ] All Devpost form fields completed
- [ ] Video link works and is publicly accessible
- [ ] GitHub link works
- [ ] forge.herakles.dev loads correctly
- [ ] forge.herakles.dev/demos/ shows all 7 demos
- [ ] forge.herakles.dev/guide.html loads correctly
- [ ] No placeholder content ("Coming soon", "TODO", etc.)
- [ ] All stats consistent across website, README, DEVPOST (sprints, tests, tools, formations)
- [ ] Benchmark scores labeled as **custom suite**
- [ ] Submit **before 5:00 PM PDT on March 16, 2026**

---

## Judging Criteria Reference

| Criteria | Weight | Our Strengths |
|----------|--------|---------------|
| **Technical Implementation** | 60% | 30K LOC, 1670 tests, 3-tier prompts, circuit breaker, convergence tracking, all 3 Nova S-tier |
| **Enterprise/Community Impact** | 20% | Open-source (MIT), community infra, model-agnostic, production patterns from 89+ services |
| **Creativity/Innovation** | 20% | DAAO formation routing, 6-level autonomy, pre-seeded context, adversarial gate review |

## Prize Targets

| Prize | Award | Eligibility |
|-------|-------|-------------|
| Overall 1st | $15,000 + $5,000 AWS | All eligible |
| Overall 2nd | $7,000 + $5,000 AWS | All eligible |
| Best Agentic System | $3,000 + $5,000 AWS | Agentic AI category |
| Blog Post | $200 AWS Credits | First 100 eligible posts |
| Feedback | $50 cash | 60 winners |

**Note**: Each project eligible for one Overall OR one Category prize (not both), plus one Blog Post prize.

## Key Dates

| Date | Event |
|------|-------|
| **Mar 16, 5:00 PM PDT** | Submission deadline |
| Mar 17 – Apr 2 | Judging period |
| ~Apr 8 | Winners announced |

---

## Quick Links

- Devpost submission: https://amazon-nova.devpost.com/
- Official rules: https://amazon-nova.devpost.com/rules
- Our GitHub: https://github.com/herakles-dev/nova-forge
- Our live demo: https://forge.herakles.dev
- Our demos: https://forge.herakles.dev/demos/
- Our guide: https://forge.herakles.dev/guide.html
