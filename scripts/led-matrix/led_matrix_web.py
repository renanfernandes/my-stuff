#!/usr/bin/env python3
"""
LED Matrix Web Controller
=========================
Single-file Flask web server that controls the LED matrix display through
a browser UI and REST API.

Imports helpers from led_matrix_display.py — that file is NOT modified.

Start:
    sudo python3 led_matrix_web.py
    sudo python3 led_matrix_web.py --simulate    # Tkinter preview, no hardware
    sudo python3 led_matrix_web.py --config path/to/config.yaml
    sudo python3 led_matrix_web.py --port 8080

Web UI:  http://<pi-ip>:5000/

REST API
--------
GET  /api/status               Current mode and state
POST /api/mode/off             Stop / clear screen
POST /api/mode/spotify         Start Spotify  {"account": "name"} (optional)
POST /api/upload               Upload image (multipart, field: "file") → display
GET  /api/spotify/accounts     List configured accounts
GET  /api/config               Full config as JSON (client_secret masked)
POST /api/config               Partial-update config and write to YAML
GET  /callback                 Spotify OAuth redirect handler (auto-sets up auth)
"""

import argparse
import io
import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import yaml
from PIL import Image

# ── Import helpers from the original display module ───────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from led_matrix_display import (
    DEFAULT_CONFIG,
    MatrixDisplay,
    SpotifyPoller,
    _SPOTIFY_OK,
    _deep_merge,
    _fit,
    _transition_cfg,
    apply_transition,
    load_config,
)

# ── Optional: Flask ────────────────────────────────────────────────────────────
try:
    from flask import Flask, jsonify, redirect, request
    _FLASK_OK = True
except ImportError:
    _FLASK_OK = False

log = logging.getLogger(__name__)

_ALLOWED_EXTENSIONS = frozenset({'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp'})


def _allowed_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in _ALLOWED_EXTENSIONS


# ─────────────────────────────────────────────────────────────────────────────
# DisplayController
# ─────────────────────────────────────────────────────────────────────────────

class DisplayController:
    """Thread-safe controller that drives the LED matrix from the web server."""

    def __init__(self, cfg: dict, simulate: bool = False):
        self.cfg = cfg
        self.simulate = simulate
        self._display = MatrixDisplay(cfg, simulate=simulate)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pollers: list = []

        # State exposed to the API
        self.mode: str = 'off'
        self.current_track: dict = {}
        self.current_image_name: str = ''
        self.started_at: float = time.time()
        self.mode_changed_at: float = time.time()

        # account_name -> (SpotifyOAuth, account_arg) for in-progress OAuth
        self._pending_auth: dict = {}
        # account_name -> SpotifyOAuth with correct redirect_uri, ready for polling
        self._auth_objects: dict = {}

    # ── internal ───────────────────────────────────────────────────────────────

    def _stop_current(self):
        """Signal the running mode thread to stop, then wait for it."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        self._stop_event.clear()
        self._thread = None
        self._pollers = []

    def _set_mode(self, mode: str, image_name: str = '', track: Optional[dict] = None):
        self.mode = mode
        self.current_image_name = image_name
        self.current_track = track or {}
        self.mode_changed_at = time.time()

    # ── public API ──────────────────────────────────────────────────────────────

    def stop(self):
        """Stop current mode and clear the display."""
        with self._lock:
            self._stop_current()
            self._display.clear()
            self._set_mode('off')

    def show_image(self, img: Image.Image, name: str = 'image', prev_img=None):
        """Display an image immediately, stopping any running mode."""
        with self._lock:
            self._stop_current()
            t = _transition_cfg(self.cfg)
            fitted = _fit(img, self._display, self.cfg)
            apply_transition(self._display, prev_img, fitted,
                             transition=t['transition'],
                             duration=t['transition_duration'],
                             fps=t['transition_fps'])
            self._set_mode('image', image_name=name)

    def start_spotify(self, account: Optional[str] = None,
                       redirect_uri: Optional[str] = None) -> dict:
        """
        Start Spotify mode.

        redirect_uri: use the web server's own callback URL so the browser
                      redirect lands back here (not localhost:8888).

        Returns:
          {'status': 'started', 'account': name}
          {'status': 'auth_required', 'auth_url': '...', 'account': name}
          {'status': 'error', 'message': '...'}
        """
        if not _SPOTIFY_OK:
            return {'status': 'error', 'message': 'spotipy / requests not installed'}

        sp_cfg = self.cfg['spotify']
        if not sp_cfg.get('client_id') or not sp_cfg.get('client_secret'):
            return {'status': 'error', 'message': 'Spotify credentials not configured — open Settings'}

        all_accounts = sp_cfg.get('accounts', {})
        account_name = account or sp_cfg.get('default_account', 'default')

        if account_name not in all_accounts:
            return {'status': 'error', 'message': f"Account '{account_name}' not found in config"}

        cache_path = all_accounts[account_name].get('cache_path', f'.spotify_token_{account_name}')
        effective_redirect_uri = redirect_uri or sp_cfg['redirect_uri']

        try:
            from spotipy.oauth2 import SpotifyOAuth
        except ImportError:
            return {'status': 'error', 'message': 'spotipy not installed'}

        auth = SpotifyOAuth(
            client_id=sp_cfg['client_id'],
            client_secret=sp_cfg['client_secret'],
            redirect_uri=effective_redirect_uri,
            scope='user-read-currently-playing user-read-playback-state',
            cache_path=cache_path,
            open_browser=False,
        )

        cached = auth.get_cached_token()
        if not cached:
            auth_url = auth.get_authorize_url()
            with self._lock:
                self._pending_auth[account_name] = (auth, account)
            return {'status': 'auth_required', 'auth_url': auth_url, 'account': account_name}

        # Token found — store auth so the polling loop uses the same object
        # (same redirect_uri for token refreshes)
        self._auth_objects[account_name] = auth
        self._launch_spotify_thread(account_name, auth)
        return {'status': 'started', 'account': account_name}

    def complete_oauth(self, code: str) -> bool:
        """Called from /callback — complete any pending OAuth and start Spotify."""
        for account_name in list(self._pending_auth.keys()):
            auth, account = self._pending_auth[account_name]
            try:
                auth.get_access_token(code)
                del self._pending_auth[account_name]
                # Reuse the same auth object (has the correct redirect_uri)
                self._auth_objects[account_name] = auth
                self._launch_spotify_thread(account_name, auth)
                return True
            except Exception as exc:
                log.error("OAuth completion for '%s' failed: %s", account_name, exc)
        return False

    def _launch_spotify_thread(self, account_name: str, auth):
        with self._lock:
            self._stop_current()
            self._set_mode('spotify')
        self._thread = threading.Thread(
            target=self._spotify_loop, args=(account_name, auth), daemon=True
        )
        self._thread.start()

    def _spotify_loop(self, account_name: str, auth):
        """
        Polling loop driven directly by the provided SpotifyOAuth object.
        Avoids SpotifyPoller.__init__ which tries to call input() if the
        token is missing — that hangs silently in a background thread.
        """
        try:
            import spotipy as _spotipy
            sp = _spotipy.Spotify(auth_manager=auth)
            sp_cfg = self.cfg['spotify']
            interval = float(sp_cfg['poll_interval'])
            clear_on_stop = bool(sp_cfg.get('clear_on_stop', True))
            clear_delay = float(sp_cfg.get('clear_delay', 60.0))
            t = _transition_cfg(self.cfg)
            transition = t['transition']
            transition_duration = t['transition_duration']
            transition_fps = t['transition_fps']
            last_track_id: Optional[str] = None
            last_playing_at: float = time.time()
            screen_cleared: bool = False
            prev_art: Optional[Image.Image] = None

            while not self._stop_event.is_set():
                try:
                    result = sp.currently_playing()
                    is_playing = bool(result and result.get('is_playing'))

                    if is_playing:
                        item = result.get('item')
                        if item:
                            track_id = item.get('uri') or item.get('id', '')
                            last_playing_at = time.time()
                            screen_cleared = False
                            if track_id != last_track_id:
                                last_track_id = track_id
                                artists = ', '.join(
                                    a['name'] for a in item.get('artists', [])
                                )
                                log.info("Now playing: %s — %s",
                                         item.get('name', '?'), artists)
                                url = SpotifyPoller._pick_art_url(item)
                                if url:
                                    art = SpotifyPoller._fetch(url)
                                    if art:
                                        new_fitted = _fit(art, self._display, self.cfg)
                                        apply_transition(
                                            self._display, prev_art, new_fitted,
                                            transition=transition,
                                            duration=transition_duration,
                                            fps=transition_fps,
                                            stop_event=self._stop_event,
                                        )
                                        prev_art = new_fitted
                                        self.current_track = {
                                            'name': item.get('name', ''),
                                            'artists': artists,
                                            'album': item.get('album', {}).get('name', ''),
                                            'art_url': url,
                                        }
                    else:
                        # Nothing playing — clear after delay
                        if clear_on_stop and not screen_cleared:
                            if time.time() - last_playing_at >= clear_delay:
                                log.info("Nothing playing for %.0fs — clearing screen",
                                         clear_delay)
                                self._display.clear()
                                self.current_track = {}
                                last_track_id = None
                                screen_cleared = True

                except Exception as exc:
                    log.warning("Spotify poll error: %s", exc)

                self._stop_event.wait(timeout=interval)

        except Exception as exc:
            log.error("Spotify loop crashed: %s", exc)
            with self._lock:
                self._set_mode('off')

    def status(self) -> dict:
        return {
            'mode': self.mode,
            'uptime_seconds': int(time.time() - self.started_at),
            'current_track': self.current_track,
            'current_image': self.current_image_name,
            'simulate': self.simulate,
        }

    def update_config(self, updates: dict, config_path: str):
        """Deep-merge *updates* into the running config and write to YAML."""
        self.cfg = _deep_merge(self.cfg, updates)
        with open(config_path, 'w') as fh:
            yaml.dump(self.cfg, fh, default_flow_style=False, allow_unicode=True)
        log.info("Config saved to %s", config_path)


# ─────────────────────────────────────────────────────────────────────────────
# Flask application
# ─────────────────────────────────────────────────────────────────────────────

def create_app(controller: DisplayController, config_path: str) -> 'Flask':
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB upload cap

    # ── REST API ───────────────────────────────────────────────────────────────

    @app.route('/api/status')
    def api_status():
        return jsonify(controller.status())

    @app.route('/api/mode/off', methods=['POST'])
    def api_mode_off():
        controller.stop()
        return jsonify({'ok': True, 'mode': 'off'})

    @app.route('/api/mode/spotify', methods=['POST'])
    def api_mode_spotify():
        data = request.get_json(silent=True) or {}
        # Use this server's own /callback as the redirect URI so the browser
        # lands back here after OAuth approval (not localhost:8888)
        web_redirect_uri = request.host_url.rstrip('/') + '/callback'
        result = controller.start_spotify(
            account=data.get('account'),
            redirect_uri=web_redirect_uri,
        )
        status_code = (
            202 if result.get('status') == 'auth_required' else
            400 if result.get('status') == 'error' else
            200
        )
        return jsonify(result), status_code

    @app.route('/api/upload', methods=['POST'])
    def api_upload():
        if 'file' not in request.files:
            return jsonify({'error': 'No file field in request'}), 400
        f = request.files['file']
        if not f.filename or not _allowed_file(f.filename):
            return jsonify({'error': 'File type not allowed'}), 400
        # Sanitise: strip any directory components from the filename
        safe_name = Path(f.filename).name
        try:
            data = f.read()
            img = Image.open(io.BytesIO(data))
            img.load()  # validate the image data fully
            controller.show_image(img, name=safe_name)
            return jsonify({'ok': True, 'filename': safe_name})
        except Exception as exc:
            log.warning("Upload failed: %s", exc)
            return jsonify({'error': str(exc)}), 400

    @app.route('/api/spotify/accounts')
    def api_spotify_accounts():
        sp = controller.cfg['spotify']
        return jsonify({
            'accounts': list(sp.get('accounts', {}).keys()),
            'default': sp.get('default_account', 'default'),
        })

    @app.route('/api/config', methods=['GET'])
    def api_config_get():
        # Deep-copy via JSON to avoid mutating the live config
        cfg = json.loads(json.dumps(controller.cfg))
        if cfg.get('spotify', {}).get('client_secret'):
            cfg['spotify']['client_secret'] = '••••••••'
        return jsonify(cfg)

    @app.route('/api/config', methods=['POST'])
    def api_config_post():
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'error': 'JSON body required'}), 400
        # Don't overwrite the real secret if the masked placeholder was sent back
        if data.get('spotify', {}).get('client_secret') == '••••••••':
            data['spotify'].pop('client_secret', None)
        try:
            controller.update_config(data, config_path)
        except Exception as exc:
            log.error("Config save failed: %s", exc)
            return jsonify({'error': str(exc)}), 500
        return jsonify({'ok': True})

    @app.route('/callback')
    def spotify_callback():
        """Spotify OAuth redirect URI — browser lands here after user approves."""
        error = request.args.get('error')
        code = request.args.get('code')
        if error:
            return redirect('/?toast=spotify_error')
        if not code:
            return redirect('/?toast=spotify_nocode')
        ok = controller.complete_oauth(code)
        return redirect('/?toast=spotify_ok' if ok else '/?toast=spotify_fail')

    # ── Web UI ─────────────────────────────────────────────────────────────────

    @app.route('/')
    def index():
        return _HTML_PAGE

    return app


# ─────────────────────────────────────────────────────────────────────────────
# Embedded single-page UI
# ─────────────────────────────────────────────────────────────────────────────

_HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LED Matrix</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0f1117;--surface:#1a1d27;--surface2:#232635;
  --border:rgba(255,255,255,.08);
  --accent:#7c3aed;--accent-hover:#8b5cf6;
  --green:#22c55e;--red:#ef4444;--yellow:#f59e0b;
  --text:#e2e8f0;--muted:#64748b;
  --radius:12px;
}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding:1rem 1rem 5rem}
/* Header */
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:1.5rem}
h1{font-size:1.35rem;font-weight:700;letter-spacing:-.02em}
.badge{display:flex;align-items:center;gap:.45rem;background:var(--surface);border:1px solid var(--border);border-radius:9999px;padding:.3rem .9rem;font-size:.82rem}
.dot{width:8px;height:8px;border-radius:50%;background:var(--muted);flex-shrink:0;transition:background .3s}
.dot.green{background:var(--green);box-shadow:0 0 8px var(--green)}
.dot.purple{background:var(--accent-hover);box-shadow:0 0 8px var(--accent-hover)}
/* Cards */
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:1.25rem;margin-bottom:1rem}
.card-title{font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:.85rem}
/* Grid */
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:1rem;margin-bottom:1rem}
/* Now playing */
#np-row{display:flex;align-items:center;gap:1rem}
#art-img{width:64px;height:64px;border-radius:8px;object-fit:cover;background:var(--surface2);flex-shrink:0}
#track-name{font-weight:600;font-size:.95rem;margin-bottom:.15rem}
#track-artists{color:var(--muted);font-size:.85rem}
#track-album{color:var(--muted);font-size:.78rem;margin-top:.1rem}
/* Buttons */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:.35rem;border:none;border-radius:8px;cursor:pointer;font-size:.88rem;font-weight:500;padding:.5rem 1rem;transition:opacity .15s,transform .1s;white-space:nowrap}
.btn:active{transform:scale(.97)}
.btn:disabled{opacity:.45;cursor:not-allowed}
.btn-primary{background:var(--accent);color:#fff}
.btn-primary:hover:not(:disabled){background:var(--accent-hover)}
.btn-success{background:var(--green);color:#000}
.btn-success:hover:not(:disabled){filter:brightness(1.1)}
.btn-danger{background:var(--red);color:#fff}
.btn-danger:hover:not(:disabled){filter:brightness(1.1)}
.btn-ghost{background:transparent;border:1px solid var(--border);color:var(--text)}
.btn-ghost:hover:not(:disabled){background:var(--surface2)}
.btn-block{width:100%;margin-top:.5rem}
.flex{display:flex;gap:.5rem}
.flex1{flex:1;min-width:0}
/* Form */
label{display:block;font-size:.82rem;color:var(--muted);margin-top:.75rem}
label:first-of-type{margin-top:0}
input[type=text],input[type=number],input[type=password],input[type=url],select,textarea{width:100%;background:var(--surface2);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:.88rem;padding:.5rem .75rem;margin-top:.25rem;outline:none;font-family:inherit}
input:focus,select:focus,textarea:focus{border-color:var(--accent)}
input[type=range]{width:100%;margin-top:.35rem;accent-color:var(--accent)}
.check-row{display:flex;align-items:center;gap:.5rem;margin-top:.75rem}
.check-row input{width:auto;accent-color:var(--accent)}
.check-row label{margin-top:0;color:var(--text)}
/* Drop zone */
#drop-zone{border:2px dashed var(--border);border-radius:8px;padding:1.5rem 1rem;text-align:center;cursor:pointer;transition:border-color .2s,background .2s;color:var(--muted);font-size:.88rem}
#drop-zone.over{border-color:var(--accent);background:rgba(124,58,237,.08)}
#drop-zone:hover{border-color:var(--accent-hover)}
#file-preview{width:72px;height:72px;border-radius:6px;object-fit:cover;margin:.5rem auto;display:none}
/* Accordion */
.acc-hdr{display:flex;align-items:center;justify-content:space-between;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:.9rem 1.25rem;cursor:pointer;font-weight:500;font-size:.92rem;transition:background .15s;user-select:none;margin-bottom:2px}
.acc-hdr:hover{background:var(--surface2)}
.acc-hdr.open{border-bottom-left-radius:0;border-bottom-right-radius:0;margin-bottom:0}
.chevron{transition:transform .2s;color:var(--muted)}
.acc-hdr.open .chevron{transform:rotate(180deg)}
.acc-body{background:var(--surface);border:1px solid var(--border);border-top:none;border-bottom-left-radius:var(--radius);border-bottom-right-radius:var(--radius);padding:1.25rem;display:none;margin-bottom:.5rem}
.acc-body.open{display:block}
/* Auth banner */
#auth-banner{background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.3);border-radius:8px;padding:.75rem 1rem;font-size:.83rem;margin-top:.75rem;display:none}
#auth-banner a{color:var(--yellow);font-weight:500}
/* Toast */
#toast{position:fixed;bottom:1.5rem;left:50%;transform:translateX(-50%) translateY(5rem);background:var(--surface2);border:1px solid var(--border);border-radius:9999px;padding:.55rem 1.4rem;font-size:.88rem;transition:transform .3s ease;white-space:nowrap;z-index:100;pointer-events:none}
#toast.show{transform:translateX(-50%) translateY(0)}
hr{border:none;border-top:1px solid var(--border);margin:1rem 0}
.hint{font-size:.75rem;color:var(--muted);margin-top:.3rem;line-height:1.4}
.warn-note{background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.2);border-radius:8px;padding:.6rem .9rem;font-size:.8rem;color:var(--yellow);margin-bottom:.75rem}
</style>
</head>
<body>

<header>
  <h1>&#x25A3; LED Matrix</h1>
  <div class="badge">
    <div class="dot" id="status-dot"></div>
    <span id="status-text">Connecting&hellip;</span>
  </div>
</header>

<!-- Now playing (Spotify only) -->
<div class="card" id="np-card" style="display:none">
  <div class="card-title">Now Playing</div>
  <div id="np-row">
    <img id="art-img" src="" alt="Album art">
    <div>
      <div id="track-name">&mdash;</div>
      <div id="track-artists"></div>
      <div id="track-album"></div>
    </div>
  </div>
</div>

<!-- Control cards -->
<div class="grid">

  <!-- Spotify -->
  <div class="card">
    <div class="card-title">Spotify</div>
    <label for="account-sel">Account</label>
    <select id="account-sel"></select>
    <div class="flex" style="margin-top:.75rem">
      <button class="btn btn-success flex1" id="btn-sp-start" onclick="startSpotify()">&#9654; Start</button>
      <button class="btn btn-ghost" id="btn-sp-stop" onclick="stopDisplay()" style="display:none">&#9632; Stop</button>
    </div>
    <div id="auth-banner">
      &#9888; Spotify authorisation required.<br>
      <a id="auth-link" href="#" target="_blank">Open Spotify auth page &#8599;</a>
      &mdash; approve, then the display will start automatically.
    </div>
  </div>

  <!-- Upload -->
  <div class="card">
    <div class="card-title">Upload Image</div>
    <div id="drop-zone"
         onclick="document.getElementById('file-input').click()"
         ondragover="event.preventDefault();this.classList.add('over')"
         ondragleave="this.classList.remove('over')"
         ondrop="handleDrop(event)">
      <img id="file-preview" src="" alt="">
      <div id="drop-label">Drop image here<br><small>or click to browse</small></div>
    </div>
    <input type="file" id="file-input" style="display:none"
           accept=".png,.jpg,.jpeg,.bmp,.gif,.webp"
           onchange="previewFile(this.files[0])">
    <button class="btn btn-primary btn-block" id="btn-upload"
            onclick="uploadImage()" disabled>Upload &amp; Display</button>
  </div>

  <!-- Quick actions -->
  <div class="card">
    <div class="card-title">Quick Actions</div>
    <button class="btn btn-danger btn-block" onclick="stopDisplay()">&#9632; Clear Screen</button>
    <p class="hint" style="margin-top:.75rem" id="uptime-text"></p>
  </div>

</div><!-- /grid -->

<!-- ── Settings accordions ────────────────────────────────────────────────── -->

<div class="acc-hdr" onclick="toggleAcc(this)">
  <span>&#127925; Spotify Credentials</span>
  <svg class="chevron" width="16" height="16" viewBox="0 0 24 24" fill="none"
       stroke="currentColor" stroke-width="2.5"><path d="M6 9l6 6 6-6"/></svg>
</div>
<div class="acc-body">
  <label>Client ID
    <input type="text" id="cfg-sp-client_id" placeholder="From developer.spotify.com/dashboard">
  </label>
  <label>Client Secret
    <input type="password" id="cfg-sp-client_secret" placeholder="Leave blank to keep current">
  </label>
  <label>Redirect URI
    <input type="url" id="cfg-sp-redirect_uri">
  </label>
  <p class="hint">The redirect URI must also be added in your Spotify app&rsquo;s dashboard under &ldquo;Redirect URIs&rdquo;.</p>
  <label>Poll interval (seconds)
    <input type="number" id="cfg-sp-poll_interval" min="1" max="60" step="1">
  </label>
  <hr>
  <div class="card-title">Accounts</div>
  <div id="accounts-list"></div>
  <button class="btn btn-ghost" style="margin-top:.5rem" onclick="addAccount()">+ Add account</button>
  <hr>
  <button class="btn btn-primary" onclick="saveSpotifyConfig()">Save Spotify Settings</button>
</div>

<div class="acc-hdr" onclick="toggleAcc(this)" style="margin-top:.5rem">
  <span>&#128444; Display Settings</span>
  <svg class="chevron" width="16" height="16" viewBox="0 0 24 24" fill="none"
       stroke="currentColor" stroke-width="2.5"><path d="M6 9l6 6 6-6"/></svg>
</div>
<div class="acc-body">
  <label>Fit mode
    <select id="cfg-disp-fit_mode">
      <option value="fit">fit &mdash; keep aspect ratio, pad edges</option>
      <option value="fill">fill &mdash; keep aspect ratio, crop edges</option>
      <option value="stretch">stretch &mdash; distort to exact size</option>
      <option value="center">center &mdash; no upscaling</option>
    </select>
  </label>
  <label>Brightness: <span id="brightness-val">80</span>%
    <input type="range" id="cfg-mat-brightness" min="0" max="100"
           oninput="document.getElementById('brightness-val').textContent=this.value">
  </label>
  <label>Slideshow interval (seconds)
    <input type="number" id="cfg-disp-slideshow_interval" min="1" step="0.5">
  </label>
  <label>Image transition
    <select id="cfg-disp-transition">
      <option value="fade">Fade (cross-dissolve)</option>
      <option value="slide_left">Slide left</option>
      <option value="slide_right">Slide right</option>
      <option value="slide_up">Slide up</option>
      <option value="slide_down">Slide down</option>
      <option value="random">Random</option>
      <option value="none">None (instant)</option>
    </select>
  </label>
  <label>Transition duration (seconds)
    <input type="number" id="cfg-disp-transition_duration" min="0.1" max="3" step="0.1">
  </label>
  <p class="hint">Applies to all image changes: uploads, slideshow, and Spotify album art.</p>
  <hr>
  <button class="btn btn-primary" onclick="saveDisplayConfig()">Save Display Settings</button>
</div>

<div class="acc-hdr" onclick="toggleAcc(this)" style="margin-top:.5rem">
  <span>&#9881; Matrix Hardware</span>
  <svg class="chevron" width="16" height="16" viewBox="0 0 24 24" fill="none"
       stroke="currentColor" stroke-width="2.5"><path d="M6 9l6 6 6-6"/></svg>
</div>
<div class="acc-body">
  <div class="warn-note">&#9888; Hardware changes require restarting the server to take effect.</div>
  <label>Hardware mapping
    <select id="cfg-mat-hardware_mapping">
      <option value="adafruit-hat">adafruit-hat</option>
      <option value="adafruit-hat-pwm">adafruit-hat-pwm (better quality, needs solder jumper)</option>
      <option value="regular">regular</option>
    </select>
  </label>
  <label>GPIO slowdown &nbsp;<small style="color:var(--muted)">(4 = Pi 4 &nbsp;|&nbsp; 2 = Pi 3 / Zero 2)</small>
    <input type="number" id="cfg-mat-gpio_slowdown" min="0" max="8">
  </label>
  <label>PWM LSB nanoseconds &nbsp;<small style="color:var(--muted)">(lower = better quality, more CPU)</small>
    <input type="number" id="cfg-mat-pwm_lsb_nanoseconds" min="50" max="500">
  </label>
  <div class="check-row">
    <input type="checkbox" id="cfg-mat-disable_hw_pulsing">
    <label for="cfg-mat-disable_hw_pulsing">Disable hardware pulsing (recommended for Pi 4)</label>
  </div>
  <hr>
  <button class="btn btn-primary" onclick="saveHardwareConfig()">Save Hardware Settings</button>
</div>

<div id="toast"></div>

<script>
'use strict';

let _selectedFile = null;
let _toastTimer   = null;

// ── Bootstrap ──────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadConfig();
  loadAccounts();
  setInterval(fetchStatus, 3000);
  fetchStatus();

  const p = new URLSearchParams(location.search);
  const t = p.get('toast');
  if (t) {
    const msgs = {
      spotify_ok:    '&#10003; Spotify connected!',
      spotify_error: '&#10007; Spotify auth error',
      spotify_fail:  '&#10007; Auth failed &mdash; try again',
      spotify_nocode:'&#10007; No auth code received',
    };
    showToast(msgs[t] || t);
    history.replaceState({}, '', '/');
  }
});

// ── Status polling ─────────────────────────────────────────────────────────────
async function fetchStatus() {
  try {
    const s = await (await fetch('/api/status')).json();
    const dot  = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    const npCard = document.getElementById('np-card');
    const btnStart = document.getElementById('btn-sp-start');
    const btnStop  = document.getElementById('btn-sp-stop');

    const modeLabels = {spotify:'Spotify', image:'Image', off:'Idle'};
    const dotClass   = {spotify:'purple',  image:'green',  off:''};
    dot.className  = 'dot ' + (dotClass[s.mode] || '');
    text.textContent = modeLabels[s.mode] || s.mode;

    // Now-playing card
    if (s.mode === 'spotify' && s.current_track && s.current_track.name) {
      npCard.style.display = 'block';
      document.getElementById('track-name').textContent    = s.current_track.name;
      document.getElementById('track-artists').textContent = s.current_track.artists || '';
      document.getElementById('track-album').textContent   = s.current_track.album || '';
      const art = document.getElementById('art-img');
      if (s.current_track.art_url && art.src !== s.current_track.art_url)
        art.src = s.current_track.art_url;
    } else {
      npCard.style.display = 'none';
    }

    // Start / stop toggle
    if (s.mode === 'spotify') {
      btnStart.style.display = 'none';
      btnStop.style.display  = '';
    } else {
      btnStart.style.display = '';
      btnStop.style.display  = 'none';
    }

    // Uptime
    const h = Math.floor(s.uptime_seconds / 3600);
    const m = Math.floor((s.uptime_seconds % 3600) / 60);
    document.getElementById('uptime-text').textContent =
      `Uptime: ${h}h ${m}m` + (s.simulate ? '  (simulation)' : '');
  } catch (_) {}
}

// ── Spotify control ────────────────────────────────────────────────────────────
async function loadAccounts() {
  try {
    const d = await (await fetch('/api/spotify/accounts')).json();
    const sel = document.getElementById('account-sel');
    sel.innerHTML = '';
    (d.accounts.length ? d.accounts : ['default']).forEach(a => {
      const o = document.createElement('option');
      o.value = a; o.textContent = a;
      if (a === d.default) o.selected = true;
      sel.appendChild(o);
    });
  } catch (_) {}
}

async function startSpotify() {
  const account = document.getElementById('account-sel').value;
  const r = await fetch('/api/mode/spotify', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({account}),
  });
  const d = await r.json();
  if (d.status === 'started') {
    showToast('&#9654; Spotify started');
    setTimeout(fetchStatus, 600);
  } else if (d.status === 'auth_required') {
    const banner = document.getElementById('auth-banner');
    banner.style.display = 'block';
    document.getElementById('auth-link').href = d.auth_url;
    showToast('&#9888; Spotify auth required &mdash; click the link above');
  } else {
    showToast('&#10007; ' + (d.message || d.error || 'Unknown error'));
  }
}

async function stopDisplay() {
  await fetch('/api/mode/off', {method: 'POST'});
  document.getElementById('auth-banner').style.display = 'none';
  showToast('&#9632; Display cleared');
  setTimeout(fetchStatus, 400);
}

// ── Image upload ───────────────────────────────────────────────────────────────
function previewFile(file) {
  if (!file) return;
  _selectedFile = file;
  const reader = new FileReader();
  reader.onload = e => {
    const img = document.getElementById('file-preview');
    img.src = e.target.result;
    img.style.display = 'block';
    document.getElementById('drop-label').textContent = file.name;
  };
  reader.readAsDataURL(file);
  document.getElementById('btn-upload').disabled = false;
}

function handleDrop(ev) {
  ev.preventDefault();
  document.getElementById('drop-zone').classList.remove('over');
  const file = ev.dataTransfer.files[0];
  if (file) previewFile(file);
}

async function uploadImage() {
  if (!_selectedFile) return;
  const form = new FormData();
  form.append('file', _selectedFile);
  const btn = document.getElementById('btn-upload');
  btn.disabled = true;
  btn.textContent = 'Uploading\u2026';
  try {
    const d = await (await fetch('/api/upload', {method:'POST', body:form})).json();
    if (d.ok) {
      showToast('&#10003; Displaying ' + d.filename);
      setTimeout(fetchStatus, 500);
    } else {
      showToast('&#10007; ' + (d.error || 'Upload failed'));
    }
  } catch (_) {
    showToast('&#10007; Upload error');
  }
  btn.disabled = false;
  btn.textContent = 'Upload \u0026 Display';
}

// ── Settings ───────────────────────────────────────────────────────────────────
async function loadConfig() {
  try {
    const cfg = await (await fetch('/api/config')).json();
    const sp  = cfg.spotify  || {};
    const d   = cfg.display  || {};
    const m   = cfg.matrix   || {};

    _v('cfg-sp-client_id',          sp.client_id   || '');
    _v('cfg-sp-client_secret',      ''); // never pre-fill
    _v('cfg-sp-redirect_uri',       sp.redirect_uri || '');
    _v('cfg-sp-poll_interval',      sp.poll_interval ?? 5);

    _v('cfg-disp-fit_mode',         d.fit_mode || 'fit');
    _v('cfg-disp-slideshow_interval', d.slideshow_interval ?? 10);
    _v('cfg-disp-transition',       d.transition || 'fade');
    _v('cfg-disp-transition_duration', d.transition_duration ?? 0.6);
    _v('cfg-mat-brightness',        m.brightness ?? 80);
    document.getElementById('brightness-val').textContent = m.brightness ?? 80;

    _v('cfg-mat-hardware_mapping',  m.hardware_mapping || 'adafruit-hat');
    _v('cfg-mat-gpio_slowdown',     m.gpio_slowdown ?? 4);
    _v('cfg-mat-pwm_lsb_nanoseconds', m.pwm_lsb_nanoseconds ?? 130);
    document.getElementById('cfg-mat-disable_hw_pulsing').checked =
      !!m.disable_hardware_pulsing;

    renderAccounts(sp.accounts || {});
  } catch (e) { console.error('loadConfig:', e); }
}

function _v(id, val) {
  const el = document.getElementById(id);
  if (el) el.value = val;
}

function renderAccounts(accounts) {
  const el = document.getElementById('accounts-list');
  el.innerHTML = '';
  Object.entries(accounts).forEach(([name, data]) => {
    el.appendChild(_accountRow(name, data.cache_path || ''));
  });
}

function addAccount() {
  document.getElementById('accounts-list').appendChild(_accountRow('', ''));
}

function _accountRow(name, cache) {
  const row = document.createElement('div');
  row.setAttribute('data-acct', '');
  row.style.cssText = 'display:flex;gap:.5rem;align-items:center;margin-top:.5rem';
  row.innerHTML =
    `<input type="text" data-acct-name value="${esc(name)}" placeholder="account name" style="flex:1">` +
    `<input type="text" data-acct-cache value="${esc(cache)}" placeholder=".spotify_token_name" style="flex:2">` +
    `<button class="btn btn-ghost" style="padding:.5rem .65rem" onclick="this.closest('[data-acct]').remove()">&#10005;</button>`;
  return row;
}

function _readAccounts() {
  const accounts = {};
  document.querySelectorAll('[data-acct]').forEach(row => {
    const name  = row.querySelector('[data-acct-name]').value.trim();
    const cache = row.querySelector('[data-acct-cache]').value.trim();
    if (name) accounts[name] = {cache_path: cache || `.spotify_token_${name}`};
  });
  return accounts;
}

async function saveSpotifyConfig() {
  const secret = document.getElementById('cfg-sp-client_secret').value;
  const payload = {spotify: {
    client_id:           document.getElementById('cfg-sp-client_id').value.trim(),
    redirect_uri:        document.getElementById('cfg-sp-redirect_uri').value.trim(),
    poll_interval:       parseFloat(document.getElementById('cfg-sp-poll_interval').value) || 5,
    accounts:            _readAccounts(),
  }};
  if (secret && secret !== '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022')
    payload.spotify.client_secret = secret;
  await _postConfig(payload);
  await loadAccounts();
}

async function saveDisplayConfig() {
  await _postConfig({
    display: {
      fit_mode:            document.getElementById('cfg-disp-fit_mode').value,
      slideshow_interval:  parseFloat(document.getElementById('cfg-disp-slideshow_interval').value) || 10,
      transition:          document.getElementById('cfg-disp-transition').value,
      transition_duration: parseFloat(document.getElementById('cfg-disp-transition_duration').value) || 0.6,
    },
    matrix: {
      brightness: parseInt(document.getElementById('cfg-mat-brightness').value) || 80,
    },
  });
}

async function saveHardwareConfig() {
  await _postConfig({matrix: {
    hardware_mapping:         document.getElementById('cfg-mat-hardware_mapping').value,
    gpio_slowdown:            parseInt(document.getElementById('cfg-mat-gpio_slowdown').value) || 4,
    pwm_lsb_nanoseconds:      parseInt(document.getElementById('cfg-mat-pwm_lsb_nanoseconds').value) || 130,
    disable_hardware_pulsing: document.getElementById('cfg-mat-disable_hw_pulsing').checked,
  }});
  showToast('&#9888; Restart the server to apply hardware changes');
}

async function _postConfig(data) {
  try {
    const d = await (await fetch('/api/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data),
    })).json();
    showToast(d.ok ? '&#10003; Settings saved' : '&#10007; ' + (d.error || 'Save failed'));
  } catch (_) {
    showToast('&#10007; Network error');
  }
}

// ── Accordion ──────────────────────────────────────────────────────────────────
function toggleAcc(hdr) {
  const open = hdr.classList.toggle('open');
  hdr.nextElementSibling.classList.toggle('open', open);
}

// ── Toast ──────────────────────────────────────────────────────────────────────
function showToast(msg) {
  const el = document.getElementById('toast');
  el.innerHTML = msg;
  el.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), 3200);
}

// ── Utils ──────────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/"/g,'&quot;')
    .replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
</script>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if not _FLASK_OK:
        sys.exit(
            "Flask is not installed.\n"
            "  pip3 install flask\n"
            "  or: pip3 install -r requirements_led_matrix_web.txt"
        )

    parser = argparse.ArgumentParser(
        prog='led_matrix_web.py',
        description='LED Matrix Web Controller — Flask UI + REST API',
    )
    parser.add_argument('-c', '--config', metavar='FILE',
                        help='YAML config file (default: led_matrix_display_config.yaml)')
    parser.add_argument('-s', '--simulate', action='store_true',
                        help='Simulation mode: Tkinter preview instead of hardware')
    parser.add_argument('--host', default='0.0.0.0',
                        help='Bind address (default: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=5000,
                        help='Port (default: 5000)')
    parser.add_argument('-v', '--verbose', action='store_true', help='Debug logging')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)-7s %(message)s',
        datefmt='%H:%M:%S',
    )

    cfg_path = args.config
    if not cfg_path:
        default = Path('led_matrix_display_config.yaml')
        if default.exists():
            cfg_path = str(default)
    cfg = load_config(cfg_path)

    effective_config_path = cfg_path or 'led_matrix_display_config.yaml'

    controller = DisplayController(cfg, simulate=args.simulate)
    app = create_app(controller, config_path=effective_config_path)

    log.info("LED Matrix Web Controller running on http://%s:%d", args.host, args.port)
    log.info("Open in browser: http://localhost:%d", args.port)
    # use_reloader=False is important — the reloader would create a second MatrixDisplay
    # load_dotenv=False avoids dotenv walking the filesystem (fails under systemd)
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False, load_dotenv=False)


if __name__ == '__main__':
    main()
