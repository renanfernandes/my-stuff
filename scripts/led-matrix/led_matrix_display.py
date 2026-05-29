#!/usr/bin/env python3
"""
LED Matrix Display — Adafruit RGB Matrix Bonnet + 64×64 Panel

Display modes:
  image      Show a single image file until Ctrl+C
  slideshow  Cycle through images in a directory
  spotify    Display album art of the currently playing Spotify track

Quick start (Raspberry Pi):
  sudo pip3 install Pillow pyyaml
  sudo pip3 install rpi-rgb-led-matrix        # hardware driver
  pip3 install spotipy requests               # Spotify (optional)

  python led_matrix_display.py image photo.png
  python led_matrix_display.py slideshow ~/photos/ --interval 5
  python led_matrix_display.py spotify

Development on a non-Pi machine (Tkinter preview window):
  python led_matrix_display.py --simulate image photo.png
  python led_matrix_display.py --simulate slideshow ~/photos/
"""

import argparse
import io
import logging
import os
import random
import signal
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

import yaml
from PIL import Image, ImageOps, ImageEnhance, ImageFilter

# ── Optional: RGB Matrix hardware ─────────────────────────────────────────────
try:
    from rgbmatrix import RGBMatrix, RGBMatrixOptions
    _MATRIX_OK = True
except ImportError:
    _MATRIX_OK = False

# ── Optional: Tkinter preview (simulation mode) ────────────────────────────────
# VNC / RaspiConnect web sessions often don't inherit DISPLAY — set a default.
if 'DISPLAY' not in os.environ:
    os.environ.setdefault('DISPLAY', ':0')

_TK_IMPORT_ERROR: Optional[str] = None
try:
    import tkinter as _tk
    from PIL import ImageTk as _ImageTk
    _TK_OK = True
except Exception as _e:
    _TK_OK = False
    _TK_IMPORT_ERROR = f"{type(_e).__name__}: {_e}"

# ── Optional: Spotify ─────────────────────────────────────────────────────────
try:
    import requests
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    _SPOTIFY_OK = True
except ImportError:
    _SPOTIFY_OK = False


log = logging.getLogger(__name__)

IMAGE_EXTENSIONS = frozenset({'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp', '.tiff'})

# ── Default configuration ──────────────────────────────────────────────────────

DEFAULT_CONFIG: dict = {
    'matrix': {
        'rows': 64,
        'cols': 64,
        'chain_length': 1,
        'parallel': 1,
        # Hardware mapping for the Bonnet.
        # Use 'adafruit-hat-pwm' for better quality (requires soldering a jumper).
        'hardware_mapping': 'adafruit-hat',
        # GPIO slowdown: 4 for Pi 4, 2 for Pi 3 / Zero 2
        'gpio_slowdown': 4,
        'brightness': 80,           # 0–100 (high values draw significant current)
        'pwm_lsb_nanoseconds': 130, # Valid range: 50–3000. Lower = better quality, more CPU
        'pwm_bits': 11,             # 1–11; lower = less flicker, less colour depth
        'panel_type': '',           # Set to 'FM6126A' for high-refresh panels with that driver IC
        'show_refresh_rate': False,
        'disable_hardware_pulsing': False,
    },
    'display': {
        # How to fit images onto the 64×64 canvas:
        #   fit     — keep aspect ratio, pad remainder with background colour
        #   fill    — keep aspect ratio, crop centre excess
        #   stretch — stretch to exact size (may distort)
        #   center  — centre without upscaling; scale down only if too large
        'fit_mode': 'fit',
        'background': [0, 0, 0],      # RGB pad colour used by fit / center modes
        'slideshow_interval': 10.0,   # Seconds to show each image
        'loop': True,
        'shuffle': False,
        'transition': 'fade',         # none | fade | slide_left | slide_right | slide_up | slide_down | random
        'transition_duration': 0.6,   # Seconds for the transition animation
        'transition_fps': 20,         # Frames per second during transition
        # Post-processing applied after downscaling to the panel resolution.
        # These compensate for the softening and colour loss of heavy downscaling.
        'sharpen': 1.2,       # Unsharp-mask strength: 0.0 = off, 1.0 = subtle, 2.0+ = aggressive
        'saturation': 1.2,    # Colour saturation multiplier: 1.0 = unchanged, 1.3 = vivid
        'contrast': 1.1,      # Contrast multiplier: 1.0 = unchanged
    },
    'spotify': {
        'client_id': '',
        'client_secret': '',
        'redirect_uri': 'http://localhost:8888/callback',
        'poll_interval': 5.0,         # Seconds between Spotify API polls
        'clear_on_stop': True,        # Clear screen when nothing is playing
        'clear_delay': 60.0,          # Seconds of inactivity before clearing
        # Named accounts — each gets its own token cache so you can switch
        # between Spotify users without re-authenticating.
        # Run with:  python led_matrix_display.py spotify --account wife
        'default_account': 'default',
        'accounts': {
            'default': {'cache_path': '.spotify_token_cache'},
        },
    },
}

# ── Config helpers ─────────────────────────────────────────────────────────────

def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, returning a new dict."""
    result = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def load_config(path: Optional[str]) -> dict:
    if not path:
        return dict(DEFAULT_CONFIG)
    with open(path) as fh:
        user_cfg = yaml.safe_load(fh) or {}
    return _deep_merge(DEFAULT_CONFIG, user_cfg)


# ── Image helpers ──────────────────────────────────────────────────────────────

def _enhance_image(
    img: Image.Image,
    sharpen: float = 0.0,
    saturation: float = 1.0,
    contrast: float = 1.0,
) -> Image.Image:
    """Apply post-downscale enhancements to compensate for heavy resizing."""
    if saturation != 1.0:
        img = ImageEnhance.Color(img).enhance(saturation)
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if sharpen > 0.0:
        img = img.filter(ImageFilter.UnsharpMask(radius=0.6, percent=int(sharpen * 80), threshold=2))
    return img


def prepare_image(
    img: Image.Image,
    width: int,
    height: int,
    fit_mode: str = 'fit',
    bg: Tuple[int, int, int] = (0, 0, 0),
    sharpen: float = 0.0,
    saturation: float = 1.0,
    contrast: float = 1.0,
) -> Image.Image:
    """Resize / crop / pad *img* to exactly (width × height)."""
    img = img.convert('RGB')  # always a fresh copy, correct mode

    if fit_mode == 'stretch':
        result = img.resize((width, height), Image.LANCZOS)
        return _enhance_image(result, sharpen, saturation, contrast)

    if fit_mode == 'fill':
        result = ImageOps.fit(img, (width, height), Image.LANCZOS)
        return _enhance_image(result, sharpen, saturation, contrast)

    # 'fit' and 'center' both build a canvas
    canvas = Image.new('RGB', (width, height), bg)

    if fit_mode == 'fit':
        img.thumbnail((width, height), Image.LANCZOS)
    elif fit_mode == 'center':
        if img.width > width or img.height > height:
            img.thumbnail((width, height), Image.LANCZOS)
    else:
        raise ValueError(f"Unknown fit_mode: {fit_mode!r}.  Choose: fit, fill, stretch, center")

    offset = ((width - img.width) // 2, (height - img.height) // 2)
    canvas.paste(img, offset)
    return _enhance_image(canvas, sharpen, saturation, contrast)


def load_image_file(path: str) -> Image.Image:
    """Open an image from disk and return a copy (closes the file handle).

    When the process runs under sudo and hits a PermissionError (e.g. the
    calling user's home directory is mode 700), this temporarily drops back
    to the original user's UID/GID to open the file, then restores root.
    """
    try:
        with Image.open(path) as img:
            if hasattr(img, 'n_frames') and img.n_frames > 1:
                img.seek(0)
            return img.copy()
    except PermissionError:
        sudo_uid = os.environ.get('SUDO_UID')
        sudo_gid = os.environ.get('SUDO_GID')
        if sudo_uid is None:
            raise
        uid = int(sudo_uid)
        gid = int(sudo_gid) if sudo_gid else os.getgid()
        log.debug("PermissionError — retrying as uid=%d gid=%d", uid, gid)
        os.setegid(gid)
        os.seteuid(uid)
        try:
            with Image.open(path) as img:
                if hasattr(img, 'n_frames') and img.n_frames > 1:
                    img.seek(0)
                return img.copy()
        finally:
            os.seteuid(0)
            os.setegid(0)


# ── Tkinter simulator ──────────────────────────────────────────────────────────

class _TkSimulator:
    """Scales the matrix image up and renders it in a Tkinter window."""

    def __init__(self, rows: int, cols: int, scale: int = 8):
        if not _TK_OK:
            raise RuntimeError(
                "Tkinter or Pillow ImageTk not available — cannot open simulator window."
            )
        self._scale = scale
        self._root = _tk.Tk()
        self._root.title('LED Matrix — Simulator')
        self._root.resizable(False, False)
        self._label = _tk.Label(self._root, bd=0)
        self._label.pack()
        self._tk_img = None  # prevent garbage collection

    def show(self, img: Image.Image):
        scaled = img.resize(
            (img.width * self._scale, img.height * self._scale),
            Image.NEAREST,
        )
        self._tk_img = _ImageTk.PhotoImage(scaled)
        self._label.configure(image=self._tk_img)
        self._root.update_idletasks()
        self._root.update()

    def close(self):
        try:
            self._root.destroy()
        except Exception:
            pass


# ── Matrix controller ──────────────────────────────────────────────────────────

class MatrixDisplay:
    """
    Unified display controller.

    On a Raspberry Pi with rpi-rgb-led-matrix installed it drives the hardware.
    On any other machine (or when --simulate is passed) it opens a scaled-up
    Tkinter preview window instead.
    """

    def __init__(self, config: dict, simulate: bool = False):
        self.cfg = config
        self.rows: int = config['matrix']['rows']
        self.cols: int = config['matrix']['cols']
        self._running = True
        self._matrix = None
        self._canvas = None   # double-buffer canvas for flicker-free updates
        self._sim: Optional[_TkSimulator] = None

        use_sim = simulate or not _MATRIX_OK
        if use_sim:
            if not _MATRIX_OK and not simulate:
                log.warning(
                    "rgbmatrix library not found — running in simulation mode.\n"
                    "  Build it from source on the Raspberry Pi:\n"
                    "  https://github.com/hzeller/rpi-rgb-led-matrix"
                )
            if _TK_OK:
                self._sim = _TkSimulator(self.rows, self.cols)
                log.info("Simulator window opened (%d×%d × 8 zoom)", self.rows, self.cols)
            else:
                log.warning(
                    "Simulation mode — Tkinter unavailable (images will only be logged).\n"
                    "  Import error: %s\n"
                    "  Possible fixes:\n"
                    "    sudo apt install python3-tk\n"
                    "    If inside a venv: make sure it was created with --system-site-packages\n"
                    "      python3 -m venv --system-site-packages venv\n"
                    "    Or check which Python is running: python3 -c 'import sys; print(sys.executable)'",
                    _TK_IMPORT_ERROR or "unknown",
                )
        else:
            self._matrix = self._build_matrix()
            self._canvas = self._matrix.CreateFrameCanvas()
            log.info("Hardware matrix ready (%d×%d)", self.rows, self.cols)

        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    # ── hardware setup ─────────────────────────────────────────────────────────

    def _build_matrix(self) -> 'RGBMatrix':
        m = self.cfg['matrix']
        opts = RGBMatrixOptions()
        opts.rows = m['rows']
        opts.cols = m['cols']
        opts.chain_length = m['chain_length']
        opts.parallel = m['parallel']
        opts.hardware_mapping = m['hardware_mapping']
        opts.gpio_slowdown = m['gpio_slowdown']
        opts.brightness = m['brightness']
        opts.pwm_lsb_nanoseconds = m['pwm_lsb_nanoseconds']
        opts.pwm_bits = m.get('pwm_bits', 11)
        if m.get('panel_type'):
            opts.panel_type = m['panel_type']
        opts.show_refresh_rate = int(m['show_refresh_rate'])
        opts.disable_hardware_pulsing = bool(m['disable_hardware_pulsing'])
        return RGBMatrix(options=opts)

    # ── public API ─────────────────────────────────────────────────────────────

    def show(self, img: Image.Image):
        """Display a prepared (rows × cols) RGB image."""
        if self._matrix is not None:
            # Draw to the back buffer then swap on the next VSync — eliminates flicker.
            self._canvas.SetImage(img.convert('RGB'))
            self._canvas = self._matrix.SwapOnVSync(self._canvas)
        elif self._sim is not None:
            self._sim.show(img)
        else:
            log.debug("(no-op) image %dx%d", img.width, img.height)

    def clear(self):
        if self._matrix is not None:
            self._matrix.Clear()

    def close(self):
        self.clear()
        if self._sim is not None:
            self._sim.close()
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def _handle_signal(self, signum, _frame):
        log.info("Signal %d received — shutting down", signum)
        self.close()
        sys.exit(0)



def _fit(img: Image.Image, display: MatrixDisplay, cfg: dict) -> Image.Image:
    d = cfg['display']
    bg = tuple(d['background'])
    log.debug(
        "Fit: %dx%d → %dx%d  mode=%s  sharpen=%.1f  sat=%.1f  contrast=%.1f",
        img.width, img.height, display.cols, display.rows,
        d['fit_mode'], d.get('sharpen', 0.0), d.get('saturation', 1.0), d.get('contrast', 1.0),
    )
    result = prepare_image(
        img, display.cols, display.rows,
        fit_mode=d['fit_mode'], bg=bg,
        sharpen=d.get('sharpen', 0.0),
        saturation=d.get('saturation', 1.0),
        contrast=d.get('contrast', 1.0),
    )
    log.debug("Fit: done")
    return result


# ── Transition engine ──────────────────────────────────────────────────────────

def apply_transition(
    display: MatrixDisplay,
    old_img: Optional[Image.Image],
    new_img: Image.Image,
    transition: str = 'fade',
    duration: float = 0.6,
    fps: int = 20,
    stop_event=None,
) -> None:
    """Animate a transition from old_img to new_img on the display.

    If old_img is None or transition is 'none', shows new_img immediately.
    Supported transitions: none, fade, slide_left, slide_right, slide_up,
    slide_down, random.

    All frames are pre-rendered before playback so Pillow CPU spikes don't
    cause uneven frame timing (which shows as flicker on the matrix).
    """
    if old_img is None or transition == 'none' or duration <= 0:
        display.show(new_img)
        return

    if transition == 'random':
        transition = random.choice(
            ['fade', 'slide_left', 'slide_right', 'slide_up', 'slide_down']
        )

    W, H = new_img.width, new_img.height
    old_rgb = old_img.convert('RGB')
    new_rgb = new_img.convert('RGB')
    n_frames = max(2, int(duration * fps))
    delay = duration / n_frames

    log.info("Transition: %s  frames=%d  %.0fms/frame", transition, n_frames + 1, delay * 1000)

    # ── Pre-render all frames before touching the display ─────────────────────
    frames = []
    for i in range(n_frames + 1):
        alpha = i / n_frames

        if transition == 'fade':
            # Use perceptual (gamma) alpha: linear blending makes the midpoint
            # appear darker on LED displays because LEDs are linear but eyes
            # are not.  A sqrt curve keeps perceived brightness even.
            alpha_p = alpha ** 0.5
            frame = Image.blend(old_rgb, new_rgb, alpha_p)

        elif transition == 'slide_left':
            # Use round() instead of int() so each frame advances at least 1px
            offset = round(alpha * W)
            frame = Image.new('RGB', (W, H))
            old_w = W - offset
            if old_w > 0:
                frame.paste(old_rgb.crop((offset, 0, W, H)), (0, 0))
            if offset > 0:
                frame.paste(new_rgb.crop((0, 0, min(offset, W), H)), (old_w, 0))

        elif transition == 'slide_right':
            offset = round(alpha * W)
            frame = Image.new('RGB', (W, H))
            old_w = W - offset
            if old_w > 0:
                frame.paste(old_rgb.crop((0, 0, old_w, H)), (offset, 0))
            if offset > 0:
                frame.paste(new_rgb.crop((W - offset, 0, W, H)), (0, 0))

        elif transition == 'slide_up':
            offset = round(alpha * H)
            frame = Image.new('RGB', (W, H))
            old_h = H - offset
            if old_h > 0:
                frame.paste(old_rgb.crop((0, offset, W, H)), (0, 0))
            if offset > 0:
                frame.paste(new_rgb.crop((0, H - offset, W, H)), (0, old_h))

        elif transition == 'slide_down':
            offset = round(alpha * H)
            frame = Image.new('RGB', (W, H))
            old_h = H - offset
            if old_h > 0:
                frame.paste(old_rgb.crop((0, 0, W, old_h)), (0, offset))
            if offset > 0:
                frame.paste(new_rgb.crop((0, 0, W, offset)), (0, 0))

        else:
            frame = new_rgb

        frames.append(frame)

    # ── Play back at a consistent rate using monotonic clock ──────────────────
    start = time.monotonic()
    for idx, frame in enumerate(frames):
        if stop_event and stop_event.is_set():
            break
        display.show(frame)
        target = start + (idx + 1) * delay
        remaining = target - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)


def _transition_cfg(cfg: dict) -> dict:
    """Extract transition settings from cfg['display']."""
    d = cfg.get('display', {})
    return {
        'transition': d.get('transition', 'fade'),
        'transition_duration': d.get('transition_duration', 0.6),
        'transition_fps': d.get('transition_fps', 20),
    }


# ── Display modes ──────────────────────────────────────────────────────────────

def mode_image(display: MatrixDisplay, path: str, cfg: dict, stop_event=None):
    """Show a single image; block until interrupted."""
    img = load_image_file(path)
    t = _transition_cfg(cfg)
    apply_transition(display, None, _fit(img, display, cfg),
                     transition=t['transition'],
                     duration=t['transition_duration'],
                     fps=t['transition_fps'],
                     stop_event=stop_event)
    log.info("Showing '%s' — press Ctrl+C to exit", path)
    while display.running and not (stop_event and stop_event.is_set()):
        time.sleep(0.5)


def mode_slideshow(display: MatrixDisplay, directory: str, cfg: dict, stop_event=None):
    """Cycle through every image in *directory*."""
    d = cfg['display']
    interval = float(d['slideshow_interval'])
    loop = bool(d['loop'])
    do_shuffle = bool(d['shuffle'])

    def _running():
        return display.running and not (stop_event and stop_event.is_set())

    all_paths = sorted(
        p for p in Path(directory).iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )
    if not all_paths:
        log.error("No images found in: %s", directory)
        return

    log.info(
        "Slideshow: %d images, %.1fs each, loop=%s, shuffle=%s",
        len(all_paths), interval, loop, do_shuffle,
    )

    t = _transition_cfg(cfg)
    prev_img: Optional[Image.Image] = None

    while _running():
        paths = list(all_paths)
        if do_shuffle:
            random.shuffle(paths)

        for path in paths:
            if not _running():
                return
            try:
                img = load_image_file(str(path))
                fitted = _fit(img, display, cfg)
                apply_transition(display, prev_img, fitted,
                                 transition=t['transition'],
                                 duration=t['transition_duration'],
                                 fps=t['transition_fps'],
                                 stop_event=stop_event)
                prev_img = fitted
                log.info("[slideshow] %s", path.name)
            except Exception as exc:
                log.warning("Skipping %s — %s", path.name, exc)
                continue

            deadline = time.monotonic() + interval
            while _running() and time.monotonic() < deadline:
                time.sleep(0.1)

        if not loop:
            break


# ── Spotify ────────────────────────────────────────────────────────────────────

class SpotifyPoller:
    """
    Polls the Spotify Web API for the currently-playing track.

    get_current_art() returns a PIL Image when the track changes, or None
    when the same track is still playing or nothing is playing.
    """

    _SCOPE = 'user-read-currently-playing user-read-playback-state'

    def __init__(self, cfg: dict, account: Optional[str] = None):
        if not _SPOTIFY_OK:
            raise RuntimeError(
                "Spotify libraries not found.  Install them with:\n"
                "  pip install spotipy requests"
            )
        sp_cfg = cfg['spotify']
        if not sp_cfg.get('client_id') or not sp_cfg.get('client_secret'):
            raise ValueError(
                "spotify.client_id and spotify.client_secret must be set in the config.\n"
                "Register your app at: https://developer.spotify.com/dashboard"
            )

        account_name = account or sp_cfg.get('default_account', 'default')
        accounts = sp_cfg.get('accounts', {})
        if account_name not in accounts:
            raise ValueError(
                f"Account '{account_name}' not found in config.\n"
                f"Available accounts: {', '.join(accounts) or 'none'}\n"
                "Add it under spotify.accounts in led_matrix_display_config.yaml"
            )
        cache_path = accounts[account_name].get('cache_path', f'.spotify_token_{account_name}')
        log.info("Using Spotify account: %s  (cache: %s)", account_name, cache_path)
        auth = SpotifyOAuth(
            client_id=sp_cfg['client_id'],
            client_secret=sp_cfg['client_secret'],
            redirect_uri=sp_cfg['redirect_uri'],
            scope=self._SCOPE,
            cache_path=cache_path,
            open_browser=False,
        )
        # Trigger auth flow now so any interactive prompt happens at startup.
        # If a valid cached token exists it is used silently.
        # On first run: copy the printed URL into any browser, approve,
        # then paste the FULL redirect URL (http://127.0.0.1:8888/callback?code=…)
        # back into this terminal — even if the browser shows "can't reach this page".
        cached = auth.get_cached_token()
        if not cached:
            auth_url = auth.get_authorize_url()
            print(
                "\n──────────────────────────────────────────────────────────\n"
                "  Spotify authorisation required (one-time only)\n"
                "──────────────────────────────────────────────────────────\n"
                f"  1. Open this URL in any browser:\n\n     {auth_url}\n\n"
                "  2. Approve the permissions.\n"
                "  3. The browser will show 'can't reach this page' — that's OK.\n"
                "  4. Copy the FULL URL from the address bar and paste it below.\n"
                "──────────────────────────────────────────────────────────"
            )
            response_url = input("\n  Paste the redirect URL here: ").strip()
            code = auth.parse_response_code(response_url)
            auth.get_access_token(code)
        self.sp = spotipy.Spotify(auth_manager=auth)
        self._last_id: Optional[str] = None
        self._last_change_time: float = 0.0
        self._current_art: Optional[Image.Image] = None

    @property
    def last_change_time(self) -> float:
        return self._last_change_time

    @property
    def current_art(self) -> Optional[Image.Image]:
        return self._current_art

    def get_current_art(self) -> Optional[Image.Image]:
        """
        Returns album art for the new track, or None if:
        - nothing is playing, or
        - the same track as last poll is still playing.
        """
        try:
            result = self.sp.currently_playing(additional_types='track,episode')
        except Exception as exc:
            log.warning("Spotify API error: %s", exc)
            return None

        if not result or not result.get('is_playing'):
            return None

        item = result.get('item')
        if not item:
            return None

        # Use URI as unique ID — more robust than 'id' (covers local files, podcasts)
        track_id = item.get('uri') or item.get('id', '')
        if track_id == self._last_id:
            return None  # Same track — nothing to update

        artists = ', '.join(a['name'] for a in item.get('artists', [])) or item.get('show', {}).get('name', '')
        log.info("Now playing: %s — %s", item.get('name', '?'), artists)

        url = self._pick_art_url(item)
        if not url:
            log.warning("No album art available for this track")
            return None

        art = self._fetch(url)
        if art:
            self._last_id = track_id
            self._last_change_time = time.monotonic()
            self._current_art = art
        return art

    @staticmethod
    def _pick_art_url(item: dict) -> Optional[str]:
        """
        Locate the album art URL from a Spotify track or episode item.
        Prefers the smallest image that is at least 64 px wide to save bandwidth.
        """
        # Regular tracks → album images
        images: list = item.get('album', {}).get('images', [])
        # Podcast episodes → episode images, then show images
        if not images:
            images = item.get('images', [])
        if not images:
            images = item.get('show', {}).get('images', [])
        if not images:
            return None

        candidates = [i for i in images if i.get('width', 0) >= 64]
        chosen = (candidates[-1] if candidates else images[-1])
        return chosen['url']

    @staticmethod
    def _fetch(url: str) -> Optional[Image.Image]:
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            with Image.open(io.BytesIO(resp.content)) as img:
                return img.copy()
        except Exception as exc:
            log.warning("Failed to fetch album art: %s", exc)
            return None


def mode_spotify(display: MatrixDisplay, cfg: dict, account: Optional[str] = None,
                 stop_event=None):
    """Poll Spotify and update the display whenever the track changes.

    Single account: pass --account NAME, or let it use spotify.default_account.
    All accounts:   omit --account when multiple accounts are defined;
                    whichever account changed tracks most recently wins.
    """
    sp_cfg = cfg['spotify']
    interval = float(sp_cfg['poll_interval'])
    all_accounts = sp_cfg.get('accounts', {})

    if account:
        pollers = [SpotifyPoller(cfg, account=account)]
        log.info("Spotify mode (account: %s) — polling every %.1fs.", account, interval)
    elif len(all_accounts) > 1:
        pollers = [SpotifyPoller(cfg, account=name) for name in all_accounts]
        log.info(
            "Spotify mode (%d accounts, last-changed-wins) — polling every %.1fs.",
            len(pollers), interval,
        )
    else:
        pollers = [SpotifyPoller(cfg)]
        log.info("Spotify mode — polling every %.1fs.", interval)

    clear_on_stop = bool(sp_cfg.get('clear_on_stop', True))
    clear_delay   = float(sp_cfg.get('clear_delay', 60.0))
    t = _transition_cfg(cfg)

    log.info("Press Ctrl+C to exit.")

    last_playing_at: float = time.monotonic()
    screen_cleared: bool = False
    prev_art: Optional[Image.Image] = None

    while display.running and not (stop_event and stop_event.is_set()):
        any_changed = False
        any_playing = False
        for poller in pollers:
            if poller.get_current_art() is not None:
                any_changed = True
            # A poller is "playing" if it changed recently or still has art
            try:
                result = poller.sp.currently_playing(additional_types='track,episode')
                if result and result.get('is_playing'):
                    any_playing = True
            except Exception:
                pass

        if any_changed:
            # Show art from whichever account changed most recently
            winner = max(pollers, key=lambda p: p.last_change_time)
            if winner.current_art is not None:
                new_fitted = _fit(winner.current_art, display, cfg)
                apply_transition(
                    display, prev_art, new_fitted,
                    transition=t['transition'],
                    duration=t['transition_duration'],
                    fps=t['transition_fps'],
                    stop_event=stop_event,
                )
                prev_art = new_fitted

        if any_playing:
            last_playing_at = time.monotonic()
            screen_cleared = False
        elif clear_on_stop and not screen_cleared:
            if time.monotonic() - last_playing_at >= clear_delay:
                log.info("Nothing playing for %.0fs — clearing screen", clear_delay)
                display.clear()
                screen_cleared = True

        time.sleep(interval)


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog='led_matrix_display.py',
        description='Adafruit RGB Matrix Bonnet controller (64×64)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s image cover.png\n"
            "  %(prog)s slideshow ~/Pictures/ --interval 8 --shuffle\n"
            "  %(prog)s spotify\n"
            "  %(prog)s --simulate image cover.png\n"
        ),
    )
    parser.add_argument(
        '-c', '--config', metavar='FILE',
        help='YAML config file (auto-detected: led_matrix_display_config.yaml)',
    )
    parser.add_argument(
        '-s', '--simulate', action='store_true',
        help='Simulation mode: render in a Tkinter window instead of hardware',
    )
    parser.add_argument('-v', '--verbose', action='store_true', help='Debug logging')

    subs = parser.add_subparsers(dest='mode', required=True)

    # image
    p_img = subs.add_parser('image', help='Display a single image file')
    p_img.add_argument('path', help='Image path (PNG, JPG, BMP, …)')

    # slideshow
    p_ss = subs.add_parser('slideshow', help='Cycle through images in a directory')
    p_ss.add_argument('directory', help='Directory containing images')
    p_ss.add_argument('--interval', type=float, metavar='SEC',
                      help='Seconds per image (overrides config)')
    p_ss.add_argument('--shuffle', action='store_true',
                      help='Randomise order (overrides config)')
    p_ss.add_argument('--no-loop', dest='no_loop', action='store_true',
                      help='Play once without looping (overrides config)')

    # spotify
    p_sp = subs.add_parser('spotify', help='Show album art of the current Spotify track')
    p_sp.add_argument(
        '--account', metavar='NAME',
        help='Spotify account name as defined in config (default: spotify.default_account)',
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)-7s %(message)s',
        datefmt='%H:%M:%S',
    )

    # Locate config
    cfg_path = args.config
    if not cfg_path:
        default = Path('led_matrix_display_config.yaml')
        if default.exists():
            cfg_path = str(default)
    cfg = load_config(cfg_path)

    # CLI overrides for slideshow
    if args.mode == 'slideshow':
        if args.interval is not None:
            cfg['display']['slideshow_interval'] = args.interval
        if args.shuffle:
            cfg['display']['shuffle'] = True
        if args.no_loop:
            cfg['display']['loop'] = False

    display = MatrixDisplay(cfg, simulate=args.simulate)

    if args.mode == 'image':
        mode_image(display, args.path, cfg)
    elif args.mode == 'slideshow':
        mode_slideshow(display, args.directory, cfg)
    elif args.mode == 'spotify':
        mode_spotify(display, cfg, account=getattr(args, 'account', None))

    display.close()


if __name__ == '__main__':
    main()
