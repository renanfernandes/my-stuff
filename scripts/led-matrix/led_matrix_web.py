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
import re as _re
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import yaml
from PIL import Image

# ── Capture Retry-After from the rate-limit warning message ────────────────
# Spotify logs "Retry will occur after: N s" before raising.  Capture that
# value so we can back off for the amount Spotify actually asked for.
class _SpotifyRateLimitCapture(logging.Filter):
    retry_after: float = 0.0

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        m = _re.search(r'Retry will occur after:\s*(\d+)', msg, _re.I)
        if m:
            _SpotifyRateLimitCapture.retry_after = float(m.group(1))
            log.debug('Captured Spotify retry delay from log: %ss', m.group(1))
        return True

_spotipy_rate_filter = _SpotifyRateLimitCapture()
for name in (
    '',
    'spotipy',
    'requests',
    'requests.packages',
    'requests.packages.urllib3',
    'requests.packages.urllib3.util',
    'requests.packages.urllib3.util.retry',
    'urllib3',
    'urllib3.util',
    'urllib3.util.retry',
):
    logging.getLogger(name).addFilter(_spotipy_rate_filter)
# ─────────────────────────────────────────────────────────────────────────────

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


def _is_invalid_grant_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return 'invalid_grant' in text or 'refresh token' in text and 'expired' in text


def _discard_token_cache(cache_path: str):
    if not cache_path:
        return
    try:
        p = Path(cache_path)
        if p.exists():
            p.unlink()
            log.warning("Removed stale Spotify token cache: %s", cache_path)
    except Exception as err:
        log.warning("Could not remove stale token cache %s: %s", cache_path, err)


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

        # Resolve which account(s) to watch.
        # Priority: explicit UI selection > watch_account config > all cached accounts
        watch = account or sp_cfg.get('watch_account', 'all')
        if watch == 'all':
            account_name = sp_cfg.get('default_account', next(iter(all_accounts), 'default'))
        else:
            account_name = watch

        if account_name not in all_accounts:
            return {'status': 'error', 'message': f"Account '{account_name}' not found in config"}

        effective_redirect_uri = redirect_uri or sp_cfg['redirect_uri']

        try:
            from spotipy.oauth2 import SpotifyOAuth
        except ImportError:
            return {'status': 'error', 'message': 'spotipy not installed'}

        # Build auth objects for ALL accounts that have a cached token.
        # The requested account is always included (for the OAuth check).
        auths: dict = {}  # name -> SpotifyOAuth
        for name, acct in all_accounts.items():
            cache_path = acct.get('cache_path', f'.spotify_token_{name}')
            a = SpotifyOAuth(
                client_id=sp_cfg['client_id'],
                client_secret=sp_cfg['client_secret'],
                redirect_uri=effective_redirect_uri,
                scope='user-read-currently-playing user-read-playback-state',
                cache_path=cache_path,
                open_browser=False,
            )
            try:
                if a.get_cached_token():
                    auths[name] = a
            except Exception as exc:
                if _is_invalid_grant_error(exc):
                    _discard_token_cache(cache_path)
                    log.warning("Spotify token expired for account '%s' — re-authorisation required.", name)
                else:
                    log.warning("Could not load Spotify token cache for account '%s': %s", name, exc)

        # If the requested account has no token, start OAuth for it
        if account_name not in auths:
            a = SpotifyOAuth(
                client_id=sp_cfg['client_id'],
                client_secret=sp_cfg['client_secret'],
                redirect_uri=effective_redirect_uri,
                scope='user-read-currently-playing user-read-playback-state',
                cache_path=all_accounts[account_name].get('cache_path', f'.spotify_token_{account_name}'),
                open_browser=False,
            )
            auth_url = a.get_authorize_url()
            with self._lock:
                self._pending_auth[account_name] = (a, account)
            return {'status': 'auth_required', 'auth_url': auth_url, 'account': account_name}

        self._auth_objects.update(auths)
        # If a specific account was requested (not 'all'), restrict to just that one
        if watch != 'all' and watch in auths:
            launch_auths = {watch: auths[watch]}
        else:
            launch_auths = auths
        self._launch_spotify_thread(launch_auths)
        return {'status': 'started', 'account': ', '.join(launch_auths.keys())}

    def complete_oauth(self, code: str) -> bool:
        """Called from /callback — complete any pending OAuth and start Spotify."""
        for account_name in list(self._pending_auth.keys()):
            auth, account = self._pending_auth[account_name]
            try:
                auth.get_access_token(code)
                del self._pending_auth[account_name]
                self._auth_objects[account_name] = auth
                # Re-launch with all available auth objects
                self._launch_spotify_thread(dict(self._auth_objects))
                return True
            except Exception as exc:
                log.error("OAuth completion for '%s' failed: %s", account_name, exc)
        return False

    def _launch_spotify_thread(self, auths: dict):
        """Start the spotify polling loop for all accounts in *auths*."""
        with self._lock:
            self._stop_current()
            self._set_mode('spotify')
        self._thread = threading.Thread(
            target=self._spotify_loop, args=(auths,), daemon=True
        )
        self._thread.start()

    def _spotify_loop(self, auths: dict):
        """
        Poll all accounts simultaneously; display art from whichever changed last.
        *auths* is a dict of account_name -> SpotifyOAuth.
        """
        try:
            import spotipy as _spotipy
            # Disable Spotipy's retry-enabled requests Session; this forces
            # immediate 429 exceptions so we can honour Spotify's wait time.
            clients = {
                name: _spotipy.Spotify(
                    auth_manager=a,
                    requests_session=False,
                    retries=0,
                    status_retries=0,
                )
                for name, a in auths.items()
            }
            log.info("Spotify loop started for accounts: %s", ', '.join(clients))

            # Per-account state
            last_track:   dict = {n: None  for n in clients}
            last_art:     dict = {n: None  for n in clients}
            last_changed: dict = {n: 0.0   for n in clients}

            last_playing_at: float = time.time()
            screen_cleared: bool = False
            prev_art: Optional[Image.Image] = None

            while not self._stop_event.is_set():
                sp_cfg = self.cfg['spotify']
                interval      = float(sp_cfg['poll_interval'])
                clear_on_stop = bool(sp_cfg.get('clear_on_stop', True))
                clear_delay   = float(sp_cfg.get('clear_delay', 60.0))
                t = _transition_cfg(self.cfg)
                transition          = t['transition']
                transition_duration = t['transition_duration']
                transition_fps      = t['transition_fps']

                any_playing = False
                best_name: Optional[str] = None
                best_art:  Optional[Image.Image] = None

                for name, sp in clients.items():
                    try:
                        result = sp.currently_playing(additional_types='track,episode')
                        is_playing = bool(result and result.get('is_playing'))
                        if not is_playing:
                            continue
                        any_playing = True
                        item = result.get('item')
                        if not item:
                            continue
                        track_id = item.get('uri') or item.get('id', '')
                        if track_id == last_track[name]:
                            # Same track still playing — candidate for display
                            # if no one else changed more recently
                            if best_name is None or last_changed[name] > last_changed.get(best_name, 0):
                                best_name = name
                                best_art  = last_art[name]
                            continue
                        # New track for this account
                        last_track[name] = track_id
                        artists = ', '.join(
                            a['name'] for a in item.get('artists', [])
                        ) or item.get('show', {}).get('name', '')
                        log.info("[%s] Now playing: %s — %s", name, item.get('name', '?'), artists)
                        url = SpotifyPoller._pick_art_url(item)
                        if url:
                            art = SpotifyPoller._fetch(url)
                            if art:
                                last_art[name]     = _fit(art, self._display, self.cfg)
                                last_changed[name] = time.time()
                                self.current_track = {
                                    'name':    item.get('name', ''),
                                    'artists': artists,
                                    'album':   item.get('album', {}).get('name', ''),
                                    'art_url': url,
                                    'account': name,
                                }
                                # This account just changed — it wins
                                best_name = name
                                best_art  = last_art[name]
                    except Exception as exc:
                        retry_after = 0.0

                        if _is_invalid_grant_error(exc):
                            cache_path = self.cfg.get('spotify', {}).get('accounts', {}).get(name, {}).get(
                                'cache_path', f'.spotify_token_{name}'
                            )
                            _discard_token_cache(cache_path)
                            log.warning("Spotify session expired for '%s' — please re-authorise in the web UI.", name)
                            continue

                        try:
                            from spotipy.exceptions import SpotifyException
                            if isinstance(exc, SpotifyException) and exc.http_status == 429:
                                headers = getattr(exc, 'headers', None) or {}
                                retry_after = float(headers.get('Retry-After', headers.get('retry-after', 0)) or 0)
                                if not retry_after:
                                    retry_after = float(getattr(exc, 'reason', '') or 0)
                        except Exception:
                            pass

                        if not retry_after:
                            m = _re.search(r'Retry will occur after:\s*(\d+)', str(getattr(exc, 'msg', '') or exc), _re.I)
                            if not m:
                                m = _re.search(r'Retry will occur after:\s*(\d+)', str(getattr(exc, 'reason', '') or ''), _re.I)
                            if m:
                                retry_after = float(m.group(1))
                        if not retry_after:
                            retry_after = float(getattr(_SpotifyRateLimitCapture, 'retry_after', 0) or 0)
                        if retry_after:
                            log.warning("Spotify rate limit [%s] — backing off for %.0fs", name, retry_after)
                            self._stop_event.wait(timeout=retry_after)
                        else:
                            log.warning("Spotify poll error [%s]: %s", name, exc)

                if best_art is not None and best_art is not prev_art:
                    apply_transition(
                        self._display, prev_art, best_art,
                        transition=transition,
                        duration=transition_duration,
                        fps=transition_fps,
                        stop_event=self._stop_event,
                    )
                    prev_art = best_art

                if any_playing:
                    last_playing_at = time.time()
                    screen_cleared  = False
                elif clear_on_stop and not screen_cleared:
                    if time.time() - last_playing_at >= clear_delay:
                        log.info("Nothing playing for %.0fs — clearing screen", clear_delay)
                        self._display.clear()
                        self.current_track = {}
                        last_track = {n: None for n in clients}
                        screen_cleared = True

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
        return jsonify({'ok': True, 'mode': controller.mode})

    @app.route('/api/mode/spotify', methods=['POST'])
    def api_mode_spotify():
        data = request.get_json(silent=True) or {}
        # Use the redirect_uri from config if set; otherwise fall back to this
        # server's own /callback so the browser lands back here after OAuth.
        configured_uri = controller.cfg.get('spotify', {}).get('redirect_uri', '')
        web_redirect_uri = configured_uri or (request.host_url.rstrip('/') + '/callback')
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

    _ui_file = Path(__file__).parent / 'index.html'

    @app.route('/')
    def index():
        return _ui_file.read_text(encoding='utf-8'), 200, {'Content-Type': 'text/html; charset=utf-8'}

    return app


# ─────────────────────────────────────────────────────────────────────────────
# (UI moved to index.html)
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

    # Auto-start Spotify with the default account if credentials are configured
    sp_cfg = cfg.get('spotify', {})
    if sp_cfg.get('client_id') and sp_cfg.get('client_secret'):
        # Use redirect_uri from config if set; otherwise build from bind address.
        configured_uri = sp_cfg.get('redirect_uri', '')
        if configured_uri:
            auto_redirect = configured_uri
        else:
            bind_host = args.host if args.host != '0.0.0.0' else 'localhost'
            auto_redirect = f"http://{bind_host}:{args.port}/callback"
        result = controller.start_spotify(
            account=sp_cfg.get('default_account'),
            redirect_uri=auto_redirect,
        )
        if result.get('status') == 'auth_required':
            log.info("Spotify auth required — open the web UI to authorise: %s", result.get('auth_url', ''))
        elif result.get('status') == 'started':
            log.info("Spotify auto-started (account: %s)", result.get('account'))
        else:
            log.warning("Spotify auto-start: %s", result.get('message', result))

    app = create_app(controller, config_path=effective_config_path)

    log.info("LED Matrix Web Controller running on http://%s:%d", args.host, args.port)
    log.info("Open in browser: http://localhost:%d", args.port)
    # use_reloader=False is important — the reloader would create a second MatrixDisplay
    # load_dotenv=False avoids dotenv walking the filesystem (fails under systemd)
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False, load_dotenv=False)


if __name__ == '__main__':
    main()
