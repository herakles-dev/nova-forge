"""Nova Forge Live-Reload Server — lightweight dev server for build preview.

Serves project files with auto-refresh injection. Shows a "building..."
page until HTML files appear. Used during /build to give remote viewers
a live view of the project being constructed in real-time.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

# ── Auto-refresh script injected into HTML responses ─────────────────────────

RELOAD_SCRIPT = """<script>
(function(){
  let h='';
  setInterval(async()=>{
    try{let r=await fetch('/__livereload');let d=await r.json();
    if(h&&h!==d.hash)window.location.reload();h=d.hash;}catch(e){}
  },2000);
})();
</script>
"""

# ── Building placeholder page ────────────────────────────────────────────────

BUILDING_PAGE = """<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><title>Nova Forge</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#0a0a0f;color:#e0e0e0;
min-height:100vh;display:flex;align-items:center;justify-content:center}
.c{text-align:center;max-width:520px;padding:2rem}
h1{font-size:2.2rem;background:linear-gradient(135deg,#a855f7,#ec4899);
-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:.3rem}
.sub{color:#666;margin-bottom:2rem;font-size:.95rem}
.sp{width:36px;height:36px;border:3px solid #222;border-top-color:#a855f7;
border-radius:50%;animation:s 1s linear infinite;margin:1.5rem auto}
@keyframes s{to{transform:rotate(360deg)}}
#fl{text-align:left;background:#111118;border:1px solid #1a1a2e;border-radius:8px;
padding:1rem 1.2rem;margin-top:1.5rem;font-family:'SF Mono',monospace;font-size:.82rem;
min-height:48px;max-height:320px;overflow-y:auto}
.ft{color:#888;font-size:.75rem;margin-bottom:.5rem;text-transform:uppercase;letter-spacing:.08em}
.f{color:#7dd3a0;padding:.15rem 0}.f::before{content:"+ ";color:#22c55e}
.e{color:#555;font-style:italic;font-size:.82rem}
a{color:#a855f7;text-decoration:none}a:hover{text-decoration:underline}
.done{background:#0f2a1a;border-color:#166534;padding:1.2rem;border-radius:8px;margin-top:1.5rem}
.done h2{color:#4ade80;font-size:1.1rem;margin-bottom:.4rem}
.done a{font-size:1rem;color:#a855f7}
</style></head><body>
<div class="c">
<h1>Nova Forge</h1>
<p class="sub">Building your project...</p>
<div class="sp" id="spinner"></div>
<div id="fl">
<div class="ft">Files</div>
<div id="files"><p class="e">Waiting for first files...</p></div>
</div>
<div id="ready" style="display:none"></div>
</div>
<script>
let h='',seen=new Set(),idx=null;
async function poll(){
  try{
    let r=await fetch('/__livereload');let d=await r.json();
    // Update file list
    if(d.files&&d.files.length){
      let el=document.getElementById('files');
      let added=false;
      d.files.forEach(f=>{if(!seen.has(f)){seen.add(f);added=true;
        if(seen.size===1)el.innerHTML='';
        let p=document.createElement('div');p.className='f';
        if(f.endsWith('.html')){
          p.innerHTML='<a href="/'+f+'">'+f+'</a>';
          if(!idx)idx='/'+f;
        }else{p.textContent=f}
        el.prepend(p);}});
    }
    // When build is done, show the link
    if(d.done&&idx){
      document.getElementById('spinner').style.display='none';
      let rd=document.getElementById('ready');
      rd.style.display='block';
      rd.innerHTML='<div class="done"><h2>Build Complete</h2><a href="'+idx+'">Open your app &rarr;</a></div>';
    }
    // Content hash change = reload (for when on a real page)
    if(h&&h!==d.hash)window.location.reload();
    h=d.hash;
  }catch(e){}
}
setInterval(poll,2000);poll();
</script></body></html>"""


# ── Request handler ──────────────────────────────────────────────────────────

SKIP_DIRS = frozenset({'.git', 'node_modules', '__pycache__', '.forge', '.venv', 'venv'})


class _Handler(SimpleHTTPRequestHandler):
    """HTTP handler with livereload injection and building-page fallback."""

    # Set by LiveReloadServer before starting
    _root: str = "."
    _building: bool = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=self._root, **kwargs)

    def do_GET(self):
        if self.path == '/__livereload':
            return self._handle_reload()

        # If root request and no index.html yet → show building page
        if self.path in ('/', '/index.html'):
            index = Path(self._root) / 'index.html'
            if not index.exists():
                # Check for index.html in subdirectories
                for p in Path(self._root).rglob('index.html'):
                    if not any(s in p.parts for s in SKIP_DIRS):
                        # Redirect to the found index
                        rel = str(p.relative_to(self._root))
                        self.send_response(302)
                        self.send_header('Location', '/' + rel)
                        self.end_headers()
                        return
                return self._serve_text(BUILDING_PAGE, 'text/html')

        # For HTML files: inject reload script
        translated = self.translate_path(self.path)
        if os.path.isfile(translated) and translated.endswith(('.html', '.htm')):
            return self._serve_html_with_reload(translated)

        super().do_GET()

    def _handle_reload(self):
        """Return JSON with content hash and file list for the livereload client."""
        root = Path(self._root)
        files = []
        mtimes = []

        for p in sorted(root.rglob('*')):
            if not p.is_file():
                continue
            if any(s in p.parts for s in SKIP_DIRS):
                continue
            rel = str(p.relative_to(root))
            files.append(rel)
            try:
                mtimes.append(str(p.stat().st_mtime_ns))
            except OSError:
                pass

        h = hashlib.md5('|'.join(mtimes[-200:]).encode()).hexdigest()[:12]

        data = json.dumps({
            "hash": h,
            "files": files[-60:],
            "done": not self._building,
        })
        self._serve_text(data, 'application/json')

    def _serve_html_with_reload(self, filepath: str):
        """Serve an HTML file with the auto-reload script injected."""
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        except OSError:
            self.send_error(404)
            return

        # Inject before </body> or at end
        if '</body>' in content:
            content = content.replace('</body>', RELOAD_SCRIPT + '</body>')
        elif '</html>' in content:
            content = content.replace('</html>', RELOAD_SCRIPT + '</html>')
        else:
            content += RELOAD_SCRIPT

        self._serve_text(content, 'text/html')

    def _serve_text(self, content: str, content_type: str):
        encoded = content.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', f'{content_type}; charset=utf-8')
        self.send_header('Content-Length', len(encoded))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        """Suppress request logging — don't pollute the build output."""
        pass


# ── Server class ─────────────────────────────────────────────────────────────

class LiveReloadServer:
    """Manages the live-reload dev server in a background thread.

    Usage:
        server = LiveReloadServer(project_path)
        port = server.start()          # Returns actual port
        # ... build happens, files are created ...
        server.mark_build_done()       # Enables redirect to index.html
        # ... later ...
        server.stop()
    """

    def __init__(self, root: Path, port: int = 8080):
        self.root = Path(root)
        self.port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        """Start the server in a background thread. Returns actual port."""
        _Handler._root = str(self.root)
        _Handler._building = True

        for p in range(self.port, self.port + 20):
            try:
                self._server = HTTPServer(('127.0.0.1', p), _Handler)
                self.port = p
                break
            except OSError:
                continue
        else:
            raise RuntimeError(f"No available port near {self.port}")

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def mark_build_done(self):
        """Signal that the build is complete — enables index.html redirect."""
        _Handler._building = False

    def stop(self):
        """Stop the server."""
        if self._server:
            self._server.shutdown()
            self._server = None
        self._thread = None
