/* Nova Forge — Interactive UI v5
   inline chat, pipeline diagrams, scroll reveals
   ─────────────────────────────────── */

(function () {
  'use strict';

  // ── Copy to Clipboard ──────────────────────────────────────────────

  function copyToClipboard(text, btn) {
    navigator.clipboard.writeText(text).then(function () {
      btn.classList.add('copied');
      var orig = btn.textContent;
      if (btn.classList.contains('copy-btn-sm')) {
        btn.textContent = 'Copied!';
      }
      setTimeout(function () {
        btn.classList.remove('copied');
        if (btn.classList.contains('copy-btn-sm')) {
          btn.textContent = 'Copy';
        }
      }, 1500);
    }).catch(function () {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      btn.classList.add('copied');
      setTimeout(function () { btn.classList.remove('copied'); }, 1500);
    });
  }

  // Code block copy buttons
  document.querySelectorAll('.copy-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      copyToClipboard(btn.getAttribute('data-copy'), btn);
    });
  });

  // Small copy buttons (command table)
  document.querySelectorAll('.copy-btn-sm').forEach(function (btn) {
    btn.addEventListener('click', function () {
      copyToClipboard(btn.getAttribute('data-copy'), btn);
    });
  });

  // Prompt cards
  document.querySelectorAll('.prompt-card').forEach(function (card) {
    var btn = card.querySelector('.copy-prompt-btn');
    var prompt = card.getAttribute('data-prompt');

    function doCopy() {
      copyToClipboard(prompt, btn);
      btn.textContent = 'Copied!';
      setTimeout(function () { btn.textContent = 'Copy prompt'; }, 1500);
    }

    card.addEventListener('click', function (e) {
      if (e.target !== btn) doCopy();
    });
    btn.addEventListener('click', doCopy);
  });

  // ── Command Tabs ───────────────────────────────────────────────────

  var tabs = document.querySelectorAll('.cmd-tab');
  var panels = document.querySelectorAll('.cmd-panel');

  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      var target = tab.getAttribute('data-tab');
      tabs.forEach(function (t) { t.classList.remove('active'); });
      panels.forEach(function (p) { p.classList.remove('active'); });
      tab.classList.add('active');
      var panel = document.getElementById('tab-' + target);
      if (panel) panel.classList.add('active');
    });
  });

  // ── Active Section Tracking ────────────────────────────────────────

  var navLinks = document.querySelectorAll('.nav-links a');
  var sections = [];

  navLinks.forEach(function (link) {
    var href = link.getAttribute('href');
    if (href && href.startsWith('#')) {
      var section = document.getElementById(href.substring(1));
      if (section) sections.push({ el: section, link: link });
    }
  });

  function updateActiveSection() {
    var scrollY = window.scrollY + 140;
    for (var i = sections.length - 1; i >= 0; i--) {
      if (sections[i].el.offsetTop <= scrollY) {
        navLinks.forEach(function (l) { l.classList.remove('active'); });
        sections[i].link.classList.add('active');
        return;
      }
    }
  }

  var scrollTimeout;
  window.addEventListener('scroll', function () {
    if (scrollTimeout) return;
    scrollTimeout = setTimeout(function () {
      scrollTimeout = null;
      updateActiveSection();
    }, 50);
  });

  // ── Scroll Reveal ──────────────────────────────────────────────────

  var revealSelectors = [
    '.section-title',
    '.section-desc',
    '.step',
    '.prompt-card',
    '.pipeline-node',
    '.pipeline-connector',
    '.formation-card',
    '.model-group',
    '.code-block',
    '.cmd-tabs',
    '.api-endpoints',
    '.chat-inline',
    '.autonomy-matrix-wrap',
    '.gallery-card'
  ];

  // Tag stagger parents
  document.querySelectorAll('.prompt-grid, .formation-grid, .steps, .pipeline-diagram, .gallery-grid').forEach(function (el) {
    el.classList.add('reveal-stagger');
  });

  // Tag individual elements
  revealSelectors.forEach(function (sel) {
    document.querySelectorAll(sel).forEach(function (el) {
      if (el.closest('#hero')) return;
      el.classList.add('reveal');
    });
  });

  if ('IntersectionObserver' in window) {
    var revealObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
          revealObserver.unobserve(entry.target);
        }
      });
    }, { threshold: 0.08, rootMargin: '0px 0px -30px 0px' });

    document.querySelectorAll('.reveal').forEach(function (el) {
      revealObserver.observe(el);
    });
  } else {
    document.querySelectorAll('.reveal').forEach(function (el) {
      el.classList.add('visible');
    });
  }

  // ── Mobile Menu ────────────────────────────────────────────────────

  var sidebar = document.getElementById('sidebar');
  var hamburger = document.getElementById('hamburger');
  var mobileClose = document.getElementById('mobile-close');

  hamburger.addEventListener('click', function () {
    sidebar.classList.add('open');
  });

  mobileClose.addEventListener('click', function () {
    sidebar.classList.remove('open');
  });

  navLinks.forEach(function (link) {
    link.addEventListener('click', function () {
      sidebar.classList.remove('open');
    });
  });

  // ── Inline Chat ────────────────────────────────────────────────────

  var chatForm = document.getElementById('chat-form');
  var chatInput = document.getElementById('chat-input');
  var chatMessages = document.getElementById('chat-messages');
  var chatSend = document.getElementById('chat-send');

  document.querySelectorAll('.chat-suggest').forEach(function (btn) {
    btn.addEventListener('click', function () {
      chatInput.value = btn.getAttribute('data-q');
      chatForm.dispatchEvent(new Event('submit'));
      var suggestionsEl = document.getElementById('chat-suggestions');
      if (suggestionsEl) suggestionsEl.remove();
    });
  });

  function appendMessage(role, text) {
    var div = document.createElement('div');
    div.className = 'chat-msg ' + role;
    var html = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\n/g, '<br>');
    div.innerHTML = '<p>' + html + '</p>';
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return div;
  }

  chatForm.addEventListener('submit', function (e) {
    e.preventDefault();
    var msg = chatInput.value.trim();
    if (!msg) return;

    appendMessage('user', msg);
    chatInput.value = '';
    chatSend.disabled = true;

    var thinkingDiv = appendMessage('thinking', 'Nova is thinking...');

    fetch('/api/docs/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg }),
    })
      .then(function (res) {
        if (!res.ok) throw new Error('API error: ' + res.status);
        return res.json();
      })
      .then(function (data) {
        thinkingDiv.remove();
        appendMessage('assistant', data.response || 'Sorry, I could not generate a response.');
      })
      .catch(function () {
        thinkingDiv.remove();
        appendMessage('assistant', 'Sorry, the chat service is currently unavailable. Please try the CLI directly: `python3 forge_cli.py`');
      })
      .finally(function () {
        chatSend.disabled = false;
        chatInput.focus();
      });
  });

  // ── Pipeline Diagram Interaction ───────────────────────────────────

  var pipelineDetails = {
    interview: 'Interactive 5-step scope definition: project description, tech stack, risk level, agent formation, and model selection. Arrow-key menus with descriptions.',
    plan: 'AI generates <code>spec.md</code> then decomposes it into <code>tasks.json</code> with dependencies, file assignments, and wave ordering via topological sort (Kahn\'s algorithm).',
    build: 'Independent tasks run concurrently via <code>asyncio.gather</code> with per-provider semaphores. Each agent gets its own tool-use loop, file sandbox, and artifact context from upstream tasks.',
    gate: 'An adversarial read-only reviewer agent inspects all build output and produces a PASS, FAIL, or CONDITIONAL verdict. Failed tasks auto-retry with error injection for self-correction.',
    deploy: 'Instant shareable URL via Cloudflare Tunnel. One-command production deployment with Docker, nginx, SSL, and health checks. BuildVerifier runs L1 (static), L2 (server), L3 (browser) checks.'
  };

  var detailPanel = document.getElementById('pipeline-detail');
  var pipelineNodes = document.querySelectorAll('.pipeline-node');

  pipelineNodes.forEach(function (node) {
    node.addEventListener('click', function () {
      var nodeName = node.getAttribute('data-node');
      var isActive = node.classList.contains('active');

      // Remove active from all nodes
      pipelineNodes.forEach(function (n) { n.classList.remove('active'); });

      if (isActive) {
        // Toggle off
        detailPanel.classList.remove('visible');
        detailPanel.innerHTML = '';
      } else {
        // Toggle on
        node.classList.add('active');
        detailPanel.innerHTML = '<p>' + (pipelineDetails[nodeName] || '') + '</p>';
        detailPanel.classList.add('visible');
      }
    });
  });

  // ── Load Dynamic Stats ─────────────────────────────────────────────

  fetch('/api/info')
    .then(function (res) { return res.json(); })
    .then(function (data) {
      var models = data.models && data.models.aliases ? Object.keys(data.models.aliases).length : 7;
      var formations = data.formations ? Object.keys(data.formations).length : 8;
      var tools = data.tools ? data.tools.length : 12;

      animateCounter('s-models', models);
      animateCounter('s-formations', formations);
      animateCounter('s-tools', tools);
      if (data.stats && data.stats.tests) {
        animateCounter('s-tests', data.stats.tests);
      }
    })
    .catch(function () {});

  // ── Counter Animation ──────────────────────────────────────────────

  function animateCounter(id, target) {
    var el = document.getElementById(id);
    if (!el) return;
    var start = 0;
    var duration = 1200;
    var startTime = null;

    function step(timestamp) {
      if (!startTime) startTime = timestamp;
      var progress = Math.min((timestamp - startTime) / duration, 1);
      var eased = 1 - Math.pow(1 - progress, 3);
      var current = Math.round(start + (target - start) * eased);
      el.textContent = current;
      if (progress < 1) {
        requestAnimationFrame(step);
      }
    }
    requestAnimationFrame(step);
  }

})();
