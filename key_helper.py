"""
Droid Tycoon Helper - two helpers for the Droid Tycoon map:
  Fishing  finds the yellow key tiles, reads them and presses them.
  Clicker  Scrap Hit keeps left-clicking, Potion Grab presses F every 2-20 seconds
           (only while Fortnite is in front).
  Traits   keeps rerolling the left, right or both droids at the trait scanner
           until they get a trait you picked. Never rolls Iconic droids.

Hotkeys (work while the game is focused):
  Insert      open the mode menu / start the chosen mode / stop
  Up / Down   pick Fishing or Traits while the menu is open
              (Traits then shows the trait list: Up/Down move, Left/Right sets
              Off / Any / Rainbow-only, Insert starts rolling)
  Page Up     rebirth +1 on the website (Page Down = undo the last one)
  F9 / F10    add / remove the droid on screen (droid screen or blueprint card) in
              your LMS Droid Tycoon inventory - press once to check, again to save
  Home        draw a custom scan area (optional - the default is automatic)
  Delete      save debug pictures (debug_raw.png, debug_detect.png, debug_letters.png)
  End         quit

You don't need to mark the area: it looks for the yellow key tiles in the middle
of your main screen by itself, and only presses when "PRESS TO FISH!" is showing
above them (set "require_prompt": false in config.json to turn that check off).
The overlay hides while paused and pops up for a
couple of seconds when something changes (set "hide_when_paused": false in
config.json to keep it showing).
"""

import ctypes
import ctypes.wintypes
import difflib
import json
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk

import cv2
import mss
import numpy as np
import pydirectinput
import pytesseract
from pynput import keyboard


def _use_windows_certificates():
    """Check HTTPS certificates the way Windows and browsers do. Python's own check
    can fail with 'unable to get issuer certificate' when a site (like GitHub) changes
    its certificates - that broke updates. Falls back to the certifi bundle."""
    try:
        import truststore
        truststore.inject_into_ssl()
        return "windows"
    except Exception:
        pass
    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        return "certifi"
    except Exception:
        return "python"


CERT_SOURCE = _use_windows_certificates()

# Always work in the folder this program lives in (double-clicking or starting as
# admin can otherwise start it in C:\Windows\System32 and lose config.json)
BASE_DIR = os.path.dirname(os.path.abspath(
    sys.executable if getattr(sys, "frozen", False) else __file__))
os.chdir(BASE_DIR)

# With no console window (double-click / .exe), keep messages in a log file
if sys.stdout is None or sys.stderr is None or not sys.stdout.isatty():
    try:
        _log = open(os.path.join(BASE_DIR, "window.log" if "--window" in sys.argv
                                 else "key_helper.log"), "w", buffering=1,
                    encoding="utf-8", errors="replace")
        sys.stdout = sys.stderr = _log
    except Exception:
        pass
else:
    try:  # a console that can't show symbols like the arrow shouldn't crash anything
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except Exception:
        pass

# ------------------------------------------------------------------ version ---
# Bump VERSION every time you publish an update (e.g. "1.0.1", "1.1.0").
VERSION = "1.8.8"
# Your GitHub "username/repository" where the .exe releases are published.
UPDATE_REPO = "sclawson21/sw-key-helper"

# LMS Droid Tycoon (Firebase) - same project as the website. Copy these two values
# from the website's firebase-config.js. While they say PASTE..., no login is needed.
FIREBASE_API_KEY = "AIzaSyBo1Fn_s1kgbULMYt5CuJOE-GkQxeTfLZQ"
FIREBASE_PROJECT = "droid-tycoon-79e66"
LOGIN_DOMAIN = "droid-tracker.app"   # logins are username@droid-tracker.app
OFFLINE_GRACE_DAYS = 7               # keep working this long without internet

CONFIG_FILE = "config.json"
DEFAULTS = {
    "auto_update": True,        # check GitHub for a newer version every time it starts
    "search_area": None,        # None = automatic (middle of the main screen)
    "allowed_keys": "EFZQC",    # only these letters are accepted
    "press_delay": 0.06,        # seconds between key presses (Page Up/Down change it)
    "key_hold": 0.02,           # how long each key is held down
    "scan_interval": 0.03,      # seconds between screen reads while running
    "after_round_delay": 0.25,  # pause after typing a row
    "settle_time": 0.1,         # a new row must look the same this long before pressing
    "retry_after": 1.0,         # if the same row is still there after this long, type it again
    "min_tile_size": 20,        # smallest tile size in pixels that counts as a key
    "require_prompt": True,     # only press when "PRESS TO FISH!" is above the keys
    "prompt_text": "PRESS TO FISH",
    # --- Traits ---
    # which traits to stop on: "any", "rainbow" (rainbow tier only) or "off".
    # You can also change these in the picker after choosing Traits.
    "trait_wanted": {
        "Overclocked": "any",
        "Chip Finder": "any",
        "Pickaxe Level": "rainbow",
        "Critical Amount": "rainbow",
    },
    "stop_if_unreadable": True,  # stop if a trait that might be wanted can't be read
    "roll_wait": 7.0,           # seconds the roll animation takes before pressing F again
    "max_rolls": 0,             # stop after this many rolls (0 = no limit)
    "trait_droids": "left",     # which droid to reroll: "left", "right" or "both" (left first)
    "max_cost": "500T",         # stop when the next roll costs more than this ("off" = no limit)
    # --- Auto clicker ---
    "clicker_cps": 10,          # Scrap Hit: clicks per second (Page Up/Down change it)
    "clicker_kind": "scrap",    # "scrap" = keep left-clicking, "potion" = press F every few seconds
    "potion_interval": 5,       # Potion Grab: seconds between F presses (2-20, Page Up/Down)
    "clicker_window": "Fortnite",  # only click while this window is in front ("" = anywhere)
    "interact_key": "f",
    # --- Inventory (LMS Droid Tycoon website) ---
    "inventory_player": "",     # which website player F9/F10 update (pick it in the tray menu)
    "kyber_default": "n",       # Kyber colour for added droids: n=Not Activated, g, b, p
    "inv_add_key": "f9",        # (older setting - the "hotkeys" list below is used now)
    "inv_remove_key": "f10",
    # --- Hotkeys (change them in the app window: Hotkeys tab) ---
    "hotkeys": {"menu": "insert", "reb_up": "page_up", "reb_down": "page_down",
                "inv_add": "f9", "inv_remove": "f10", "area": "home",
                "snapshot": "delete", "quit": "end"},
    # --- App window (website on top, controls below) ---
    "website_url": "",          # your LMS Droid Tycoon website address
    "open_window_on_start": True,
    "panel_height": 360,
    "show_rebirth": True,       # next rebirth's credits and droids under the badge while idle
    # --- Overlay ---
    "monitor": "auto",          # which screen to watch: "auto" (Fortnite's screen) or 1, 2, ...
    "overlay": True,            # show the on-screen info box
    "hide_when_paused": True,   # hide the box while paused (it pops up briefly on changes)
    "overlay_x": 20,
    "overlay_y": None,          # None = just under the name/ID line at the top of the screen
    "mini_badge": True,         # small "running" badge in the top-left when idle
    "discreet": False,          # discreet mode: hide the idle badge (click the tray icon)
    "mini_x": 8,
    "mini_y": None,
    "tray_icon": True,          # icon by the clock: right-click to update or quit
    "tesseract_path": r"C:\Program Files\Tesseract-OCR\tesseract.exe",
}
DELAY_STEP, DELAY_MIN, DELAY_MAX = 0.01, 0.01, 0.50

# Make coordinates match real screen pixels on scaled (125%/150%) displays
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                cfg.update(json.load(f))
        except Exception:
            print("config.json is broken - using defaults.")
    return cfg


def save_config():
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


cfg = load_config()
if cfg["allowed_keys"] == "EFZQ":  # older config files - the game added C
    cfg["allowed_keys"] = "EFZQC"
if cfg.get("overlay_y") == 120:      # old default spot -> new spot under the name/ID line
    cfg["overlay_y"] = None
if cfg.get("mini_y") == 8:
    cfg["mini_y"] = None
_saved_hotkeys = cfg.get("hotkeys") if cfg.get("hotkeys") is not DEFAULTS["hotkeys"] else None
_saved_hotkeys = dict(_saved_hotkeys or {})
for _old, _new in (("faster", "reb_up"), ("slower", "reb_down")):     # renamed in 1.8
    if _old in _saved_hotkeys:
        _saved_hotkeys.setdefault(_new, _saved_hotkeys.pop(_old))
cfg["hotkeys"] = {**DEFAULTS["hotkeys"], **_saved_hotkeys}
if not _saved_hotkeys:                # older config: keep a changed F9/F10 setting
    cfg["hotkeys"]["inv_add"] = cfg.get("inv_add_key") or "f9"
    cfg["hotkeys"]["inv_remove"] = cfg.get("inv_remove_key") or "f10"


def find_tesseract():
    """Use the copy of Tesseract built into the .exe if there is one, otherwise an
    installed copy - so the .exe works on any PC without installing anything."""
    bundled = os.path.join(getattr(sys, "_MEIPASS", BASE_DIR), "tesseract")
    candidates = [
        os.path.join(bundled, "tesseract.exe"),
        os.path.join(BASE_DIR, "tesseract", "tesseract.exe"),
        cfg["tesseract_path"],
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tesseract-OCR",
                     "tesseract.exe"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            pytesseract.pytesseract.tesseract_cmd = path
            tessdata = os.path.join(os.path.dirname(path), "tessdata")
            if os.path.isdir(tessdata):
                os.environ["TESSDATA_PREFIX"] = tessdata
            return path
    return None  # fall back to whatever is on PATH


find_tesseract()
pydirectinput.PAUSE = 0  # we handle our own timing

with mss.mss() as _s:
    MONITORS = [dict(m) for m in _s.monitors[1:]]   # every screen; [0] is the main one
PRIMARY = MONITORS[0]
_screen_cache = {"time": 0.0, "mon": PRIMARY}


def fortnite_monitor():
    """The screen the Fortnite window is on (None if Fortnite isn't open)."""
    if sys.platform != "win32":
        return None
    try:
        u = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def each(hwnd, _):
            if u.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                u.GetWindowTextW(hwnd, buf, 256)
                if "fortnite" in buf.value.lower():
                    found.append(hwnd)
            return True

        u.EnumWindows(each, 0)
        for hwnd in found:
            r = ctypes.wintypes.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
            for m in MONITORS:
                if m["left"] <= cx < m["left"] + m["width"] and m["top"] <= cy < m["top"] + m["height"]:
                    return m
    except Exception as e:
        print("Couldn't find the Fortnite window:", e)
    return None


def screen():
    """The screen the app watches: the one picked in the tray, or (Auto) the one
    Fortnite is on, falling back to the main screen."""
    pick = str(cfg.get("monitor", "auto"))
    if pick.isdigit() and 1 <= int(pick) <= len(MONITORS):
        return MONITORS[int(pick) - 1]
    if time.time() - _screen_cache["time"] > 2:
        _screen_cache.update(time=time.time(), mon=fortnite_monitor() or PRIMARY)
    return _screen_cache["mon"]


state = {
    "enabled": False, "quit": False, "snapshot": False, "select_area": False,
    "reading": "", "pressed": "", "scan_ms": 0, "inputs": 0,
    "message": "", "error": "", "show_until": 0.0,
    "mode": "fishing", "menu_open": False, "menu_choice": 0, "menu_time": 0.0,
    "trait": "", "rolls": 0, "status": "",
    "menu_stage": "mode", "pick_index": 0, "choice_index": 0,
    "trait_queue": [], "trait_side": "left", "side_checked": None,
    "fish_round": {"active": False, "chips": 0, "chips0": None},
    "fish_last_prompt": 0.0,
    "inv_pending": None, "inv_busy": False, "inv_lines": [], "inv_until": 0.0,
    "inv_color": None, "capture_hide": False,
    "clicks": 0, "spent": 0.0, "cost": "", "pending_roll": None, "roll_times": [],
    "hk_capture": None, "hk_error": "", "update_status": "",
}
MODES = ["fishing", "traits", "clicker"]
MODE_NAMES = ["Fishing", "Traits (droid scanner)", "Auto Clicker"]
DROID_CHOICES = [("left", "Left droid"), ("right", "Right droid"), ("both", "Both (take turns - faster)")]
CLICKER_CHOICES = [("scrap", "Scrap Hit (keep left-clicking)"), ("potion", "Potion Grab (press F every few seconds)")]
COST_STEPS = ["off", "1B", "10B", "100B", "1T", "10T", "50T", "100T", "500T", "600T", "700T",
              "800T", "900T", "1Qa", "2Qa", "3Qa", "4Qa", "5Qa"]   # 5Qa is the cap
SUFFIX = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12, "QA": 1e15, "QI": 1e18,
          "SX": 1e21, "SP": 1e24}
STATS_FILE = "trait_stats.json"
TIERS = ["default", "gold", "diamond", "rainbow"]

# How each trait is recognised (words from its name or description)
TRAIT_WORDS = {
    "Overclocked": ["OVERCLOCKED", "DOUBLES COMPANION"],
    "Chip Finder": ["CHIP FINDER", "WHEN BUILDING DROIDS"],
    "Pickaxe Level": ["PICKAXE LEVEL"],
    "Critical Amount": ["CRITICAL AMOUNT"],
    "Critical Chance": ["CRITICAL CHANCE"],
    "Credit Per Second": ["CREDIT PER SECOND", "CREDITS PER SECOND", "MULTIPLIER"],
    "Health": ["MAX HEALTH"],
    "Crafting Speed": ["CRAFTING SPEED"],
    "Upgrade Chip Bonus": ["UPGRADE CHIP BONUS", "CHIP BONUS"],
    "Scrap Specialist": ["SCRAP SPECIALIST", "SCRAP STATIONS"],
    "Fusion Expert": ["FUSION EXPERT", "AMOUNT ON FUSIONS"],
    "Mission Efficiency": ["MISSION EFFICIENCY", "MISSION SPEED"],
    "Offline Earnings": ["OFFLINE EARNINGS"],
    "Passive Credits": ["PASSIVE CREDITS", "SEC AS COMPANION"],
    "Fusion Preservation": ["FUSION PRESERVATION", "DROID KEPT", "TRAIT LOCKS"],
    "Golden Splash Refill": ["GOLDEN SPLASH", "HITTING DEPOT"],
}
# display order (app window and in-game picker): rainbow-only traits first, then the
# chip / scrap / fusion / mission ones, then the rest
TRAIT_ORDER = ["Overclocked", "Chip Finder", "Fusion Preservation", "Passive Credits",
               "Golden Splash Refill", "Upgrade Chip Bonus", "Scrap Specialist", "Pickaxe Level",
               "Fusion Expert", "Mission Efficiency"]
TRAIT_NAMES = TRAIT_ORDER + [n for n in TRAIT_WORDS if n not in TRAIT_ORDER]
# these only ever come in rainbow, so an unreadable non-rainbow trait can't be one
ALWAYS_RAINBOW = {"Overclocked", "Chip Finder", "Passive Credits",
                  "Fusion Preservation", "Golden Splash Refill"}
CHOICES = ["off", "any", "diamond", "rainbow"]    # diamond = Diamond or Rainbow


def choices_for(name):
    """Always-rainbow traits only make sense as Off / Rainbow."""
    return ["off", "rainbow"] if name in ALWAYS_RAINBOW else CHOICES


def notify(msg, seconds=2.5):
    print(msg)
    state["message"] = msg
    state["show_until"] = time.time() + seconds  # pop the overlay up for a while


# ---------------------------------------------------------------- hotkeys ---

def start_mode():
    state["menu_open"] = False
    state["rolls"], state["trait"], state["status"] = 0, "", ""
    state["clicks"], state["spent"], state["cost"] = 0, 0.0, ""
    state["active_secs"] = 0.0
    state["earn"] = {"samples": [], "start": None, "last": None, "made": 0.0,
                     "prev": None, "text": "", "text_time": 0.0}
    state["pending_roll"], state["roll_times"] = None, []
    state["last_cost"] = None
    if state["mode"] == "fishing":
        state["enabled"] = True
        threading.Thread(target=fish_tracker, daemon=True).start()
    if state["mode"] == "traits":
        state["trait_queue"] = {"left": ["left"], "right": ["right"],
                                "both": ["left", "right"]}.get(cfg["trait_droids"], ["left"])
        state["trait_queue_all"] = list(state["trait_queue"])
        state["trait_side"] = state["trait_queue"].pop(0)
        state["side_checked"] = None
        state["trait_droid"] = ""
        fetch_shared()
    state["enabled"] = True
    notify(f"{state['mode'].title()} started")


HOTKEY_ACTIONS = [            # (id, what it does) - keys live in cfg["hotkeys"]
    ("menu", "Open menu / start / stop"),
    ("reb_up", "Rebirth +1 (marks the next rebirth complete)"),
    ("reb_down", "Rebirth \u22121 (undoes the last one)"),
    ("inv_add", "Add droid on screen to inventory"),
    ("inv_remove", "Remove droid on screen from inventory"),
    ("area", "Draw the fishing scan area"),
    ("snapshot", "Save debug pictures"),
    ("quit", "Quit Droid Tycoon Helper"),
]
APP_WINDOW_TITLE = "Droid Tycoon Helper"
_KEY_LABELS = {"page_up": "Page Up", "page_down": "Page Down", "esc": "Esc",
               "caps_lock": "Caps Lock", "num_lock": "Num Lock", "scroll_lock": "Scroll Lock",
               "print_screen": "Print Screen", "pause": "Pause", "menu": "Menu key"}


def key_name(key):
    """A stable name for a pynput key: 'insert', 'f9', 'page_up', 'k', 'vk96'..."""
    if isinstance(key, keyboard.Key):
        return key.name
    ch = getattr(key, "char", None)
    if ch and ch.isprintable() and not ch.isspace():
        return ch.lower()
    vk = getattr(key, "vk", None)
    return f"vk{vk}" if vk is not None else ""


def pretty_key(name):
    if not name:
        return "(none)"
    if name in _KEY_LABELS:
        return _KEY_LABELS[name]
    if re.fullmatch(r"f\d+", name):
        return name.upper()
    if name.startswith("vk") and name[2:].isdigit():
        v = int(name[2:])
        return f"Num {v - 96}" if 96 <= v <= 105 else f"Key {v}"
    return name.replace("_", " ").title() if len(name) > 1 else name.upper()


def hk(action):
    return cfg["hotkeys"].get(action, "")


def hk_label(action):
    return pretty_key(hk(action))


def hotkey_problem(name):
    """Why a key can't be a hotkey (it's needed in the game or by the app), or ''."""
    game = set(cfg["allowed_keys"].lower()) | {cfg["interact_key"].lower(), "w", "a", "s", "d"}
    if name in ("up", "down", "left", "right"):
        return "Arrow keys are used by the on-screen menus"
    if name in ("esc", "enter", "space", "tab", "backspace"):
        return f"{pretty_key(name)} is needed in the game"
    if name.startswith(("shift", "ctrl", "alt", "cmd")):
        return "Shift / Ctrl / Alt can't be used on their own"
    if name in game:
        return f"{name.upper()} is used in the game (fishing keys, F, WASD)"
    return ""


def finish_capture(name):
    """A key was pressed while the Hotkeys tab was waiting for one."""
    action = state["hk_capture"]
    if name == "esc":
        state["hk_capture"], state["hk_error"] = None, ""
        return
    why = hotkey_problem(name)
    if why:
        state["hk_error"] = why                   # keep waiting for another key
        return
    old = hk(action)
    for other, k in cfg["hotkeys"].items():       # taken? swap the two
        if other != action and k == name:
            cfg["hotkeys"][other] = old
    cfg["hotkeys"][action] = name
    cfg["inv_add_key"], cfg["inv_remove_key"] = hk("inv_add"), hk("inv_remove")
    save_config()
    state["hk_capture"], state["hk_error"] = None, ""
    notify(f"{dict(HOTKEY_ACTIONS)[action]}: {pretty_key(name)}", 3)


def on_key(key):
    K = keyboard.Key
    name = key_name(key)
    if state.get("hk_capture"):
        finish_capture(name)
        return
    if foreground_title() == APP_WINDOW_TITLE:
        return                                    # typing in the app window - not for us
    act = next((a for a, k in cfg["hotkeys"].items() if k and k == name), None)
    if act == "menu":
        if state["enabled"]:                      # running -> stop
            state["enabled"] = False
            notify(f"{state['mode'].title()} stopped")
        elif state["menu_open"] and state["menu_stage"] == "mode":
            state["mode"] = MODES[state["menu_choice"]]
            state["menu_time"] = time.time()
            if state["mode"] == "traits":         # traits -> which droid(s) first
                state["menu_stage"] = "droids"
                state["choice_index"] = next((i for i, c in enumerate(DROID_CHOICES)
                                              if c[0] == cfg["trait_droids"]), 0)
            elif state["mode"] == "clicker":      # clicker -> scrap or potion
                state["menu_stage"] = "clicker"
                state["choice_index"] = next((i for i, c in enumerate(CLICKER_CHOICES)
                                              if c[0] == cfg["clicker_kind"]), 0)
            else:
                start_mode()
        elif state["menu_open"] and state["menu_stage"] == "droids":
            cfg["trait_droids"] = DROID_CHOICES[state["choice_index"]][0]
            state["menu_stage"] = "traits"            # then the trait picker
            state["menu_time"] = time.time()
        elif state["menu_open"] and state["menu_stage"] == "clicker":
            cfg["clicker_kind"] = CLICKER_CHOICES[state["choice_index"]][0]
            save_config()
            start_mode()
        elif state["menu_open"]:                  # trait picker -> start rolling
            if not any(v != "off" for v in cfg["trait_wanted"].values()):
                notify("Turn on at least one trait first (Left/Right)")
                return
            save_config()
            start_mode()
        else:                                     # stopped -> open the menu
            state["menu_open"], state["menu_stage"] = True, "mode"
            state["menu_choice"] = MODES.index(state["mode"])
            state["menu_time"] = time.time()
    elif (key in (K.up, K.down, K.left, K.right) and not state["menu_open"]
          and adjust_pending(key)):
        pass
    elif key in (K.up, K.down) and state["menu_open"]:
        state["menu_time"] = time.time()
        step = 1 if key == K.down else -1
        if state["menu_stage"] == "mode":
            state["menu_choice"] = (state["menu_choice"] + step) % len(MODES)
        elif state["menu_stage"] in ("droids", "clicker"):
            n = len(DROID_CHOICES if state["menu_stage"] == "droids" else CLICKER_CHOICES)
            state["choice_index"] = (state["choice_index"] + step) % n
        else:
            state["pick_index"] = (state["pick_index"] + step) % (len(TRAIT_NAMES) + 1)
    elif key in (K.left, K.right) and state["menu_open"] and state["menu_stage"] == "traits":
        state["menu_time"] = time.time()
        step = 1 if key == K.right else -1
        if state["pick_index"] == len(TRAIT_NAMES):          # last row: cost limit
            cur = cfg["max_cost"] if cfg["max_cost"] in COST_STEPS else "500T"
            i = min(len(COST_STEPS) - 1, max(0, COST_STEPS.index(cur) + step))   # stops at the ends
            cfg["max_cost"] = COST_STEPS[i]
        else:
            name = TRAIT_NAMES[state["pick_index"]]
            opts = choices_for(name)
            cur = cfg["trait_wanted"].get(name, "off")
            cur = cur if cur in opts else ("rainbow" if cur != "off" else "off")
            cfg["trait_wanted"][name] = opts[(opts.index(cur) + step) % len(opts)]
    elif act in ("reb_up", "reb_down"):
        rebirth_hotkey(1 if act == "reb_up" else -1)
    elif act == "inv_add":
        inventory_hotkey("add")
    elif act == "inv_remove":
        inventory_hotkey("remove")
    elif act == "area" and not state["enabled"]:   # not while a mode is running
        state["select_area"] = True
    elif act == "snapshot":
        state["snapshot"] = True
    elif act == "quit":
        state["quit"] = True
        return False


def rebirth_hotkey(step):
    """Page Up / Page Down in the game: mark the next rebirth done, or undo the last."""
    if not accounts_enabled() or not _account.get("refresh"):
        notify("Sign in to change rebirths", 4)
        return

    def work():
        try:
            player = current_player()
            if not player:
                notify("No website player yet", 4)
                return
            url = _players_url(player["id"])
            doc = _http(url, token=id_token())
            fields = {k: _fs_value(v) for k, v in doc.get("fields", {}).items()}
            cycle = str(fields.get("cycle") or 1)
            done = fields.get("done") or {}
            levels = sorted(set(int(x) for x in (done.get(cycle) or [])))
            if step > 0:
                nxt = next((n for n in range(1, 41) if n not in levels), None)
                if nxt is None:
                    notify(f"{player['name']}: all 40 rebirths are already done", 5)
                    return
                levels = sorted(levels + [nxt])
                msg = f"\u2714 Rebirth {nxt} complete for {player['name']}"
            else:
                if not levels:
                    notify(f"{player['name']}: no rebirths to undo", 4)
                    return
                last = levels.pop()
                msg = f"Undid rebirth {last} for {player['name']}"
            done[cycle] = levels
            body = {"fields": {"done": _to_fs(done),
                               "updatedAt": {"integerValue": str(int(time.time() * 1000))}}}
            _http(url + "?updateMask.fieldPaths=done&updateMask.fieldPaths=updatedAt", body,
                  token=id_token(), method="PATCH")
            state["data_version"] = state.get("data_version", 0) + 1
            top = max(levels) if levels else 0
            notify(f"{msg} \u00b7 now at rebirth {top} (cycle {cycle})", 6)
        except Exception as e:
            notify(f"Couldn't change the rebirth: {e}"[:120], 6)
    threading.Thread(target=work, daemon=True).start()


def change_speed(step):
    """+1 = faster, -1 = slower (fishing press delay, or the clicker's speed)."""
    if state["mode"] == "clicker":
        if cfg["clicker_kind"] == "potion":       # faster = shorter wait
            cfg["potion_interval"] = int(min(20, max(2, cfg["potion_interval"] - step)))
            notify(f"Potion Grab: F every {cfg['potion_interval']} seconds")
        else:
            cfg["clicker_cps"] = int(min(50, max(1, cfg["clicker_cps"] + step)))
            notify(f"Scrap Hit: {cfg['clicker_cps']} clicks per second")
    else:
        cfg["press_delay"] = round(min(DELAY_MAX, max(DELAY_MIN,
                                                      cfg["press_delay"] - step * DELAY_STEP)), 3)
        notify(f"Delay between presses: {int(cfg['press_delay'] * 1000)} ms")
    save_config()


# ---------------------------------------------------------- tile detection ---

def get_search_area():
    a = cfg["search_area"]
    if a:
        m = screen()
        # a box too small to hold a whole key row (e.g. from an accidental Home
        # press) would make fishing see nothing - fall back to automatic
        if a.get("width", 0) >= 0.2 * m["width"] and a.get("height", 0) >= 0.08 * m["height"]:
            return a
        print("Custom scan area too small - using automatic:", a)
        cfg["search_area"] = None
        save_config()
        notify("Custom scan area was too small - switched back to automatic", 6)
    m = screen()
    return {"left": m["left"] + int(m["width"] * 0.15),
            "top": m["top"] + int(m["height"] * 0.10),
            "width": int(m["width"] * 0.70),
            "height": int(m["height"] * 0.65)}


def pale_square(hsv_tile):
    """Mask of the pale letter square inside a tile (less saturated than the yellow)."""
    sat, val = hsv_tile[:, :, 1].astype(np.float32), hsv_tile[:, :, 2]
    tile_sat = np.percentile(sat, 85)
    return ((sat < tile_sat * 0.7) & (val > 150)).astype(np.uint8)


_prompt_cache = {"key": None, "ok": False, "until": 0.0}


def in_dark_panel(bgr, tiles):
    """The real key row sits inside a black panel - check just above the tiles."""
    h = int(np.median([t[3] for t in tiles]))
    top = min(t[1] for t in tiles)
    x0, x1 = tiles[0][0], tiles[-1][0] + tiles[-1][2]
    y0, y1 = top - int(h * 0.12), top - int(h * 0.04)
    if y0 < 0 or y1 <= y0:
        return False
    val = cv2.cvtColor(bgr[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)[:, :, 2]
    return (val < 80).mean() > 0.6


def prompt_visible(bgr, tiles):
    """Read the yellow text just above the key row and check it says PRESS TO FISH."""
    h = int(np.median([t[3] for t in tiles]))
    top = min(t[1] for t in tiles)
    cx = (tiles[0][0] + tiles[-1][0] + tiles[-1][2]) // 2
    cache_key = (top // 8, h // 8)
    now = time.time()
    if _prompt_cache["key"] == cache_key and now < _prompt_cache["until"]:
        return _prompt_cache["ok"]

    y0, y1 = max(0, int(top - 1.05 * h)), max(0, int(top - 0.2 * h))
    x0, x1 = max(0, int(cx - 3.2 * h)), min(bgr.shape[1], int(cx + 3.2 * h))
    ok = False
    if y1 - y0 > 8 and x1 - x0 > 8:
        hsv = cv2.cvtColor(bgr[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
        text_mask = cv2.inRange(hsv, (15, 120, 190), (40, 255, 255))
        img = cv2.resize(255 - text_mask, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
        img = cv2.copyMakeBorder(img, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
        text = re.sub("[^A-Z]", "", pytesseract.image_to_string(img, config="--psm 7").upper())
        want = re.sub("[^A-Z]", "", cfg["prompt_text"].upper())
        words = [w for w in re.findall("[A-Z]{4,}", cfg["prompt_text"].upper())]
        ok = (any(w in text for w in words)
              or difflib.SequenceMatcher(None, text, want).ratio() > 0.6)
    # Remember a "yes" for a while (saves time); re-check a "no" quickly
    _prompt_cache.update(key=cache_key, ok=ok, until=now + (1.5 if ok else 0.25))
    return ok


def fishing_row(bgr):
    """Tiles of the fishing prompt, or [] if the prompt isn't on screen."""
    tiles = find_tiles(bgr)
    if tiles and cfg["require_prompt"]:
        if not in_dark_panel(bgr, tiles) or not prompt_visible(bgr, tiles):
            return []
    return tiles


def find_tiles(bgr):
    """Find the yellow key tiles that still need pressing, left to right.
    Finished tiles turn green/dark in the game, so they are skipped automatically."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    yellow = cv2.inRange(hsv, (12, 90, 140), (40, 255, 255))
    yellow = cv2.morphologyEx(yellow, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    n, _, stats, _ = cv2.connectedComponentsWithStats(yellow)
    cands = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w < cfg["min_tile_size"] or h < cfg["min_tile_size"]:
            continue
        if not 0.7 < w / h < 1.45:          # tiles are roughly square
            continue
        if area / (w * h) < 0.45:           # solid tile, not a thin outline or text
            continue
        pale = pale_square(hsv[y:y + h, x:x + w])
        if pale[h // 4:3 * h // 4, w // 4:3 * w // 4].mean() < 0.3:  # needs the letter square
            continue
        cands.append((x, y, w, h))

    if not cands:
        return []
    # Keep the biggest group of similar-sized tiles sitting on one row
    best = []
    for (x, y, w, h) in cands:
        cy = y + h / 2
        group = [c for c in cands
                 if abs((c[1] + c[3] / 2) - cy) < h * 0.5 and 0.7 < c[3] / h < 1.4]
        if len(group) > len(best) or (len(group) == len(best) and h > best[0][3]):
            best = group
    return sorted(best, key=lambda c: c[0])


def letter_image(bgr, tile):
    """Crop the dark letter on its pale square and make it black-on-white."""
    x, y, w, h = tile
    crop = bgr[y:y + h, x:x + w]
    pale = pale_square(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV))
    pale = cv2.morphologyEx(pale, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(pale)
    if n > 1:
        i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        lx, ly, lw, lh = stats[i][:4]
        if lw > w * 0.2 and lh > h * 0.2:
            crop = crop[ly:ly + lh, lx:lx + lw]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (int(64 * gray.shape[1] / max(1, gray.shape[0])), 64),
                      interpolation=cv2.INTER_CUBIC)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if np.mean(bw) < 127:
        bw = cv2.bitwise_not(bw)
    # Clean the edges of the square so its border isn't read as a letter
    m = 6
    bw[:m, :] = 255; bw[-m:, :] = 255; bw[:, :m] = 255; bw[:, -m:] = 255
    # Crop tightly around the letter and make it big - Tesseract likes ~50px letters
    ys, xs = np.where(bw == 0)
    if len(xs):
        bw = bw[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        scale = 50 / bw.shape[0]
        bw = cv2.resize(bw, (max(1, int(bw.shape[1] * scale)), 50),
                        interpolation=cv2.INTER_NEAREST)
    pad_x = (60 - bw.shape[1]) // 2 if bw.shape[1] < 60 else 0
    return cv2.copyMakeBorder(bw, 20, 20, 20 + pad_x, 20 + pad_x,
                              cv2.BORDER_CONSTANT, value=255)


def ocr(img, psm):
    allowed = cfg["allowed_keys"].upper()
    text = pytesseract.image_to_string(
        img, config=f"--psm {psm} -c tessedit_char_whitelist={allowed}")
    return [c for c in text.upper() if c in allowed]


def shape_letter(img):
    """Tell E, F, Z, Q and C apart by their shape (faster and more reliable than OCR
    for this game's font). Returns None if it doesn't look like any of them."""
    ink = (img == 0).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink)
    if n < 2:
        return None
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = stats[biggest][:4]
    if h < 15:
        return None
    # Q has a closed loop (a hole in the letter); E, F, Z and C don't
    glyph = ((labels == biggest) * 255).astype(np.uint8)[y:y + h, x:x + w]
    contours, hier = cv2.findContours(glyph, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    holes = sum(1 for i, c in enumerate(contours)
                if hier[0][i][3] != -1 and cv2.contourArea(c) > 0.05 * w * h)
    if holes:
        return "Q"
    g = cv2.resize(glyph, (30, 30), interpolation=cv2.INTER_AREA) > 127
    left_side = g[:, :8].any(axis=1).mean()    # E, F and C have a full left side
    if left_side < 0.8:
        return "Z"
    middle_bar = g[12:18, 12:].mean()           # E and F have a middle bar, C doesn't
    if middle_bar < 0.08:
        return "C"
    bottom_right = g[-7:, 15:].mean()           # E has a bottom bar, F doesn't
    return "E" if bottom_right > 0.12 else "F"


def read_keys(bgr, tiles):
    """Return (keys, strip image). Reads all tiles in one go, falls back to one by one."""
    if not tiles:
        return [], None
    letters = [letter_image(bgr, t) for t in tiles]
    height = max(img.shape[0] for img in letters)
    letters = [cv2.copyMakeBorder(img, 0, height - img.shape[0], 0, 0,
                                  cv2.BORDER_CONSTANT, value=255) for img in letters]
    gap = np.full((height, 30), 255, np.uint8)
    parts = []
    for img in letters:
        parts += [img, gap]
    strip = np.hstack(parts[:-1])

    allowed = cfg["allowed_keys"].upper()
    if set(allowed) <= set("EFZQC"):
        keys = [shape_letter(img) for img in letters]
        if all(k and k in allowed for k in keys):
            return [k.lower() for k in keys], strip

    keys = ocr(strip, 7)
    if len(keys) != len(tiles):
        keys = []
        for img in letters:
            k = ocr(img, 10)
            if not k:
                return [], strip  # unsure - don't guess
            keys.append(k[0])
    return [k.lower() for k in keys], strip


def arrow(keys):
    return " \u2192 ".join(k.upper() for k in keys)


def press(k):
    pydirectinput.keyDown(k)
    time.sleep(cfg["key_hold"])
    pydirectinput.keyUp(k)


def save_debug(img, tiles, keys, strip):
    cv2.imwrite("debug_raw.png", img)
    marked = img.copy()
    for i, (x, y, w, h) in enumerate(tiles):
        cv2.rectangle(marked, (x, y), (x + w, y + h), (0, 255, 0), 2)
        if i < len(keys):
            cv2.putText(marked, keys[i].upper(), (x, y - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
    cv2.imwrite("debug_detect.png", marked)
    if strip is not None:
        cv2.imwrite("debug_letters.png", strip)
    notify(f"Debug pictures saved. Tiles found: {len(tiles)}, read: "
           f"{''.join(keys).upper() or '(nothing)'}")


def scanner():
    """Background loop: grab the screen, find the tiles, read them, press them."""
    last_pressed, last_press_time, previous, first_seen = [], 0.0, None, 0.0

    with mss.mss() as sct:
        while not state["quit"]:
            if state["enabled"] and state["mode"] == "traits":
                run_traits(sct)
                continue
            if state["enabled"] and state["mode"] == "clicker":
                run_clicker()
                continue
            try:
                t0 = time.perf_counter()
                img = np.array(sct.grab(get_search_area()))[:, :, :3]
                tiles = fishing_row(img)
                if tiles:
                    state["fish_last_prompt"] = time.time()
                keys, strip = read_keys(img, tiles)
                state["scan_ms"] = int((time.perf_counter() - t0) * 1000)
                state["reading"] = arrow(keys) if keys else (
                    f"{len(tiles)} tiles, can't read" if tiles else "")
                state["error"] = ""

                if state["snapshot"]:
                    state["snapshot"] = False
                    save_debug(img, tiles, keys, strip)

                if not state["enabled"] or state["mode"] != "fishing":
                    previous = None
                    time.sleep(0.15)
                    continue

                if not keys:
                    last_pressed, previous = [], None
                    time.sleep(cfg["scan_interval"])
                    continue

                # Only act once the row has looked the same for a moment
                # (avoids pressing while a new row is still animating in)
                if keys != previous:
                    previous, first_seen = keys, time.time()
                stable = time.time() - first_seen >= cfg["settle_time"]
                # Don't retype what we just typed while the game catches up
                just_done = (last_pressed and keys == last_pressed[-len(keys):]
                             and time.time() - last_press_time < cfg["retry_after"])

                if stable and not just_done:
                    print("Pressing:", arrow(keys))
                    for k in keys:
                        if not state["enabled"]:
                            break
                        press(k)
                        state["inputs"] += 1
                        time.sleep(cfg["press_delay"])
                    state["pressed"] = arrow(keys)
                    last_pressed, last_press_time = keys, time.time()
                    previous = None
                    time.sleep(cfg["after_round_delay"])
                else:
                    time.sleep(cfg["scan_interval"])
            except pytesseract.TesseractNotFoundError:
                state["error"] = "Tesseract not found - check tesseract_path in config.json"
                time.sleep(1)
            except Exception as e:
                state["error"] = f"Error: {e}"
                print("Error:", e)
                time.sleep(1)


# ------------------------------------------------------------------ traits ---

def traits_active():
    return state["enabled"] and state["mode"] == "traits" and not state["quit"]


def grab_screen(sct):
    return np.array(sct.grab(screen()))[:, :, :3]


def squash(text):
    return re.sub("[^A-Z0-9%]", "", text.upper())


def fuzzy_contains(text, word, ratio=0.8):
    """True if `word` appears in `text`, allowing a few OCR mistakes."""
    word = squash(word)
    if not word or not text:
        return False
    if word in text:
        return True
    n = len(word)
    for i in range(0, max(1, len(text) - n + 1)):
        if difflib.SequenceMatcher(None, text[i:i + n], word).ratio() >= ratio:
            return True
    return False


def yellow_buttons(bgr):
    """Solid yellow menu buttons (SELECT, credit cost, TRAIT GUIDE) with their text."""
    H, W = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (18, 150, 180), (35, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    buttons = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w > 0.08 * W and h > 0.03 * H and 2.5 < w / h < 9 and area / (w * h) > 0.6:
            gray = cv2.cvtColor(bgr[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)
            _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            bw = cv2.copyMakeBorder(bw, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
            text = squash(pytesseract.image_to_string(bw, config="--psm 7"))
            buttons.append({"x": x, "y": y, "w": w, "h": h, "text": text,
                            "cx": x + w // 2, "cy": y + h // 2})
    return buttons


def find_menu(bgr, side=None):
    """('select', SELECT button of the chosen droid) or ('panel', credit button) or None."""
    buttons = yellow_buttons(bgr)
    guide = [b for b in buttons if fuzzy_contains(b["text"], "GUIDE", 0.75)]
    if guide:
        g = guide[0]
        above = [b for b in buttons if b["y"] < g["y"] and abs(b["cx"] - g["cx"]) < g["w"] * 0.3]
        if above:
            return "panel", max(above, key=lambda b: b["y"])
    selects = [b for b in buttons if fuzzy_contains(b["text"], "SELECT", 0.75)]
    if selects:
        side = side or state.get("trait_side", "left")
        # The two SELECT buttons sit either side of the screen's middle. While the menu
        # pops in/out only one may be visible - so go by which half it's in, never by
        # "the rightmost one we can see" (that picked the LEFT droid by mistake).
        mid = bgr.shape[1] / 2
        mine = [b for b in selects if (b["cx"] > mid) == (side == "right")]
        if not mine:
            return None                           # our droid's button isn't up yet
        return "select", (max if side == "right" else min)(mine, key=lambda b: b["x"])
    return None


def pill_rarity(reg):
    """Rarity from the colour of the rarity pill on a droid card.
    Iconic = pale cream-yellow, Legendary = vivid orange, Mythic = pink, Epic = violet,
    Rare = blue, Common = grey."""
    hsv = cv2.cvtColor(reg, cv2.COLOR_BGR2HSV)
    for kind, mask in (("colour", (hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 150)),
                       ("grey", (hsv[:, :, 1] < 40) & (hsv[:, :, 2] > 170))):
        n, lab, st, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8))
        best = None
        for i in range(1, n):
            x, y, w, h, a = st[i]
            if w < 20 or h < 6 or not (1.8 < w / h < 8) or a / (w * h) < 0.5:
                continue
            if y < reg.shape[0] * 0.35:                # the pill is on the lower line
                continue
            if best is None or a > best[1]:
                best = (i, a)
        if not best:
            continue
        if kind == "grey":
            return "COMMON"
        px = hsv[lab == best[0]]
        hue, sat = int(np.median(px[:, 0])), int(np.median(px[:, 1]))
        if 8 <= hue <= 34:
            return "LEGENDARY" if sat >= 185 else "ICONIC"
        if 140 <= hue <= 178:
            return "MYTHIC"
        if 118 <= hue < 140:
            return "EPIC"
        if 88 <= hue < 118:
            return "RARE"
    return None


def droid_card_check(bgr, button):
    """Is the droid above this SELECT button Iconic? -> ('iconic'|'ok'|'unsure', name)."""
    x, y, w, h = button["x"], button["y"], button["w"], button["h"]
    reg = bgr[max(0, y - int(1.45 * h)):max(0, y - int(0.05 * h)), x:x + w]
    if reg.size == 0:
        return "unsure", ""
    up = cv2.resize(reg, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    hsv, g = cv2.cvtColor(up, cv2.COLOR_BGR2HSV), cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
    toks = []
    for m in ((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 170), g < 70, hsv[:, :, 2] > 150):
        toks += _ocr(255 - m.astype(np.uint8) * 255, 6).split()
    words = [_sq(t) for t in toks if len(_sq(t)) >= 3]
    name, sc = _match_name(" ".join(toks))
    shown = name if name and sc >= 0.7 else ""
    rarity = pill_rarity(reg)
    # Iconic if the pill colour, the word ICONIC or the droid's name says so
    if (rarity == "ICONIC" or any("ICONIC" in t or _ratio(t, "ICONIC") >= 0.75 for t in words)
            or (name and sc >= 0.8 and DROID_RARITY.get(name) == "Iconic")):
        return "iconic", shown or "Iconic"
    if rarity:
        pool = [n for n, r in DROID_RARITY.items() if r.upper() == rarity]
        n2, s2 = _match_name(" ".join(toks), pool)
        return "ok", (n2 if n2 and s2 >= 0.8 else "")
    other = [r for r in RARITY_WORDS if r != "ICONIC"]
    if any(r in t or _ratio(t, r) >= 0.8 for t in words for r in other):
        return "ok", shown
    if name and sc >= 0.85:
        return "ok", shown
    return "unsure", shown


def white_text(bgr, box, any_bright=False):
    """Read white (or any bright, for coloured names) text inside a box given as
    fractions of the screen."""
    H, W = bgr.shape[:2]
    x0, y0, x1, y1 = int(box[0] * W), int(box[1] * H), int(box[2] * W), int(box[3] * H)
    hsv = cv2.cvtColor(bgr[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    if any_bright:
        mask = (hsv[:, :, 2] > 150).astype(np.uint8) * 255
    else:
        mask = ((hsv[:, :, 1] < 90) & (hsv[:, :, 2] > 170)).astype(np.uint8) * 255
    img = cv2.resize(255 - mask, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
    img = cv2.copyMakeBorder(img, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    return squash(pytesseract.image_to_string(img, config="--psm 6"))


def colour_count(part):
    """How many different colours a piece of the screen has (rainbow = 3 or more)."""
    hsv = cv2.cvtColor(part, cv2.COLOR_BGR2HSV)
    m = (hsv[:, :, 1] > 110) & (hsv[:, :, 2] > 120)
    if m.mean() < 0.05:
        return 0
    hist, _ = np.histogram(hsv[:, :, 0][m], bins=6, range=(0, 180))
    return int((hist / hist.sum() > 0.06).sum())


def rainbow_trait(bgr):
    """True if the trait in the panel is rainbow tier (rainbow icon or rainbow name)."""
    H, W = bgr.shape[:2]
    icon = bgr[int(0.65 * H):int(0.84 * H), int(0.29 * W):int(0.42 * W)]
    name = bgr[int(0.57 * H):int(0.615 * H), int(0.26 * W):int(0.45 * W)]
    return colour_count(icon) >= 3 or colour_count(name) >= 3


def identify_trait(texts):
    """Name of the trait the text belongs to, or '' if it can't be told.
    ("Upgrade Chips" alone could be Chip Finder or Upgrade Chip Bonus - no guessing.)"""
    for name, words in TRAIT_WORDS.items():
        if any(fuzzy_contains(t, w) for t in texts for w in words):
            return name
    return ""


def is_wanted(name, tier):
    """tier: 'default' / 'gold' / 'diamond' / 'rainbow' ('' if unknown)."""
    choice = cfg["trait_wanted"].get(name, "off")
    return (choice == "any" or (choice == "rainbow" and tier == "rainbow")
            or (choice == "diamond" and tier in ("diamond", "rainbow")))


def could_be_wanted(tier):
    """For a trait we couldn't read: might it be one we want?"""
    for name, choice in cfg["trait_wanted"].items():
        if choice in ("rainbow", "diamond") and tier == "rainbow":
            return True
        if choice == "diamond" and tier == "diamond" and name not in ALWAYS_RAINBOW:
            return True
        if choice == "any" and (tier == "rainbow" or name not in ALWAYS_RAINBOW):
            return True
    return False


def wanted_summary():
    parts = [f"{n} ({'rainbow' if n in ALWAYS_RAINBOW else c if c in ('rainbow', 'diamond') else 'any'})"
             for n, c in cfg["trait_wanted"].items() if c != "off"]
    return ", ".join(parts) or "nothing"


def click_at(x, y):
    m = screen()
    x, y = int(m["left"] + x), int(m["top"] + y)
    if m is PRIMARY or sys.platform != "win32":
        pydirectinput.moveTo(x, y)
        time.sleep(0.06)
        pydirectinput.moveTo(x + 2, y)       # small wiggle so the game notices the cursor
        time.sleep(0.06)
        pydirectinput.moveTo(x, y)
    else:                                     # other screens: move the cursor directly
        u = ctypes.windll.user32
        u.SetCursorPos(x, y)
        time.sleep(0.06)
        u.mouse_event(0x0001, 2, 0, 0, 0)     # small wiggle so the game notices
        time.sleep(0.06)
        u.mouse_event(0x0001, -2, 0, 0, 0)
    time.sleep(0.06)
    pydirectinput.mouseDown()
    time.sleep(0.06)
    pydirectinput.mouseUp()


def traits_wait(seconds):
    end = time.time() + seconds
    while time.time() < end and traits_active():
        time.sleep(0.1)
    return traits_active()


def wait_for_menu(sct, want, timeout, side=None):
    """Wait until the given menu ('select' / 'panel' / either) shows up."""
    end = time.time() + timeout
    while time.time() < end and traits_active():
        found = find_menu(grab_screen(sct), side)
        if found and (want == "either" or found[0] == want):
            return found
        time.sleep(0.15)
    return None


def stop_traits(msg):
    state["enabled"] = False
    state["status"] = msg
    notify(msg, 30)


def next_droid(msg):
    """This droid is done (wanted trait / cost limit). Move to the next droid if 'Both'
    was picked - returns True - otherwise stop and return False."""
    if not state["trait_queue"]:
        stop_traits(msg)
        return False
    pydirectinput.press("esc")                    # close this droid's trait panel
    time.sleep(1.0)
    state["trait_side"] = state["trait_queue"].pop(0)
    state["side_checked"] = None
    state["trait_droid"] = ""
    state["last_cost"] = None
    notify(f"{msg}  Moving on to the {state['trait_side']} droid.", 10)
    return True


def trait_tier(bgr):
    """'default' (teal), 'gold', 'diamond' or 'rainbow' from the panel's trait icon."""
    H, W = bgr.shape[:2]
    icon = bgr[int(0.65 * H):int(0.84 * H), int(0.29 * W):int(0.42 * W)]
    if rainbow_trait(bgr):
        return "rainbow"
    hsv = cv2.cvtColor(icon, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    vivid = (hsv[:, 1] > 110) & (hsv[:, 2] > 120)
    whiteish = ((hsv[:, 1] < 70) & (hsv[:, 2] > 190)).mean()
    if vivid.mean() < 0.03 and whiteish < 0.03:
        return ""                                  # no icon (e.g. "Discover trait")
    hue = float(np.median(hsv[vivid, 0])) if vivid.any() else -1
    if 10 <= hue <= 40:
        return "gold"
    if 78 <= hue <= 92:
        return "default"
    return "diamond"                               # light blue / white (best guess)


def parse_amount(text):
    """'67.20B' -> 67.2e9. Any number of decimals ('1.4T', '420.6B', '12B').
    Handles the game font's B being read as 8 (e.g. '33.608' -> 33.60B)."""
    t = re.sub(r"[^0-9A-Za-z.,]", "", text).upper().replace(",", ".")
    t = re.sub(r"[O0]A$", "QA", t).replace("0I", "QI")
    m = re.match(r"^(\d+(?:\.\d+)?)([A-Z]*)$", t)
    if not m:
        return None
    num, suf = m.group(1), m.group(2)
    if not suf and "." in num and len(num.split(".")[1]) == 3 and num[-1] in "6817":
        # 2 decimals + a letter the font makes look like a digit: B->8/6, T->7/1
        num, suf = num[:-1], {"6": "B", "8": "B", "7": "T", "1": "T"}[num[-1]]
    if suf not in SUFFIX:
        return None
    return float(num) * SUFFIX[suf]


def format_amount(value):
    for suf, mult in sorted(SUFFIX.items(), key=lambda kv: -kv[1]):
        if value >= mult and mult > 1:
            return f"{value / mult:.2f}{suf.title() if len(suf) > 1 else suf}"
    return f"{value:.0f}"


def fix_cost_decimals(text):
    """The roll button always shows 2 decimals ("1.35T"). OCR sometimes drops the small
    dot ("135T"), which would read 100x too high - put it back."""
    t = re.sub(r"[^0-9A-Za-z.,]", "", text).replace(",", ".")
    m = re.match(r"^(\d{3,})([A-Za-z]+)$", t)
    if m:
        return f"{m.group(1)[:-2]}.{m.group(1)[-2:]}{m.group(2)}"
    return t


def _cost_value(text):
    """A roll cost always has a K/M/B/T/Qa... ending - anything without one is a
    half-drawn or misread button and must not be trusted."""
    v = parse_amount(fix_cost_decimals(text))
    t = re.sub(r"[^0-9A-Za-z.]", "", text)
    if v is None or not re.search(r"[A-Za-z]$", t) and not re.search(r"\.\d{3}$", t):
        return None
    return v


def read_cost(bgr, button):
    """Read the credit amount on the roll button. The amount is centred, so the coin
    moves with the number's length - find the coin and read what's right of it."""
    x, y, w, h = button["x"], button["y"], button["w"], button["h"]
    g = cv2.cvtColor(bgr[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)
    _, dark = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    n, _, st, _ = cv2.connectedComponentsWithStats(dark)
    comps = [tuple(st[i]) for i in range(1, n) if st[i][4] > 4]
    coins = [c for c in comps if c[3] > 0.7 * h and c[2] > 0.5 * h]   # the round coin
    coin_right = max((c[0] + c[2] for c in coins), default=0)
    chars = [c for c in comps if c[0] >= coin_right - 1 and 0.3 * h < c[3] < 0.8 * h
             and c[1] > 0 and c[1] + c[3] < h]
    if not chars:
        return None
    x0 = max(0, min(c[0] for c in chars) - 4)
    x1 = min(w, max(c[0] + c[2] for c in chars) + 4)
    crop = g[:, x0:x1]
    crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    _, bw = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if bw.mean() < 127:
        bw = 255 - bw
    bw = cv2.copyMakeBorder(bw, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    text = pytesseract.image_to_string(bw, config="--psm 8").strip()
    value = _cost_value(text)
    if value is None:
        text2 = pytesseract.image_to_string(
            bw, config="--psm 7 -c tessedit_char_whitelist=0123456789.KMBTQaiSxp").strip()
        value = _cost_value(text2)
        if value is None:
            print("Couldn't parse roll cost:", repr(text), repr(text2))
    return value


# ----------------------------------------------------------------- account ---
# Invite-only: people sign in with the LMS Droid Tycoon login you made for them.

AUTH_FILE = "account.json"
_account = {"uid": None, "username": "", "id_token": None, "expires": 0.0,
            "refresh": None, "offline": False}


class AuthError(Exception):
    """Firebase said no (wrong password, account turned off, removed...)."""


def accounts_enabled():
    return not (FIREBASE_API_KEY.startswith("PASTE") or FIREBASE_PROJECT.startswith("PASTE"))


def _http(url, data=None, form=False, token=None, method=None):
    import urllib.request, urllib.parse, urllib.error
    headers = {"User-Agent": "SW-Key-Helper"}
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error", {}).get("message", "") or str(e)
        except Exception:
            msg = str(e)
        raise AuthError(msg)


def friendly_auth_error(msg):
    m = str(msg).upper()
    if "DISABLED" in m:
        return "This account has been turned off. Ask the admin."
    if "TOO_MANY" in m:
        return "Too many tries. Wait a minute and try again."
    if any(k in m for k in ("INVALID", "NOT_FOUND", "PASSWORD", "MISSING")):
        return "That username and password don't match."
    return f"Couldn't sign in ({msg})."


def save_account():
    try:
        with open(AUTH_FILE, "w") as f:
            json.dump({"username": _account["username"], "uid": _account["uid"],
                       "refresh": _account["refresh"], "last_ok": time.time()}, f)
    except Exception as e:
        print("Couldn't save login:", e)


def sign_in(username, password):
    username = username.strip()
    email = username if "@" in username else f"{username.lower()}@{LOGIN_DOMAIN}"
    r = _http("https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
              f"?key={FIREBASE_API_KEY}",
              {"email": email, "password": password, "returnSecureToken": True})
    _account.update(uid=r["localId"], username=username.split("@")[0],
                    id_token=r["idToken"], refresh=r["refreshToken"],
                    expires=time.time() + int(r.get("expiresIn", 3600)) - 120,
                    offline=False)
    save_account()


def refresh_login():
    r = _http(f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_API_KEY}",
              {"grant_type": "refresh_token", "refresh_token": _account["refresh"]}, form=True)
    _account.update(uid=r["user_id"], id_token=r["id_token"], refresh=r["refresh_token"],
                    expires=time.time() + int(r.get("expires_in", 3600)) - 120,
                    offline=False)
    save_account()


def id_token():
    """A fresh Firebase token for database calls (refreshed when it runs out)."""
    if not _account["id_token"] or time.time() > _account["expires"]:
        refresh_login()
    return _account["id_token"]


def check_saved_login():
    """'ok', 'offline' (no internet but logged in recently) or 'login' (ask)."""
    try:
        with open(AUTH_FILE) as f:
            saved = json.load(f)
    except Exception:
        return "login"
    _account.update(username=saved.get("username", ""), uid=saved.get("uid"),
                    refresh=saved.get("refresh"))
    if not _account["refresh"]:
        return "login"
    try:
        refresh_login()
        return "ok"
    except AuthError as e:                       # turned off / removed / password changed
        print("Saved login no longer works:", e)
        sign_out(quiet=True)
        return "login"
    except OSError:                              # no internet
        if time.time() - saved.get("last_ok", 0) < OFFLINE_GRACE_DAYS * 86400:
            _account["offline"] = True
            return "offline"
        return "login"


def sign_out(quiet=False):
    try:
        os.remove(AUTH_FILE)
    except OSError:
        pass
    _account.update(uid=None, id_token=None, refresh=None)
    if not quiet:
        notify("Signed out - Droid Tycoon Helper will close", 5)


def login_window(message=""):
    """Small sign-in window. Returns True once signed in, False if closed."""
    win = tk.Tk()
    win.title("Droid Tycoon Helper - Sign in")
    win.configure(bg=BG)
    win.resizable(False, False)
    win.attributes("-topmost", True)
    ico = icon_path()
    if ico:
        try:
            win.iconbitmap(ico)
        except Exception:
            pass
    box = tk.Frame(win, bg=BG, padx=28, pady=22)
    box.pack()
    tk.Label(box, text="DROID TYCOON HELPER", bg=BG, fg=FG,
             font=("Segoe UI", 15, "bold")).pack(anchor="w")
    tk.Label(box, text="Sign in with your LMS Droid Tycoon login", bg=BG, fg=DIM,
             font=("Segoe UI", 10)).pack(anchor="w", pady=(0, 14))

    def field(label, show=""):
        tk.Label(box, text=label, bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(anchor="w")
        e = tk.Entry(box, width=32, font=("Segoe UI", 11), bg="#232833", fg=FG,
                     insertbackground=FG, relief="flat", show=show)
        e.pack(fill="x", pady=(2, 10), ipady=5)
        return e

    user = field("Username")
    pw = field("Password", show="•")
    user.insert(0, _account.get("username") or "")
    status = tk.Label(box, text=message, bg=BG, fg=RED if message else DIM,
                      font=("Segoe UI", 9), wraplength=300, justify="left")
    status.pack(anchor="w", pady=(0, 8))
    result = {"ok": False}

    def submit(_=None):
        if not user.get().strip() or not pw.get():
            status.config(text="Enter your username and password.", fg=RED)
            return
        status.config(text="Signing in…", fg=DIM)
        btn.config(state="disabled")
        win.update()
        try:
            sign_in(user.get(), pw.get())
            result["ok"] = True
            win.destroy()
            return
        except AuthError as e:
            status.config(text=friendly_auth_error(e), fg=RED)
        except OSError:
            status.config(text="Couldn't reach the server. Check your internet.", fg=RED)
        btn.config(state="normal")

    btn = tk.Button(box, text="Sign in", command=submit, bg=GREEN, fg="#0b0d10",
                    activebackground=GREEN, relief="flat", font=("Segoe UI", 11, "bold"),
                    cursor="hand2")
    btn.pack(fill="x", ipady=4)
    tk.Label(box, text="No login? Ask the admin to make you one.", bg=BG, fg=DIM,
             font=("Segoe UI", 9)).pack(anchor="w", pady=(12, 0))
    win.bind("<Return>", submit)

    def watch():                                  # close if an update restarts the app
        if state["quit"]:
            win.destroy()
        else:
            win.after(300, watch)

    watch()
    win.update_idletasks()
    w, h = win.winfo_width(), win.winfo_height()
    win.geometry(f"+{(win.winfo_screenwidth() - w) // 2}+{(win.winfo_screenheight() - h) // 3}")
    (pw if user.get() else user).focus_force()
    win.mainloop()
    return result["ok"]


def require_login():
    """Make sure someone is signed in before the app starts. False = quit."""
    if not accounts_enabled():
        return True
    status = check_saved_login()
    if status == "ok":
        return True
    if status == "offline":
        notify(f"Offline - using your saved login ({_account['username']})", 6)
        return True
    msg = "" if not os.path.exists(AUTH_FILE) else "Please sign in again."
    return login_window(msg)


# ------------------------------------------------------- shared trait odds ---
# Every roll adds +1 to a shared counter in Firebase (just trait + tier, nothing
# personal), so everyone's odds are based on all players' rolls.

_shared = {"counts": {}, "total": 0, "fetched": 0.0}
_KEY_TO_ROLL = {}


def roll_key(trait, tier):
    key = re.sub(r"[^A-Za-z0-9]+", "_", f"{tier or 'none'}__{trait or 'unknown'}").strip("_")
    _KEY_TO_ROLL[key] = (trait, tier)
    return key


for _t in TIERS + [""]:
    for _n in TRAIT_NAMES + ["?"]:
        roll_key(_n, _t)


def _firestore_base():
    return (f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT}"
            f"/databases/(default)/documents")


def share_roll(trait, tier):
    if not accounts_enabled() or not _account["refresh"]:
        return

    def send():
        try:
            base = f"projects/{FIREBASE_PROJECT}/databases/(default)/documents/traitStats"
            inc = [{"fieldPath": "n", "increment": {"integerValue": "1"}}]
            _http(_firestore_base() + ":commit", {"writes": [
                {"transform": {"document": f"{base}/{roll_key(trait, tier)}",
                               "fieldTransforms": inc}},
                {"transform": {"document": f"{base}/_total", "fieldTransforms": inc}},
            ]}, token=id_token())
        except Exception as e:
            print("Couldn't share roll:", e)

    threading.Thread(target=send, daemon=True).start()


def fetch_shared():
    if not accounts_enabled() or not _account["refresh"]:
        return

    def get():
        try:
            counts, page = {}, ""
            while True:
                url = _firestore_base() + "/traitStats?pageSize=300" + (
                    f"&pageToken={page}" if page else "")
                r = _http(url, token=id_token())
                for d in r.get("documents", []):
                    key = d["name"].rsplit("/", 1)[-1]
                    counts[key] = int(d.get("fields", {}).get("n", {}).get("integerValue", 0))
                page = r.get("nextPageToken", "")
                if not page:
                    break
            _shared.update(counts=counts, total=counts.pop("_total", 0), fetched=time.time())
        except Exception as e:
            print("Couldn't load shared odds:", e)

    threading.Thread(target=get, daemon=True).start()


def load_stats():
    try:
        with open(STATS_FILE) as f:
            return json.load(f)
    except Exception:
        return {"rolls": []}


_stats = None


def record_roll(trait, tier, cost):
    global _stats
    if _stats is None:
        _stats = load_stats()
    _stats["rolls"].append({"trait": trait, "tier": tier, "cost": cost,
                            "t": round(time.time(), 1)})
    _stats["rolls"] = _stats["rolls"][-5000:]
    share_roll(trait, tier)
    if state["rolls"] % 25 == 0:
        fetch_shared()
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(_stats, f)
    except Exception as e:
        print("Couldn't save trait stats:", e)


def odds_data():
    """Roll results for the app window: everyone's shared counts if there are more
    of those, otherwise this PC's. {'who', 'n', 'counts': {trait: {tier: n}}}"""
    global _stats
    if _stats is None:
        _stats = load_stats()
    mine, everyone = {}, {}
    for r in _stats["rolls"]:
        if r.get("tier"):
            t = mine.setdefault(r["trait"], {})
            t[r["tier"]] = t.get(r["tier"], 0) + 1
    for key, c in _shared["counts"].items():
        if key in _KEY_TO_ROLL and _KEY_TO_ROLL[key][1]:
            trait, tier = _KEY_TO_ROLL[key]
            t = everyone.setdefault(trait, {})
            t[tier] = t.get(tier, 0) + c
    n_m = sum(sum(v.values()) for v in mine.values())
    n_e = sum(sum(v.values()) for v in everyone.values())
    who, counts, n = ("everyone", everyone, n_e) if n_e >= n_m else ("you", mine, n_m)
    times = state["roll_times"]
    gaps = sorted(b - a for a, b in zip(times, times[1:]) if 0 < b - a < 60)
    return {"who": who, "n": n, "counts": counts,
            "per_roll": gaps[len(gaps) // 2] if gaps else None}


def stats_summary():
    """Two overlay lines: tier percentages, and the chance of a wanted trait."""
    global _stats
    if _stats is None:
        _stats = load_stats()
    rolls = [r for r in _stats["rolls"] if r.get("tier")]
    # tally (trait, tier) -> count, for this PC and for everyone (shared)
    mine = {}
    for r in rolls:
        mine[(r["trait"], r["tier"])] = mine.get((r["trait"], r["tier"]), 0) + 1
    everyone = {}
    for key, c in _shared["counts"].items():
        if key in _KEY_TO_ROLL and _KEY_TO_ROLL[key][1]:
            everyone[_KEY_TO_ROLL[key]] = c

    def tier_line(label, tally):
        n = sum(tally.values())
        pct = {t: 100 * sum(c for (_, tt), c in tally.items() if tt == t) / n for t in TIERS}
        return (f"{label}Default {pct['default']:.0f}% \u00b7 Gold {pct['gold']:.0f}% \u00b7 "
                f"Diamond {pct['diamond']:.0f}% \u00b7 Rainbow {pct['rainbow']:.1f}%  "
                f"({n:,} rolls)")

    if not mine and not everyone:
        return "Odds: collecting data (no rolls recorded yet)"
    lines = []
    if mine:
        lines.append(tier_line("You: " if everyone else "", mine))
    if everyone:
        lines.append(tier_line("Everyone: ", everyone))
    # use whichever has more data for the "wanted" estimate
    tally = everyone if sum(everyone.values()) >= sum(mine.values()) else mine
    n = sum(tally.values())
    hits = sum(c for (trait, tier), c in tally.items() if is_wanted(trait, tier))
    times = state["roll_times"]
    gaps = sorted(b - a for a, b in zip(times, times[1:]) if 0 < b - a < 60)
    per_roll = gaps[len(gaps) // 2] if gaps else None
    if n < 20:
        line2 = f"Wanted odds: need 20+ rolls of data ({n} so far)"
    elif hits == 0:
        line2 = f"Wanted odds: under 1 in {n} so far"
    else:
        p = hits / n
        exp = 1 / p
        line2 = f"Wanted: {100 * p:.1f}% per roll \u2248 1 in {exp:.0f}"
        if per_roll:
            line2 += f" \u00b7 ~{exp * per_roll / 60:.0f} min on average"
    return "\n".join(lines + [line2])


def clear_hands(sct):
    """Quick left click (only with no menu open) - a blueprint that dropped while
    rolling ends up in your hands and blocks the scanner; the click puts it away."""
    if not traits_active():
        return
    img = grab_screen(sct)
    if find_menu(img, "left") or find_menu(img, "right"):
        return                                    # a menu is open - never click into it
    pydirectinput.mouseDown()
    time.sleep(0.05)
    pydirectinput.mouseUp()
    time.sleep(0.25)


def run_traits(sct):
    """Roll traits at the scanner. With "Both", the two droids take turns: right after
    one droid's roll starts, the other one is rolled while the first is still
    animating - nearly twice as many rolls per minute."""
    sides = list(state["trait_queue_all"])
    info = {sd: {"last_roll": 0.0, "pending": None, "checked": False, "droid": "",
                 "last_cost": None, "rolls": 0} for sd in sides}
    results = []
    turn = 0

    def finish(side, msg):
        """This droid is done. Returns True if another droid is still rolling."""
        results.append(msg)
        if side in sides:
            sides.remove(side)
        if not sides:
            stop_traits("  ".join(results))
            return False
        pydirectinput.press("esc")                # close this droid's panel
        time.sleep(0.6)
        notify(f"{msg}  Still rolling the {sides[0]} droid.", 10)
        return True

    try:
        while traits_active() and sides:
            side = sides[turn % len(sides)]
            turn += 1
            st = info[side]
            state["trait_side"], state["trait_droid"] = side, st["droid"]
            state["last_cost"] = st["last_cost"]
            # wait until this droid's previous roll has finished animating
            left_to_wait = cfg["roll_wait"] - (time.time() - st["last_roll"])
            if left_to_wait > 0:
                state["status"] = f"Waiting for the {side} droid's roll"
                if not traits_wait(left_to_wait):
                    return
            clear_hands(sct)

            # 1. Press F at the scanner (retry - a roll may still be animating)
            state["status"] = "Opening droid menu"
            found = None
            for _ in range(5):
                press(cfg["interact_key"])
                found = wait_for_menu(sct, "either", 2.5)
                if found or not traits_active():
                    break
                clear_hands(sct)
            if not traits_active():
                return
            if not found:
                stop_traits("Couldn't open the droid menu - stand at the trait scanner")
                return

            # 2. Pick the droid (left or right) - never an Iconic one.
            # While one droid is still rolling, F skips the Select Droid screen and opens
            # the other (free) droid's panel straight away - that's this droid.
            if found[0] != "select" and not st["checked"]:
                stop_traits("Couldn't see the Select Droid screen to check the droid's "
                            "rarity - stopped to be safe")
                return
            if found[0] == "select":
                state["status"] = f"Selecting {side} droid"
                if not traits_wait(0.3):          # let the menu finish its pop-in
                    return
                settled = wait_for_menu(sct, "select", 1.5, side) or found
                if settled[0] != "select":
                    settled = found
                if not all(info[sd]["checked"] for sd in sides):
                    # check every droid we'll roll now, while both cards are on screen
                    shot = grab_screen(sct)
                    for sd in sides:
                        if info[sd]["checked"]:
                            continue
                        btn = settled[1] if sd == side else (find_menu(shot, sd) or (None, None))[1]
                        if btn is None:
                            if sd == side:
                                stop_traits(f"Couldn't see the {sd} droid's card - stopped to be safe")
                                return
                            continue
                        verdict, dname = droid_card_check(shot, btn)
                        if verdict == "iconic":
                            stop_traits(f"The {sd} droid is Iconic ({dname or 'Iconic'}) - "
                                        "never rolling Iconics. Stopped.")
                            return
                        if verdict == "unsure":
                            stop_traits(f"Couldn't tell if the {sd} droid is Iconic - "
                                        "stopped to be safe")
                            return
                        info[sd]["checked"], info[sd]["droid"] = True, dname
                    state["trait_droid"] = st["droid"]
                click_at(settled[1]["cx"], settled[1]["cy"])
                found = wait_for_menu(sct, "panel", 3)
                if not found and traits_active():
                    sel = wait_for_menu(sct, "select", 0.5, side)
                    if sel:
                        click_at(sel[1]["cx"], sel[1]["cy"])
                    found = wait_for_menu(sct, "panel", 3)
                if not traits_active():
                    return
                if not found:
                    stop_traits("The trait panel didn't open after selecting the droid")
                    return

            # 3. Read the droid's current trait BEFORE spending anything
            state["status"] = f"Reading the {side} droid's trait"
            if not traits_wait(0.4):              # let the panel finish sliding in
                return
            texts, tier_name, name = [], "", ""
            for _ in range(5):                    # a few tries if the text is hard to read
                img = grab_screen(sct)
                box = (0.2, 0.56, 0.5, 0.70)
                texts += [white_text(img, box), white_text(img, box, any_bright=True)]
                t = trait_tier(img)
                if t == "rainbow" or not tier_name:
                    tier_name = t
                name = identify_trait(texts)
                if name or not traits_active():
                    break
                time.sleep(0.25)
            if st["pending"] is not None:          # the result of this droid's last roll
                record_roll(name or "?", tier_name, st["pending"])
                st["pending"] = None
            tier = f" ({tier_name})" if tier_name and tier_name != "default" else ""
            state["trait"] = (name + tier) if name else (
                (max(texts, key=len)[:30] + tier) if any(texts) else "(none yet)")
            if name and is_wanted(name, tier_name):
                if finish(side, f"GOT {name.upper()}{tier.upper()} on the {side} droid "
                                f"after {st['rolls']} rolls!"):
                    continue
                return
            if not name and not tier_name:      # grey "?" - this droid has no trait yet
                state["trait"] = "(no trait yet)"
            elif not name and cfg["stop_if_unreadable"] and could_be_wanted(tier_name):
                stop_traits(f"Couldn't read the {side} droid's trait and it might be one you "
                            "want - stopped to be safe. Check it!")
                return
            if cfg["max_rolls"] and st["rolls"] >= cfg["max_rolls"]:
                if finish(side, f"Reached max_rolls ({cfg['max_rolls']}) on the {side} droid."):
                    continue
                return

            # 4. Check the price, click the credit button and make sure the roll started
            panel = wait_for_menu(sct, "panel", 1.5)
            if not panel:
                stop_traits("Lost the trait panel before rolling")
                return
            cost = None
            for _ in range(5):
                cost = read_cost(grab_screen(sct), panel[1])
                if cost is not None:
                    break
                time.sleep(0.2)
            state["cost"] = format_amount(cost) if cost is not None else "?"
            limit = parse_amount(cfg["max_cost"] if "." in cfg["max_cost"] else
                                 re.sub(r"^(\d+)", r"\1.00", cfg["max_cost"]))
            if cfg["max_cost"] != "off" and limit:
                last = st["last_cost"]
                if cost is None and not (last and last * 3 < limit):
                    stop_traits("Couldn't read the roll cost - stopped because the cost "
                                "limit is on")
                    return
                if cost is not None and cost > limit:
                    # make sure it wasn't a misread: two more reads must agree
                    checks = [read_cost(grab_screen(sct), panel[1]) for _ in range(2)]
                    if not all(c is not None and abs(c - cost) <= 0.01 * cost for c in checks):
                        good = [c for c in checks if c is not None and c <= limit]
                        if good:
                            cost = good[0]
                            state["cost"] = format_amount(cost)
                if cost is not None and cost > limit:
                    if finish(side, f"Next roll on the {side} droid costs {format_amount(cost)} - "
                                    f"over your {cfg['max_cost']} limit."):
                        continue
                    return
            if cost is not None:
                st["last_cost"] = state["last_cost"] = cost
            state["status"] = f"Rolling the {side} droid (#{state['rolls'] + 1}) \u00b7 {state['cost']}"
            click_at(panel[1]["cx"], panel[1]["cy"])
            started = False
            end = time.time() + 3
            while time.time() < end and traits_active():
                time.sleep(0.15)
                img = grab_screen(sct)
                if not (find_menu(img, "left") or find_menu(img, "right")):
                    started = True
                    break
            if not traits_active():
                return
            if not started:
                if finish(side, f"Roll didn't start on the {side} droid - not enough credits "
                                f"({state['cost']})?"):
                    continue
                return
            st["last_roll"] = time.time()
            st["rolls"] += 1
            st["pending"] = cost
            state["rolls"] += 1
            state["inputs"] += 1
            state["pending_roll"] = cost
            state["spent"] += cost or 0
            state["roll_times"] = (state["roll_times"] + [time.time()])[-50:]
            # 5. straight on to the other droid (or, with one droid, wait for this roll)
    except pytesseract.TesseractNotFoundError:
        stop_traits("Tesseract not found - check tesseract_path in config.json")
    except Exception as e:
        stop_traits(f"Traits error: {e}")


# ----------------------------------------------------------- droid reader ---
# Reads a droid's name and variant from the in-game droid screen or from a
# blueprint card, so it can be added to / removed from the website inventory.

# Same list as the LMS Droid Tycoon website (name, type code, * = fusion only)
DROID_RAW = {
    "Common": "Gonk W,Mouse W,Pit W,R8 A,CB A,R3 A,R5 A,Imperial Probe B,B1 Battle B,ID10 B,DRK-1 Probe B",
    "Rare": "EG-58 W,WHL-EX W*,BU-4D W,Senate Hovercam W,ARG W,ROLL-R W,Bal-Core W,BDX Explorer W,ZRO-TEC A*,R9 A,R4 A,A-LT A,2BB A,BTL-R B*,B1 Security B,HOV-R B,VECT-Arm B,NAV-EX B,SA-5 P",
    "Epic": "EGL W,KT W,MPH A,N-UL W*,Gunrunner W,AMP Walker W,SEN-TRI W,Opti-Pod W,LO W,Groundmech W,SCRP-R A*,R2 A,TRAK-R A,R6 A,Util-Tec A,ORB-Walker A,BB A,OPT-AR B*,ARM-CORE B*,B1 Heavy B,Strike-Orb B,B2 Heavy B,LNG-Shot B,B2 Super B,Haul-R B,LOM P",
    "Legendary": "JO9-4MN W,PLNK W,FUS-3 W*,RO-TOR W*,Mono-WLKR W,Mecha-Droid W,Proto-Roller W,QIK-BIT A*,R7 A,BB9 A,ORB-XL B*,Opti-STRK B,B2-RP B,Cyclo-Grav B,PZ P",
    "Mythic": "ECG A,LOW-MO W*,RIV-3T W*,LUG-G W*,Loadlifter W,LEP W,RIC-1200 W,RIC W,Snow Mouse W,AXI-POD A*,MO-TRAK A,TRI-TEK A,DRFT-R A,CYCLENS A,X-ONK B*,SRV-O B*,KX B,IG B,TDA P",
    "Iconic": "Scourge Prime P,WG-22 B,D-O W,DJ R-3X W,BB-8 A,CB-23 A,Chopper A,R2-D2 A,IG-11 Marshal B,Mister Bones B,C-3PO P",
}
DROID_RARITY = {x[:x.rfind(" ")]: r for r, s in DROID_RAW.items() for x in s.split(",")}
DROID_NAMES = list(DROID_RARITY)
VARIANTS = ["DEFAULT", "GOLD", "DIAMOND", "RAINBOW", "BESKAR", "GALACTIC", "STELLAR", "KYBER", "SCOURGE"]
VARIANT_LABEL = {"DEFAULT": "Basic", "GOLD": "Gold", "DIAMOND": "Diamond", "RAINBOW": "Rainbow",
                 "BESKAR": "Beskar", "GALACTIC": "Galactic", "STELLAR": "Stellar", "KYBER": "Kyber",
                 "SCOURGE": "Scourge"}
RARITY_WORDS = ["COMMON", "RARE", "EPIC", "LEGENDARY", "MYTHIC", "ICONIC"]
_UP = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_NAME_WL = _UP + "0123456789-"


def _sq(t):
    return re.sub(r"[^A-Z0-9]", "", t.upper())


def _ratio(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def _best(word, options, cut):
    r = max(((_ratio(word, o), o) for o in options), default=(0, None))
    return r[1] if r[0] >= cut else None


def _ocr(img, psm=7, wl=None):
    cfg_ = f"--psm {psm}" + (f" -c tessedit_char_whitelist={wl}" if wl else "")
    img = cv2.copyMakeBorder(img, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=255)
    return pytesseract.image_to_string(img, config=cfg_).upper()


_LOOKALIKE = str.maketrans("0O1IL5S8B2Z6G", "OOIIISSBBZZGG")


def _shape(t):
    """Letters and digits that look alike in the game font count as the same
    (R6 is often read as RG, B1 as 81, IG as 16...)."""
    return t.translate(_LOOKALIKE)


def _match_name(text, pool=None):
    toks = [_sq(t) for t in text.split() if _sq(t)]
    cands = toks + [a + b for a, b in zip(toks, toks[1:])] + ["".join(toks)]
    sc, name = 0, None
    for n in (pool or DROID_NAMES):
        target = _sq(n)
        for t in cands:
            q = max(_ratio(t, target), _ratio(_shape(t), _shape(target)) - 0.01)
            if q > sc:
                sc, name = q, n
    return name, sc


def _color_masks(hsv):
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    return {"white": (S < 60) & (V > 170), "gray": (S < 50) & (V > 110) & (V < 200),
            "yellow": (H >= 15) & (H <= 35) & (S > 90) & (V > 150),
            "blue": (H >= 92) & (H <= 118) & (S > 100) & (V > 100),
            "lavender": (H >= 118) & (H <= 168) & (S > 30) & (V > 140),
            "red": ((H <= 10) | (H >= 169)) & (S > 90) & (V > 120)}


# which variants each text colour can mean
_COLOR_VARS = {"white": ["DEFAULT", "KYBER"], "gray": ["BESKAR"], "yellow": ["STELLAR", "GOLD"],
               "blue": ["DIAMOND"], "lavender": ["GALACTIC", "SCOURGE"], "red": ["RAINBOW"]}


def _variant_from_line(line, h):
    """Read the coloured variant word (left of the rarity pill) on a line image."""
    up = cv2.resize(line, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(up, cv2.COLOR_BGR2HSV)
    masks = _color_masks(hsv)
    # cut off the rarity pill (solid coloured blob right of the word)
    sat = ((hsv[:, :, 1] > 70) & (hsv[:, :, 2] > 140)).astype(np.uint8)
    sat = cv2.morphologyEx(sat, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(sat)
    pills = [st[i] for i in range(1, n) if st[i][4] > 0.55 * st[i][2] * st[i][3]
             and st[i][2] > 1.8 * st[i][3] and st[i][3] > 0.35 * up.shape[0]
             and st[i][0] > 0.15 * up.shape[1]]
    if pills:
        cut = min(p[0] for p in pills)
        for k in masks:
            masks[k][:, cut:] = False
    # the variant word = first run of text columns
    textm = np.zeros(up.shape[:2], bool)
    for k in _COLOR_VARS:
        textm |= masks[k]
    cols = textm.any(axis=0)
    runs, start, last, gap = [], None, None, int(0.45 * h * 5)
    for i, c in enumerate(cols):
        if c:
            if start is None:
                start = i
            elif i - last > gap:
                runs.append((start, last))
                start = i
            last = i
    if start is not None:
        runs.append((start, last))
    word = None
    if runs:
        word = runs[0]
        if word[1] - word[0] < 2 * h * 5 and len(runs) > 1:
            word = (runs[0][0], runs[1][1])
    sl = slice(max(0, word[0] - 5), word[1] + 5) if word else slice(None)
    counts = {k: int(masks[k][:, sl].sum()) for k in _COLOR_VARS}
    total = sum(counts.values()) or 1
    allowed = set()
    for k, c in counts.items():
        if c > 0.2 * total:
            allowed.update(_COLOR_VARS[k])
    if sum(1 for k in ("yellow", "blue", "lavender", "red") if counts[k] > 0.15 * total) >= 2:
        allowed.add("RAINBOW")
    if not allowed:
        allowed = set(VARIANTS)
    best_v, best_s = None, 0
    any_bright = hsv[:, :, 2] > 140
    if pills:
        any_bright[:, cut:] = False
    for m in list(masks.values()) + [any_bright]:
        mm = m.copy()
        if word:
            mm[:, :max(0, word[0] - 5)] = False
            mm[:, word[1] + 5:] = False
        for tok in _ocr(255 - mm.astype(np.uint8) * 255, 7, _UP + " ").split():
            s = _sq(tok)
            if len(s) < 3:
                continue
            for v in VARIANTS:
                q = max(_ratio(s, v), 1.0 if v in s else 0)
                if v not in allowed and q < 0.85:     # colour must agree unless near-exact
                    continue
                if q > best_s:
                    best_s, best_v = q, v
    if best_s >= 0.5:
        return best_v
    return next(iter(allowed)) if len(allowed) == 1 else None


def _word_colours(line):
    """Share of each colour family in the variant word's text (background ignored)."""
    word = cv2.cvtColor(_variant_word(line), cv2.COLOR_BGR2HSV).reshape(-1, 3)
    bg = int(np.median(word[:, 0]))
    vivid = word[(word[:, 1] > 110) & (word[:, 2] > 120)]
    d = np.abs(vivid[:, 0].astype(int) - bg)
    vivid = vivid[np.minimum(d, 180 - d) > 12]          # ignore the background colour
    if len(vivid) < 40:
        return {}
    h = vivid[:, 0]
    fam = {"red": (h <= 8) | (h >= 170), "yellow": (h >= 15) & (h <= 34),
           "green": (h >= 36) & (h <= 80), "blue": (h >= 90) & (h <= 125),
           "purple": (h >= 126) & (h <= 169)}
    return {k: float(m.mean()) for k, m in fam.items()}


def _rainbow_word(line):
    """RAINBOW's letters cycle through colours, so OCR often fails - but it always has
    yellow, blue AND purple letters at once, which no other variant has."""
    c = _word_colours(line)
    return bool(c) and c["yellow"] >= 0.10 and c["blue"] >= 0.15 and c["purple"] >= 0.08


def _green_word(line):
    """Green variant text only ever means an activated green Kyber."""
    c = _word_colours(line)
    return bool(c) and c["green"] >= 0.30 and c["blue"] < 0.10 and c["purple"] < 0.10


def _variant_word(line):
    """The variant word part of a 'VARIANT [PILL]' line: everything left of the pill
    (the pill's black text is the only black text on the line)."""
    hsv = cv2.cvtColor(line, cv2.COLOR_BGR2HSV)
    dark = hsv[:, :, 2] < 70
    cols = np.where(dark.sum(axis=0) >= max(2, int(0.08 * line.shape[0])))[0]
    cols = cols[cols > 0.15 * line.shape[1]]
    cut = int(cols.min() - 0.03 * line.shape[1]) if len(cols) else line.shape[1]
    return line[:, :max(1, cut)]


def _contrast_text(word):
    """Pixels that stand out from the background, whatever colour both are
    (e.g. blue KYBER on a blue background)."""
    lab = cv2.cvtColor(word, cv2.COLOR_BGR2LAB).astype(np.float32)
    bg = np.median(lab.reshape(-1, 3), axis=0)
    d = np.sqrt(((lab - bg) ** 2).sum(axis=2))
    return d > max(30, np.percentile(d, 60))


def _kyber_colour(line):
    """Activated Kyber shows 'KYBER' in its colour: g / b / p, or n (white = not activated)."""
    word0 = _variant_word(line)
    on_card = np.median(cv2.cvtColor(word0, cv2.COLOR_BGR2HSV)[:, :, 2]) < 60
    c = {} if on_card else _word_colours(line)    # droid screen: background-free shares
    if c:
        top = max(("green", "blue", "purple"), key=lambda k: c[k])
        if c[top] >= 0.30:
            return {"green": "g", "blue": "b", "purple": "p"}[top]
    word = _variant_word(line)
    hsv = cv2.cvtColor(word, cv2.COLOR_BGR2HSV)
    if np.median(hsv[:, :, 2]) < 60:              # blueprint card: text on a black bar
        mask = hsv[:, :, 2] > 150
    else:                                         # droid screen: text on a coloured glow
        mask = _contrast_text(word)
    px = hsv[mask]
    if len(px) < 8:
        return None
    sat, hue = np.median(px[:, 1]), np.median(px[:, 0])
    if sat < 60:
        return "n"
    if 25 <= hue <= 88:
        return "g"
    if 88 < hue <= 125:
        return "b"
    if 125 < hue <= 165:
        return "p"
    return None


def _companions_bar(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (88, 150, 150), (112, 255, 255))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(m)
    best = None
    H = bgr.shape[0]
    for i in range(1, n):
        x, y, w, h, a = st[i]
        # the COMPANIONS header is a thick bar (not the thin health bar in the HUD)
        if not (6 * h < w < 16 * h and w > 150 and h >= 0.025 * H and a / (w * h) > 0.6):
            continue
        # ...with the word COMPANIONS written in black across it
        if (cv2.cvtColor(bgr[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY) < 60).mean() < 0.03:
            continue
        if best is None or y > best[1]:          # the lowest one (a blue name glow can
            best = (x, y, w, h)                  # look like a bar too, but sits higher)
    return best


def _read_droid_screen(bgr):
    """The droid screen (WORK / SWAP / FUSION ... menu with COMPANIONS below)."""
    b = _companions_bar(bgr)
    if not b:
        return None
    x, y, w, h = b
    right = x + w
    W = int(10.6 * h)
    left = right - W
    top, bot = max(0, y - int(1.15 * W)), y - int(0.72 * W)
    reg = bgr[top:bot, max(0, left + int(0.05 * W)):right - int(0.1 * W)]
    if reg.size == 0:
        return None
    R = reg.shape[0]
    line, namez = reg[int(0.80 * R):R], reg[int(0.30 * R):int(0.80 * R)]
    up = cv2.resize(line, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    ug, uh = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY), cv2.cvtColor(up, cv2.COLOR_BGR2HSV)
    H_, S_, V_ = uh[:, :, 0], uh[:, :, 1], uh[:, :, 2]
    masks = [ug < 70, (S_ < 90) & (V_ > 150), V_ > 200, (S_ > 80) & (V_ > 120),
             (H_ >= 92) & (H_ <= 118) & (S_ > 110) & (V_ > 110),
             (H_ >= 120) & (H_ <= 170) & (S_ > 35) & (V_ > 160),
             (H_ >= 18) & (H_ <= 34) & (S_ > 90) & (V_ > 190),
             (S_ < 45) & (V_ > 110) & (V_ < 235), (H_ >= 70) & (H_ <= 95) & (S_ > 80) & (V_ > 120)]
    rar = var = None
    word = _variant_word(line)                  # text that stands out from its background
    wm2 = cv2.resize((255 - _contrast_text(word).astype(np.uint8) * 255), None, fx=2, fy=2,
                     interpolation=cv2.INTER_NEAREST)
    for tok in _ocr(wm2, 7, _UP + " ").split():
        s = _sq(tok)
        if len(s) >= 3:
            var = var or _best(s, VARIANTS, 0.7) or next((v for v in VARIANTS if v in s), None)
    for m in masks:
        for tok in _ocr(255 - m.astype(np.uint8) * 255, 7, _UP + " ").split():
            s = _sq(tok)
            if len(s) < 3:
                continue
            rar = rar or _best(s, RARITY_WORDS, 0.75)
            var = var or _best(s, VARIANTS, 0.7)
            var = var or next((v for v in VARIANTS if v in s), None)
            rar = rar or next((r for r in RARITY_WORDS if r in s), None)
        if rar and var:
            break
    if _rainbow_word(line):                      # colour beats OCR for rainbow text
        var = "RAINBOW"
    elif var in (None, "RAINBOW") and _green_word(line):
        var = "KYBER"                            # green text = green Kyber
    nh = cv2.cvtColor(namez, cv2.COLOR_BGR2HSV)
    wm = ((nh[:, :, 1] < 70) & (nh[:, :, 2] > 200)).astype(np.uint8) * 255
    pool = [n for n, r in DROID_RARITY.items() if not rar or r.upper() == rar]
    name, sc = _match_name(_ocr(255 - wm, 7) + " " + _ocr(255 - wm, 8), pool)
    if sc < 0.5:
        return None
    kyber = _kyber_colour(line) if var == "KYBER" else None
    return {"name": name, "variant": var, "kyber": kyber, "source": "droid screen"}


def _name_rows(bgr):
    """Rows of big white letters on black (blueprint card names)."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    white = ((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 200)).astype(np.uint8)
    dark = (hsv[:, :, 2] < 55).astype(np.uint8)
    n, _, st, _ = cv2.connectedComponentsWithStats(white)
    chars = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if not (9 <= h <= 90 and w <= 2.2 * h and a > 12):
            continue
        ring = dark[max(0, y - h // 3):y + h + h // 3, max(0, x - h // 2):x + w + h // 2].mean()
        if ring > 0.35:
            chars.append((x, y, w, h))
    rows = []
    for c in sorted(chars):
        for r in rows:
            last = r[-1]
            if (abs((c[1] + c[3] / 2) - (last[1] + last[3] / 2)) < 0.4 * last[3]
                    and 0.7 < c[3] / last[3] < 1.4 and c[0] - (last[0] + last[2]) < 1.2 * last[3]):
                r.append(c)
                break
        else:
            rows.append([c])
    rows = [r for r in rows if len(r) >= 2]
    rows.sort(key=lambda r: len(r) * np.mean([c[3] for c in r]), reverse=True)
    return rows[:6]


def _read_card(bgr, target=None):
    """A blueprint card. With target=(x, y) (fractions of the image) only the card
    whose name sits there is read - that's where a held card always is."""
    H, W = bgr.shape[:2]
    out = []
    rows = _name_rows(bgr)
    if target:
        def dist(r):
            cx = (min(c[0] for c in r) + max(c[0] + c[2] for c in r)) / 2 / W
            cy = np.mean([c[1] + c[3] / 2 for c in r]) / H
            return abs(cx - target[0]) + abs(cy - target[1])
        # the held card is near the middle and has the biggest name letters on screen
        # (where exactly depends on the camera, so only roughly near the target)
        rows = sorted((r for r in rows if dist(r) < 0.45),
                      key=lambda r: -float(np.median([c[3] for c in r])))[:3]
    for r in rows:
        cs = np.array([(c[0] + c[2] / 2, c[1] + c[3] / 2) for c in r])
        h = float(np.median([c[3] for c in r]))
        ang = np.degrees(np.arctan(np.polyfit(cs[:, 0], cs[:, 1], 1)[0])) if len(r) >= 3 else 0.0
        x0, x1 = min(c[0] for c in r), max(c[0] + c[2] for c in r)
        cy, cx = float(cs[:, 1].mean()), (x0 + x1) / 2
        rot = cv2.warpAffine(bgr, cv2.getRotationMatrix2D((cx, cy), ang, 1.0), (W, H),
                             flags=cv2.INTER_CUBIC, borderValue=(0, 0, 0))
        ni = rot[max(0, int(cy - 0.8 * h)):min(H, int(cy + 0.8 * h)),
                 max(0, int(x0 - 0.4 * h)):min(W, int(x1 + 0.4 * h))]
        if ni.size == 0:
            continue
        nu = cv2.resize(ni, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        nh = cv2.cvtColor(nu, cv2.COLOR_BGR2HSV)
        wm = ((nh[:, :, 1] < 70) & (nh[:, :, 2] > 180)).astype(np.uint8) * 255
        name, sc = _match_name(_ocr(255 - wm, 7, _NAME_WL + " ") + " " + _ocr(255 - wm, 8, _NAME_WL))
        if sc < 0.6:
            continue
        line = rot[min(H - 1, int(cy + 0.55 * h)):min(H, int(cy + 1.6 * h)),
                   max(0, int(x0 - 0.4 * h)):min(W, int(x0 + max(5.5 * h, (x1 - x0) * 0.9)))]
        var = _variant_from_line(line, 0.5 * h) if line.size else None
        if not var and not target:
            continue                                 # not sure it's a card - skip
        kyber = None                                 # blueprints: colour isn't reliable
        # the held card is closest to the camera, so its name is the biggest
        out.append((round(sc, 1), h, {"name": name, "variant": var, "kyber": kyber,
                                      "source": "blueprint"}))
    out.sort(key=lambda o: (o[0] >= 0.6, o[1], o[0]), reverse=True)
    return out[0][2] if out else None


def holding_card(bgr):
    """True while a blueprint is held: 'HELD ITEM' over the hotbar or the
    'TOSS BLUEPRINT ON CRAFTING STATION' hint is on screen."""
    H, W = bgr.shape[:2]
    for (x0, y0, x1, y1) in ((0.70, 0.78, 0.92, 0.86), (0.30, 0.72, 0.70, 0.80)):
        g = cv2.cvtColor(bgr[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)], cv2.COLOR_BGR2GRAY)
        g = cv2.resize(g, None, fx=2, fy=2)
        _, bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        for img in (bw, 255 - bw):
            t = _sq(pytesseract.image_to_string(img, config="--psm 6"))
            if any(k in t for k in ("HELDITEM", "TOSSBLUE", "BLUEPRINT", "CRAFTINGSTATION")):
                return True
    return False


def read_droid(bgr):
    """{'name', 'variant', 'kyber', 'source'} for the droid on the droid screen or the
    blueprint card you're holding (held cards sit above your character), or None."""
    r = _read_droid_screen(bgr)
    if r:
        return r
    # the 'HELD ITEM' hint isn't always readable, so try the held card anyway -
    # the confirm box shows what was read before anything is saved
    return read_held_card(bgr)


def read_held_card(bgr):
    """The card held above your character: its name is always in the same spot."""
    H, W = bgr.shape[:2]
    zone = bgr[int(0.08 * H):int(0.72 * H), int(0.18 * W):int(0.68 * W)]
    return _read_card(zone, target=(0.45, 0.55))

# -------------------------------------------------------------- inventory ---
# F9 = add the droid on screen to your website inventory, F10 = remove it.
# Press once to read it, again within 8 seconds to confirm.

VARIANT_KEY = {"DEFAULT": "0", "GOLD": "1", "DIAMOND": "2", "RAINBOW": "3",
               "BESKAR": "4", "GALACTIC": "5", "STELLAR": "6",
               "SCOURGE": "8"}            # same keys as the website (Kyber = "7n"/"7g"/...)
KYBER_KEYS = ["7n", "7g", "7b", "7p"]
KYBER_NAMES = {"n": "Not Activated", "g": "Green", "b": "Blue", "p": "Purple"}
_players = {"list": []}


def _fs_value(v):
    """Firestore REST value -> Python."""
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return v["doubleValue"]
    if "stringValue" in v:
        return v["stringValue"]
    if "booleanValue" in v:
        return v["booleanValue"]
    if "mapValue" in v:
        return {k: _fs_value(x) for k, x in v["mapValue"].get("fields", {}).items()}
    if "arrayValue" in v:
        return [_fs_value(x) for x in v["arrayValue"].get("values", [])]
    return None


def list_players():
    """The website players on this login: [{'id', 'name'}]."""
    r = _http(_firestore_base() + f"/users/{_account['uid']}/players?pageSize=50", token=id_token())
    out = []
    for d in r.get("documents", []):
        pid = d["name"].rsplit("/", 1)[-1]
        name = d.get("fields", {}).get("name", {}).get("stringValue", pid)
        out.append({"id": pid, "name": name})
    out.sort(key=lambda p: p["name"].lower())
    _players["list"] = out
    return out


def current_player():
    pl = _players["list"] or list_players()
    if not pl:
        return None
    return next((p for p in pl if p["id"] == cfg["inventory_player"]), pl[0])


def variant_label(variant, key=None):
    if variant == "KYBER":
        k = (key or "7" + cfg["kyber_default"])[1:]
        return f"Kyber ({KYBER_NAMES.get(k, k)})" if k == "n" else f"Kyber {KYBER_NAMES.get(k, k)}"
    return VARIANT_LABEL.get(variant, variant.title())


def change_inventory(name, variant, delta, kyber=None):
    """Add (+1) or remove (-1) one droid on the website. Returns a message."""
    player = current_player()
    if not player:
        return "No players on your website login yet - add one on the site first."
    url = _firestore_base() + f"/users/{_account['uid']}/players/{player['id']}"
    doc = _http(url, token=id_token())
    inv = _fs_value(doc.get("fields", {}).get("inv", {"mapValue": {}})) or {}
    entry = dict(inv.get(name, {}))
    if DROID_RARITY.get(name) == "Iconic":
        key, label = "0", ""
    elif variant == "KYBER":
        if delta > 0:
            key = "7" + (kyber or cfg["kyber_default"])
        elif kyber and entry.get("7" + kyber, 0) > 0:
            key = "7" + kyber
        else:
            key = next((k for k in KYBER_KEYS if entry.get(k, 0) > 0), None)
        label = variant_label(variant, key) + " "
    elif variant not in VARIANT_KEY:
        return f"The website has no {variant_label(variant)} tier yet - not saved."
    else:
        key, label = VARIANT_KEY[variant], variant_label(variant) + " "
    if delta < 0 and (not key or entry.get(key, 0) <= 0):
        return f"{player['name']} has no {label}{name} to remove."
    new = max(0, min(999, entry.get(key, 0) + delta))
    if new:
        entry[key] = new
    else:
        entry.pop(key, None)
    # only this droid's entry (and updatedAt) is changed - the rest of the save is untouched
    inv_fields = {}
    if entry:
        inv_fields[name] = {"mapValue": {"fields": {k: {"integerValue": str(c)}
                                                    for k, c in entry.items()}}}
    body = {"fields": {"inv": {"mapValue": {"fields": inv_fields}},
                       "updatedAt": {"integerValue": str(int(time.time() * 1000))}}}
    import urllib.parse
    mask = "updateMask.fieldPaths=" + urllib.parse.quote(f"inv.`{name}`") + \
           "&updateMask.fieldPaths=updatedAt"
    _http(url + "?" + mask, body, token=id_token(), method="PATCH")
    verb = "Added" if delta > 0 else "Removed"
    state["data_version"] = state.get("data_version", 0) + 1     # app window reloads
    return f"{verb} {label}{name} – {player['name']} now has {new}"


def inv_show(lines, seconds=6, color=None):
    """Show text in the small inventory box under the top-left badge."""
    if isinstance(lines, str):
        lines = [lines]
    state["inv_lines"] = lines
    state["inv_color"] = color
    state["inv_until"] = time.time() + seconds
    print("Inventory:", " | ".join(lines))


def grab_without_overlays(sct):
    """Screenshot with our own boxes out of the way (they're also excluded from
    captures on Windows 10/11, this is the backup for older Windows)."""
    state["capture_hide"] = True
    time.sleep(0.3)                               # let the boxes hide (refresh is 100 ms)
    try:
        return grab_screen(sct)
    finally:
        state["capture_hide"] = False


def show_pending(note=""):
    """Draw the confirm box for the droid waiting to be added/removed."""
    p = state.get("inv_pending")
    if not p:
        return
    iconic = DROID_RARITY.get(p["name"]) == "Iconic"
    if iconic:
        vtext = "ICONIC"
    elif not p["variant"]:
        vtext = "?  (Up/Down to pick)"
    else:
        vtext = p["variant"]
        if p["variant"] == "KYBER":
            vtext += f" ({KYBER_NAMES.get(p.get('kyber') or cfg['kyber_default'], '?').upper()})"
    verb = "Add to" if p["action"] == "add" else "Remove from"
    lines = [f"Droid: {p['name'].upper()}", f"Variant: {vtext}",
             f"{verb} {p['player']}? Press {p['key']} again"]
    if not iconic:
        lines.append("Wrong? Up/Down variant" + (" \u00b7 Left/Right colour"
                                                  if p["variant"] == "KYBER" else ""))
    if note:
        lines.append(note)
    inv_show(lines, max(1, p["expires"] - time.time()), RED if note else None)


def adjust_pending(key):
    """Up/Down change the variant, Left/Right the Kyber colour, while confirming."""
    p = state.get("inv_pending")
    if not p or time.time() > p["expires"] or DROID_RARITY.get(p["name"]) == "Iconic":
        return False
    K = keyboard.Key
    if key in (K.up, K.down):
        step = 1 if key == K.down else -1
        i = VARIANTS.index(p["variant"]) if p["variant"] in VARIANTS else (-1 if step > 0 else 0)
        p["variant"] = VARIANTS[(i + step) % len(VARIANTS)]
    elif key in (K.left, K.right) and p["variant"] == "KYBER":
        cols = list(KYBER_NAMES)
        cur = p.get("kyber") or cfg["kyber_default"]
        p["kyber"] = cols[(cols.index(cur) + (1 if key == K.right else -1)) % len(cols)]
    else:
        return False
    p["expires"] = time.time() + 12
    show_pending()
    return True


def inventory_hotkey(action):
    """Called from the hotkey listener: read the droid, then confirm on a 2nd press."""
    if state.get("inv_busy"):
        return
    if not accounts_enabled() or not _account["refresh"]:
        inv_show("Sign in to use the inventory keys", 4, RED)
        return
    if _account.get("offline"):
        inv_show("Offline - inventory changes need internet", 4, RED)
        return
    pend = state.get("inv_pending")
    key_label = hk_label("inv_add" if action == "add" else "inv_remove")

    def work():
        state["inv_busy"] = True
        try:
            if pend and pend["action"] == action and time.time() < pend["expires"]:
                if not pend["variant"] and DROID_RARITY.get(pend["name"]) != "Iconic":
                    show_pending("Pick the variant with Up/Down first")
                    return
                state["inv_pending"] = None
                inv_show("Saving\u2026", 5)
                msg = change_inventory(pend["name"], pend["variant"], 1 if action == "add" else -1,
                                       pend.get("kyber"))
                ok = msg.startswith(("Added", "Removed"))
                inv_show(("\u2714 " if ok else "") + msg, 6, GREEN if ok else RED)
                return
            inv_show("Reading droid\u2026", 6)
            # several looks a moment apart, keep the answer they agree on
            looks, got, reader = [], None, read_droid
            with mss.mss() as sct:
                for attempt in range(4):
                    img = grab_without_overlays(sct)
                    if attempt == 0:
                        got = _read_droid_screen(img)
                        if got:
                            reader = _read_droid_screen
                        else:                     # held blueprint card (with or
                            reader = read_held_card   # without the HELD ITEM hint)
                            got = read_held_card(img)
                    else:
                        got = reader(img)
                    if got and got.get("variant"):
                        looks.append(got)
                    keys = [(g["name"], g["variant"], g.get("kyber")) for g in looks]
                    if keys and max(keys.count(k) for k in keys) >= 2:
                        break                     # two looks agree - good enough
                    if attempt == 0 and not got:
                        break                     # nothing there at all
                    time.sleep(0.25)
            if not looks and got:
                looks = [got]                     # name only - report it
            r = None
            if looks:
                keys = [(g["name"], g["variant"], g.get("kyber")) for g in looks]
                best = max(set(keys), key=keys.count)
                r = looks[keys.index(best)]
            if not r:
                inv_show(["No droid found.", "Open the droid screen, or hold the",
                          "blueprint card, then try again."], 6, RED)
                return
            player = current_player()
            if not player:
                inv_show("No players on your website login yet", 5, RED)
                return
            state["inv_pending"] = {"action": action, "name": r["name"], "variant": r["variant"],
                                    "kyber": r.get("kyber"), "expires": time.time() + 12,
                                    "player": player["name"], "key": key_label}
            show_pending()
        except AuthError as e:
            inv_show(f"Website said no: {e}", 6, RED)
        except OSError:
            inv_show("Couldn't reach the website - check your internet", 6, RED)
        except Exception as e:
            print("Inventory error:", e)
            inv_show(f"Inventory error: {e}", 6, RED)
        finally:
            state["inv_busy"] = False

    threading.Thread(target=work, daemon=True).start()



# ------------------------------------------------------------ fishing stats ---
# Chips and credits earned per fishing round, read from the HUD counters, plus
# your best round ever (saved in fish_stats.json).

FISH_FILE = "fish_stats.json"
ROUND_GAP = 60          # no fishing prompt for this long = the round is over


_hud_coin = {"box": None}


def _find_coin(bgr):
    """The gold credits coin at the bottom-left (its position is remembered, because
    orange or yellow scenery behind it can make it hard to find again)."""
    H, W = bgr.shape[:2]
    y0 = int(0.70 * H)
    reg = bgr[y0:int(0.92 * H), :int(0.30 * W)]
    hsv = cv2.cvtColor(reg, cv2.COLOR_BGR2HSV)
    n, _, st, _ = cv2.connectedComponentsWithStats(cv2.inRange(hsv, (12, 120, 150), (35, 255, 255)))
    coins = [st[i] for i in range(1, n) if 0.035 * H < st[i][3] < 0.09 * H
             and 0.75 < st[i][2] / st[i][3] < 1.3 and st[i][4] > 0.5 * st[i][2] * st[i][3]]
    old = _hud_coin["box"]
    if old:
        coins = [c for c in coins if abs(c[3] - old[3]) < 0.2 * old[3]
                 and abs(c[0] - old[0]) < old[3] and abs(c[1] + y0 - old[1]) < old[3]]
    if coins:
        x, y, w, h, _ = min(coins, key=lambda c: c[1])
        _hud_coin["box"] = (int(x), int(y + y0), int(w), int(h))
    return _hud_coin["box"]


def _hud_number(bgr, x0, x1, y0, y1, h, ocr=True):
    """Read the white HUD number in a box. Only letter-sized white shapes are kept, so
    bright scenery behind the text (lights, white floors) is ignored."""
    H, W = bgr.shape[:2]
    x0, x1, y0, y1 = max(0, int(x0)), min(W, int(x1)), max(0, int(y0)), min(H, int(y1))
    if x1 - x0 < 5 or y1 - y0 < 5:
        return "", None
    box = bgr[y0:y1, x0:x1]
    hs = cv2.cvtColor(box, cv2.COLOR_BGR2HSV)
    wm = ((hs[:, :, 1] < 45) & (hs[:, :, 2] > 225)).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(wm)
    keep = np.zeros_like(wm)
    chars = []
    for i in range(1, n):
        cx, cy, cw, ch, a = st[i]
        letter = 0.35 * h <= ch <= 1.05 * h and cw <= 1.1 * h
        dot = ch < 0.3 * h and cw < 0.3 * h and a > 2 and cy > 0.45 * (y1 - y0)
        if (letter or dot) and cx > 0 and cx + cw < box.shape[1] and cy > 0 and cy + ch < box.shape[0]:
            keep[lab == i] = 1
            chars.append((cx, cw, letter))
    chars.sort()
    if not any(c[2] for c in chars):
        return "", None
    # the number = the first group of letters (stop at a big gap)
    first = next(k for k, c in enumerate(chars) if c[2])
    end = chars[first][0] + chars[first][1]
    for cx, cw, _ in chars[first + 1:]:
        if cx - end > 0.6 * h:
            break
        end = max(end, cx + cw)
    if not ocr:                                    # just where the number ends
        return "", x0 + end
    a = max(0, chars[first][0] - 4)
    img = cv2.resize(255 - keep[:, a:end + 4] * 255, None, fx=2, fy=2,
                     interpolation=cv2.INTER_NEAREST)
    img = cv2.copyMakeBorder(img, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=255)
    t = pytesseract.image_to_string(img, config="--psm 7").strip()
    return re.sub(r"[^0-9A-Za-z.]", "", t).replace("H", "K").replace("k", "K"), x0 + end


def read_chips(bgr):
    """Just the chip count (one OCR call - cheap enough to run between fishing rows)."""
    coin = _find_coin(bgr)
    if not coin:
        return None
    x, y, w, h = coin
    h = max(h, w)
    _, end = _hud_number(bgr, x + w + 0.1 * h, x + 6.0 * h, y - 0.15 * h, y + 0.85 * h,
                         0.75 * h, ocr=False)
    if not end:                                    # can't place the chips number safely
        return None
    c0 = end + 0.75 * h
    t, _ = _hud_number(bgr, c0, c0 + 2.6 * h, y - 0.12 * h, y + 0.5 * h, 0.4 * h)
    return parse_amount(t) if re.fullmatch(r"\d+(\.\d+)?[KMBT]?", t) and len(t) >= 3 else None


def read_hud(bgr):
    """{'credits': value, 'chips': value} from the bottom-left HUD (either may be None).
    Positions are measured from the gold coin: the credits number sits right of it,
    the chips number (smaller) right of the chip icon after it."""
    out = {"credits": None, "chips": None}
    coin = _find_coin(bgr)
    if not coin:
        return out
    x, y, w, h = coin
    h = max(h, w)                                  # coin box can be clipped - use the bigger side
    t, end = _hud_number(bgr, x + w + 0.1 * h, x + 6.0 * h, y - 0.15 * h, y + 0.85 * h, 0.75 * h)
    t = re.sub(r"[O0]a$", "Qa", t)
    if re.fullmatch(r"\d{1,3}\.\d{1,2}(K|M|B|T|Qa|Qi|Sx|Sp)", t):
        out["credits"] = parse_amount(t)
    # chips: after the chip icon that follows the credits number
    if not end:
        return out
    c0 = end + 0.75 * h
    t2, _ = _hud_number(bgr, c0, c0 + 2.6 * h, y - 0.12 * h, y + 0.5 * h, 0.4 * h)
    if re.fullmatch(r"\d+(\.\d+)?[KMBT]?", t2):
        out["chips"] = parse_amount(t2)
    return out


def load_fish():
    try:
        with open(FISH_FILE) as f:
            return json.load(f)
    except Exception:
        return {"best_chips": 0, "best_credits": 0, "rounds": 0}


def save_fish(d):
    try:
        with open(FISH_FILE, "w") as f:
            json.dump(d, f)
    except Exception as e:
        print("Couldn't save fishing stats:", e)


class _Steady:
    """Only trust a HUD number once two reads in a row agree."""
    def __init__(self):
        self.last, self.value = None, None

    def feed(self, v):
        if v is not None and self.last is not None and abs(v - self.last) <= 0.006 * max(v, 1):
            self.value = v
        self.last = v if v is not None else self.last
        return self.value


CHIP_CHECK_EVERY = 60    # seconds between chip checks while fishing
KEYS_QUIET = 3           # only check when no keys have been on screen for this long


def _chips_now(sct, prev):
    """Two quick reads that must agree; a big jump (1.5x+) needs a third to agree too."""
    a = read_chips(grab_screen(sct))
    time.sleep(0.3)
    b = read_chips(grab_screen(sct))
    if a is None or b is None or abs(a - b) > 0.01 * max(a, b):
        return None
    if prev and (a > prev * 1.5 or a < prev / 1.5):
        time.sleep(0.3)
        c = read_chips(grab_screen(sct))
        if c is None or abs(c - a) > 0.01 * a:
            return None
    return a


def fish_tracker():
    """Background thread for fishing stats. Pressing keys always comes first: chips
    are only read when no key row has been on screen for a few seconds, and at most
    once a minute (plus once when a round ends)."""
    fr = state["fish_round"]
    last_read, last_val = 0.0, None
    with mss.mss() as sct:
        while state["enabled"] and state["mode"] == "fishing" and not state["quit"]:
            now = time.time()
            since_keys = now - state.get("fish_last_prompt", 0)
            try:
                if since_keys < KEYS_QUIET:                 # fishing right now
                    if not fr["active"]:
                        fr.update(active=True, chips0=last_val, chips=0, started=now)
                else:
                    round_over = fr["active"] and since_keys > ROUND_GAP
                    every = CHIP_CHECK_EVERY if last_val is not None else 2   # need a start value
                    if round_over or now - last_read >= every:
                        v = _chips_now(sct, last_val)
                        last_read = time.time()
                        if v is not None:
                            last_val = v
                            if fr["active"]:
                                if fr["chips0"] is None:
                                    fr["chips0"] = v
                                fr["chips"] = max(0, v - fr["chips0"])
                    if round_over:
                        finish_round()
            except Exception as e:
                print("Fishing stats error:", e)
            time.sleep(0.5)
    if fr["active"]:
        finish_round()


_fish_cache = {"data": None}


def load_fish_cached():
    if _fish_cache["data"] is None:
        _fish_cache["data"] = load_fish()
    return _fish_cache["data"]


def finish_round():
    fr = state["fish_round"]
    fr["active"] = False
    if fr["chips"] <= 0:
        return
    d = load_fish()
    d["rounds"] = d.get("rounds", 0) + 1
    best = fr["chips"] > d.get("best_chips", 0)
    if best:
        d["best_chips"] = fr["chips"]
    save_fish(d)
    _fish_cache["data"] = d
    fr["last"] = fr["chips"]
    msg = f"Round done: +{format_amount(fr['chips'])} chips"
    if best:
        msg += "  \u2b50 NEW BEST!"
    notify(msg, 15)


def fishing_stats_text():
    fr, d = state["fish_round"], load_fish_cached()
    if fr["active"]:
        cur = (f"This round: +{format_amount(fr['chips']) if fr['chips'] else '0'} chips"
               " (checked between rows)")
    elif fr.get("last"):
        cur = f"Last round: +{format_amount(fr['last'])} chips"
    else:
        cur = "This round: starts at the first PRESS TO FISH!"
    return cur + f"\nBest round: +{format_amount(d.get('best_chips', 0)) or '0'} chips"


# ------------------------------------------------------------ auto clicker ---

def foreground_title():
    if sys.platform != "win32":
        return ""
    try:
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(hwnd, buf, 256)
        return buf.value
    except Exception:
        return ""


def read_balance(bgr):
    """Your credit balance from the bottom-left of the HUD (the number next to the
    gold coin), e.g. 6.23Qa -> 6.23e15. None if it can't be read."""
    H, W = bgr.shape[:2]
    x1 = int(0.30 * W)
    reg = bgr[int(0.70 * H):int(0.92 * H), :x1]
    hsv = cv2.cvtColor(reg, cv2.COLOR_BGR2HSV)
    n, _, st, _ = cv2.connectedComponentsWithStats(cv2.inRange(hsv, (12, 120, 150), (35, 255, 255)))
    coins = [st[i] for i in range(1, n) if st[i][3] > 0.035 * H
             and 0.7 < st[i][2] / st[i][3] < 1.4 and st[i][4] > 0.4 * st[i][2] * st[i][3]]
    if not coins:
        return None
    x, y, w, h, _ = min(coins, key=lambda c: c[1])          # topmost coin = the balance
    txt = reg[max(0, y - int(0.15 * h)):y + int(0.85 * h), x + w:min(x1, x + w + int(6 * h))]
    hs = cv2.cvtColor(txt, cv2.COLOR_BGR2HSV)
    wm = ((hs[:, :, 1] < 35) & (hs[:, :, 2] > 235)).astype(np.uint8)
    n, _, st, _ = cv2.connectedComponentsWithStats(wm)
    chars = sorted([st[i] for i in range(1, n) if st[i][3] > 0.3 * h
                    or (st[i][4] > 4 and st[i][1] > 0.5 * txt.shape[0])], key=lambda c: c[0])
    keep = []
    for c in chars:                               # stop at the gap after the number
        if keep and c[0] - (keep[-1][0] + keep[-1][2]) > 0.5 * h:
            break
        keep.append(c)
    if not keep:
        return None
    a, b = max(0, keep[0][0] - 4), keep[-1][0] + keep[-1][2] + 4
    img = cv2.resize(255 - wm[:, a:b] * 255, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
    img = cv2.copyMakeBorder(img, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=255)
    t = pytesseract.image_to_string(img, config="--psm 7").strip()
    return parse_amount(t) if re.search(r"[A-Za-z]$", t) else None


def _confirmed(hist, raw, prev):
    hist.append(raw)
    del hist[:-3]

    def close(a, b):                              # same number, allowing a little growth
        return a is not None and b is not None and b > 0 and 0.87 <= a / b <= 1.15
    if raw is None or len(hist) < 2 or not close(raw, hist[-2]):
        return None
    if prev and (raw > prev * 1.5 or raw < prev / 1.5):
        if not (len(hist) == 3 and all(close(raw, h) for h in hist)):
            return None
    return raw


def track_earnings(sct):
    """Sample the balance and keep a running credits-per-second estimate."""
    e = state["earn"]
    hud = read_hud(grab_screen(sct))
    now = time.time()
    # Misreads (a dropped or extra digit, the coin's "7") must never count as money
    # made: a number is trusted once two reads agree, and a big jump (over 1.5x
    # up or down) only once it has been seen three times in a row.
    v = _confirmed(e.setdefault("cred_reads", []), hud["credits"], e["prev"])
    c = _confirmed(e.setdefault("chip_reads", []), hud["chips"], e.get("chips_prev"))
    if c is not None:                             # chips (Scrap Hit): add increases only
        if e.get("chips_prev") is not None and c > e["chips_prev"]:
            e["chips_made"] = e.get("chips_made", 0) + c - e["chips_prev"]
        e["chips_prev"] = c
    if v is None:
        return
    s = e["samples"]
    if s:
        last = s[-1][1]
        if v < last * 0.98:                       # spent credits - start measuring again
            s.clear()
    # credits made while active: add every increase, skip drops (spending) and the
    # gap after a pause (so time spent paused isn't counted)
    if e["prev"] is not None and v > e["prev"]:
        e["made"] += v - e["prev"]
    e["prev"] = v
    if e["start"] is None:
        e["start"] = v
    s.append((now, v))
    del s[:-60]                                   # last ~5 minutes
    e["last"] = v


def earnings_text():
    """Overlay lines for the clicker. The estimate refreshes once a minute."""
    e = state["earn"]
    now = time.time()
    if not e["text"] or e["text"].startswith("Measuring") or now - e["text_time"] >= 60:
        new = _estimate_text()
        if new != e["text"]:
            e["text"], e["text_time"] = new, now
    made = f"Made while active: +{format_amount(e['made'])}" if e["made"] > 0 else \
        "Made while active: \u2013"
    nxt = ""
    if not e["text"].startswith("Measuring"):
        nxt = f" \u00b7 next update in {max(0, 60 - int(now - e['text_time']))}s"
    out = f"{e['text']}{nxt}\n{made}"
    if cfg["clicker_kind"] == "scrap":
        chips = e.get("chips_made", 0)
        mins = state.get("active_secs", 0) / 60
        rate = f" \u00b7 {chips / mins:,.0f}/min" if mins >= 1 else ""
        out += f"\nChips: +{format_amount(chips) if chips else '0'}{rate}"
        hist = load_scrap()
        if hist["minutes"] >= 1:
            out += (f" \u00b7 your average {hist['chips'] / hist['minutes']:,.0f}/min "
                    f"({hist['sessions']} session{'s' if hist['sessions'] != 1 else ''})")
    return out


SCRAP_FILE = "scrap_stats.json"


def load_scrap():
    try:
        with open(SCRAP_FILE) as f:
            return json.load(f)
    except Exception:
        return {"sessions": 0, "minutes": 0.0, "chips": 0.0, "history": []}


def save_scrap_session():
    """Add this Scrap Hit session to the all-time chips-per-minute data."""
    e = state.get("earn") or {}
    mins = state.get("active_secs", 0) / 60
    chips = e.get("chips_made", 0)
    if mins < 1:                                   # too short to mean anything
        return
    d = load_scrap()
    d["sessions"] += 1
    d["minutes"] += mins
    d["chips"] += chips
    d.setdefault("history", []).append({"t": int(time.time()), "minutes": round(mins, 1),
                                        "chips": chips, "per_min": round(chips / mins, 1)})
    d["history"] = d["history"][-200:]
    try:
        with open(SCRAP_FILE, "w") as f:
            json.dump(d, f)
    except Exception as ex:
        print("Couldn't save scrap stats:", ex)


def _estimate_text():
    s = state["earn"]["samples"]
    if len(s) < 3 or s[-1][0] - s[0][0] < 20:
        return "Measuring your earnings\u2026 (needs ~20 s)"
    rate = (s[-1][1] - s[0][1]) / (s[-1][0] - s[0][0])
    if rate <= 0:
        return "Balance isn't going up yet"
    mins = 1e15 / rate / 60
    per = (f"{mins * 60:.0f} sec" if mins < 1 else f"{mins:.1f} min" if mins < 10
           else f"{mins:.0f} min" if mins < 120 else f"{mins / 60:.1f} h")
    return f"Earning ~{format_amount(rate * 60)}/min \u00b7 1 Qa \u2248 {per}"


def fmt_duration(sec):
    sec = int(sec)
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else \
        f"{sec // 60}:{sec % 60:02d}"


def run_clicker():
    """Scrap Hit: left-click continuously. Potion Grab: press F every few seconds."""
    try:
        _run_clicker()
    finally:
        if cfg["clicker_kind"] == "scrap":
            save_scrap_session()


def _run_clicker():
    next_click = time.perf_counter()
    last_tick, next_sample = time.time(), 0.0
    sct = mss.mss()
    while state["enabled"] and state["mode"] == "clicker" and not state["quit"]:
        now = time.time()
        want = cfg["clicker_window"].strip().lower()
        if want and want not in foreground_title().lower():
            state["status"] = f"Paused - {cfg['clicker_window']} isn't in front"
            state["earn"]["prev"] = None          # don't count what's earned while paused
            state["earn"]["samples"].clear()
            time.sleep(0.25)
            next_click = time.perf_counter()
            last_tick = time.time()
            continue
        state["active_secs"] += min(1.0, now - last_tick)   # only time actually running
        last_tick = now
        if now >= next_sample:                    # check the balance every 5 seconds
            next_sample = now + 5
            try:
                track_earnings(sct)
            except Exception as e:
                print("Balance read failed:", e)
        if cfg["clicker_kind"] == "potion":
            press(cfg["interact_key"])
            state["clicks"] += 1
            end = time.time() + cfg["potion_interval"]
            while (time.time() < end and state["enabled"] and state["mode"] == "clicker"
                   and not state["quit"] and cfg["clicker_kind"] == "potion"):
                state["status"] = f"Pressed F \u00b7 next in {end - time.time():.0f}s"
                time.sleep(0.1)
                state["active_secs"] += min(1.0, time.time() - last_tick)
                last_tick = time.time()
            continue
        state["status"] = "Clicking"
        pydirectinput.mouseDown()
        time.sleep(0.01)
        pydirectinput.mouseUp()
        state["clicks"] += 1
        next_click += 1.0 / max(1, cfg["clicker_cps"])
        delay = next_click - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        else:
            next_click = time.perf_counter()


# ---------------------------------------------------------------- windows ---

BG, FG, DIM = "#14171c", "#e8e8e8", "#8a9099"
GREEN, AMBER, RED = "#3ddc84", "#ffb020", "#ff5a5a"
DASH = "\u2013"


def hwnd_of(win):
    win.update_idletasks()
    return ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()


def make_click_through(win):
    """Let mouse clicks pass through the overlay to the game, and keep the overlay out
    of screenshots so it never covers what the app reads (Windows only)."""
    if sys.platform != "win32":
        return
    try:
        hwnd = hwnd_of(win)
        style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
        style |= 0x80000 | 0x20 | 0x80 | 0x08000000  # layered, transparent, toolwindow, noactivate
        ctypes.windll.user32.SetWindowLongW(hwnd, -20, style)
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd_of(win), 0x11)  # exclude from capture
    except Exception:
        pass


def force_focus(win):
    """Pull the window in front of the game so the mouse is freed."""
    if sys.platform == "win32":
        try:
            u = ctypes.windll.user32
            u.keybd_event(0x12, 0, 0, 0)   # tap Alt - lets Windows switch focus
            u.keybd_event(0x12, 0, 2, 0)
            u.SetForegroundWindow(hwnd_of(win))
        except Exception:
            pass
    win.focus_force()


def select_area(root):
    """Full-screen see-through layer: drag a box with the mouse."""
    m = screen()
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.geometry(f"{m['width']}x{m['height']}+{m['left']}+{m['top']}")
    win.attributes("-topmost", True)
    win.attributes("-alpha", 0.45)
    c = tk.Canvas(win, bg="black", highlightthickness=0, cursor="crosshair")
    c.pack(fill="both", expand=True)
    c.create_text(m["width"] // 2, 60, fill="white", font=("Segoe UI", 20, "bold"),
                  text="Drag a box around the key tiles\n"
                       "Esc = cancel   \u00b7   R = back to automatic", justify="center")
    drag = {"x": 0, "y": 0, "rect": None}

    def close(_=None):
        win.destroy()

    def down(e):
        drag["x"], drag["y"] = e.x, e.y
        drag["rect"] = c.create_rectangle(e.x, e.y, e.x, e.y, outline=GREEN, width=3)

    def move(e):
        if drag["rect"]:
            c.coords(drag["rect"], drag["x"], drag["y"], e.x, e.y)

    def up(e):
        x0, x1 = sorted((drag["x"], e.x))
        y0, y1 = sorted((drag["y"], e.y))
        if x1 - x0 < 0.2 * m["width"] or y1 - y0 < 0.08 * m["height"]:
            notify(f"Box too small - it must fit a whole key row. Press {hk_label('area')} to try again.")
        else:
            cfg["search_area"] = {"left": m["left"] + x0, "top": m["top"] + y0,
                                  "width": x1 - x0, "height": y1 - y0}
            save_config()
            notify("Custom scan area saved.")
        close()

    def reset(_=None):
        cfg["search_area"] = None
        save_config()
        notify("Back to automatic scan area.")
        close()

    c.bind("<ButtonPress-1>", down)
    c.bind("<B1-Motion>", move)
    c.bind("<ButtonRelease-1>", up)
    win.bind("<Escape>", close)
    win.bind("r", reset)
    win.bind("R", reset)
    force_focus(win)


# ---------------------------------------------------- next rebirth (overlay) ---
# Same rebirth list as the LMS Droid Tycoon website: per cycle, 40 levels of
# "Droid tier,Droid tier,Droid tier" (b g d r s x t k = Basic..Kyber).
REBIRTH_CYCLES = [
    "CB b,Pit b,DRK-1 Probe b|BDX Explorer b,2BB b,Bal-Core b|A-LT b,BU-4D b,R9 g|ARG g,B1 Security g,Groundmech b|BU-4D g,HOV-R g,R9 d|A-LT d,ARG d,Groundmech g|BU-4D d,B1 Security d,BB g|HOV-R d,LO g,Util-Tec g|TRAK-R g,R6 g,Groundmech r|Strike-Orb g,Haul-R r,LO r|AMP Walker r,B1 Heavy r,BB9 b|Proto-Roller g,Mecha-Droid b,Mono-WLKR b|R7 b,Cyclo-Grav b,B2-RP b|Opti-STRK b,Mono-WLKR g,Mecha-Droid g|B2-RP g,BB9 g,R7 g|Opti-STRK g,Mono-WLKR d,Proto-Roller d|B2-RP d,Cyclo-Grav d,Mecha-Droid d|BB9 d,R7 d,Mono-WLKR r|B2-RP r,Cyclo-Grav r,Proto-Roller r|R7 r,Opti-STRK r,Mecha-Droid r|BB s,ORB-Walker s,Groundmech s|AMP Walker s,B1 Heavy s,Proto-Roller s|Opti-STRK s,Mono-WLKR s,R7 s|BB9 s,Cyclo-Grav s,MO-TRAK b|B2-RP s,IG b,DRFT-R g|CYCLENS g,Loadlifter d,RIC-1200 r|KX d,TRI-TEK r,Snow Mouse s|Proto-Roller x,MO-TRAK r,DRFT-R s|Mono-WLKR x,Mecha-Droid x,IG s|B2-RP x,CYCLENS s,Loadlifter x|SEN-TRI t,Proto-Roller s,KX s|ORB-Walker x,Opti-Pod x,RIC s|B1 Heavy t,Cyclo-Grav x,DRFT-R x|Groundmech t,BB t,CYCLENS x|BB9 t,IG t,Snow Mouse t|BDX Explorer k,2BB k,A-LT k|B1 Heavy k,Groundmech k,BB k|R2 k,R6 k,BB9 k|R7 k,Proto-Roller k,IG k|Snow Mouse k,DRFT-R k,CYCLENS k",
    "ID10 b,Mouse b,Gonk b|ROLL-R b,Senate Hovercam b,NAV-EX b|R4 b,VECT-Arm b,BDX Explorer g|2BB g,Bal-Core g,ORB-Walker b|R4 g,VECT-Arm g,NAV-EX g|Gunrunner b,2BB d,Bal-Core d|ROLL-R d,BDX Explorer d,R2 g|R4 d,B2 Super g,Gunrunner g|NAV-EX r,Strike-Orb g,AMP Walker g|VECT-Arm r,R2 d,B2 Super d|Bal-Core r,Strike-Orb d,B2 Heavy d|ORB-Walker r,R2 r,BB9 b|B2 Super r,Mecha-Droid b,Proto-Roller b|B2 Heavy r,B2-RP b,R7 g|Strike-Orb r,BB9 g,Proto-Roller g|AMP Walker r,Mecha-Droid g,B2-RP d|Opti-Pod r,Mono-WLKR g,R7 d|Util-Tec r,BB9 d,Proto-Roller d|Mecha-Droid d,R7 r,B2-RP r|Mono-WLKR r,Opti-STRK r,Cyclo-Grav r|LO s,R6 s,Haul-R s|SEN-TRI s,Strike-Orb s,Proto-Roller s|BB9 s,Cyclo-Grav s,B2-RP s|Opti-STRK s,B2-RP s,Snow Mouse b|Mono-WLKR s,TRI-TEK g,RIC-1200 b|KX g,DRFT-R d,IG r|LEP d,Loadlifter r,MO-TRAK s|Mecha-Droid x,Snow Mouse r,TRI-TEK s|Cyclo-Grav x,R7 x,RIC s|Opti-STRK x,KX s,DRFT-R x|B2 Super t,B2-RP s,Loadlifter s|Gunrunner x,B1 Heavy x,LEP s|R2 t,Opti-STRK x,KX x|R6 t,LO t,RIC x|R7 t,DRFT-R t,CYCLENS t|B1 Security k,R4 k,R9 k|LO k,TRAK-R k,ORB-Walker k|B2 Super k,B2 Heavy k,B2-RP k|Mecha-Droid k,Cyclo-Grav k,KX k|RIC k,Loadlifter k,LEP k",
    "Mouse b,Pit b,Gonk b|R3 b,2BB b,Senate Hovercam b|R8 b,R5 b,R4 b|B1 Battle g,R9 g,B1 Security g|R3 g,2BB g,Senate Hovercam g|R5 d,R4 d,BDX Explorer d|R8 d,B1 Battle d,R9 d|R3 r,2BB r,B1 Security r|R5 r,R4 r,BDX Explorer r|Senate Hovercam r,Groundmech b,TRAK-R b|B2 Heavy b,B2 Super b,Util-Tec b|Bal-Core r,Groundmech g,TRAK-R g|B2 Super r,Mecha-Droid b,Proto-Roller b|B2 Heavy r,B2-RP b,R7 g|Strike-Orb r,BB9 g,Proto-Roller g|AMP Walker r,Mecha-Droid g,B2-RP d|Opti-Pod r,Mono-WLKR g,R7 d|Util-Tec r,BB9 d,Proto-Roller d|Mecha-Droid d,R7 r,B2-RP r|Mono-WLKR r,Opti-STRK r,Cyclo-Grav r|B2 Super s,Opti-Pod s,R2 s|Gunrunner s,LNG-Shot s,B2-RP s|Mono-WLKR s,Cyclo-Grav s,Mecha-Droid s|BB9 s,B2-RP s,RIC b|Proto-Roller s,Loadlifter b,MO-TRAK g|LEP g,TRI-TEK d,Snow Mouse r|RIC-1200 d,IG r,DRFT-R s|BB9 x,RIC r,MO-TRAK s|Mecha-Droid x,Opti-STRK x,IG s|R7 x,LEP s,DRFT-R x|B2 Heavy t,Mecha-Droid s,RIC-1200 s|Groundmech x,BB x,MO-TRAK s|TRAK-R t,Mono-WLKR x,Loadlifter x|ORB-Walker t,B2 Super t,LEP x|Proto-Roller t,KX t,RIC t|ARG k,Senate Hovercam k,BU-4D k|Strike-Orb k,AMP Walker k,Util-Tec k|Haul-R k,LNG-Shot k,Opti-STRK k|Mono-WLKR k,BB9 k,RIC-1200 k|MO-TRAK k,TRI-TEK k,IG k",
    "ID10 b,Pit b,DRK-1 Probe b|R3 b,2BB b,Senate Hovercam b|R5 g,R8 g,R4 b|B1 Battle g,R9 g,B1 Security g|R3 g,2BB g,Senate Hovercam g|R5 d,R4 d,BDX Explorer d|R8 d,B1 Battle d,R9 d|R3 r,B1 Security r,2BB r|R5 r,R4 r,BDX Explorer r|Senate Hovercam r,Groundmech b,TRAK-R b|B2 Heavy b,B2 Super b,Util-Tec b|Bal-Core r,Groundmech g,TRAK-R g|B2 Super r,Mecha-Droid b,Proto-Roller b|Bal-Core d,Groundmech d,TRAK-R r|B2 Heavy d,B2 Super r,B2-RP b|Util-Tec r,BB9 b,R7 g|Opti-STRK b,Cyclo-Grav g,Mecha-Droid g|B2-RP g,BB9 g,R7 d|Mecha-Droid d,R7 r,B2-RP r|Mono-WLKR r,Opti-STRK r,Cyclo-Grav r|AMP Walker s,Groundmech s,Haul-R s|Gunrunner s,Strike-Orb s,B2 Super s|Mono-WLKR s,Cyclo-Grav s,B2-RP s|Mecha-Droid s,Proto-Roller s,MO-TRAK b|Opti-STRK s,TRI-TEK b,DRFT-R g|CYCLENS g,LEP d,MO-TRAK r|RIC-1200 d,Snow Mouse r,Loadlifter s|Opti-STRK x,IG r,KX s|BB9 x,R7 x,TRI-TEK s|Mono-WLKR x,CYCLENS s,IG x|TRAK-R t,Cyclo-Grav s,TRI-TEK s|R2 x,R6 x,IG s|B2 Heavy t,BB9 x,RIC-1200 x|Strike-Orb t,AMP Walker t,MO-TRAK x|B2-RP t,Loadlifter t,LEP t|NAV-EX k,Bal-Core k,VECT-Arm k|SEN-TRI k,Opti-Pod k,Gunrunner k|B1 Heavy k,Groundmech k,R7 k|Proto-Roller k,B2-RP k,Snow Mouse k|DRFT-R k,CYCLENS k,KX k",
    "ID10 b,Mouse b,Gonk b|Imperial Probe g,2BB b,ROLL-R b|R4 b,VECT-Arm b,BDX Explorer g|B1 Battle g,R9 g,B1 Security g|R3 g,Bal-Core g,R4 g|2BB d,BDX Explorer d,Gunrunner b|R5 d,ROLL-R d,R2 g|R8 d,B1 Battle d,B2 Super g|NAV-EX r,Strike-Orb g,AMP Walker g|Imperial Probe s,Groundmech b,TRAK-R b|B2 Heavy b,B2 Super b,Util-Tec b|Bal-Core r,Groundmech g,TRAK-R g|B2 Super g,B2 Heavy g,R2 d|Groundmech d,TRAK-R d,Util-Tec r|B2 Heavy d,B2 Super d,B2-RP b|BB9 b,R7 g,Proto-Roller g|Opti-STRK b,Cyclo-Grav g,Mecha-Droid g|BB9 g,B2-RP g,R7 d|Opti-STRK d,Cyclo-Grav d,Mecha-Droid r|B2-RP r,BB9 r,R7 r|LO s,Strike-Orb s,Haul-R s|SEN-TRI s,R6 s,Gunrunner s|BB9 s,Cyclo-Grav s,B2-RP s|Mono-WLKR s,Opti-STRK s,MO-TRAK b|Mecha-Droid s,RIC b,TRI-TEK g|CYCLENS g,LEP d,Snow Mouse r|RIC-1200 d,IG r,Loadlifter s|BB9 x,RIC r,MO-TRAK s|Mecha-Droid x,Opti-STRK x,IG s|R7 x,LEP s,CYCLENS x|AMP Walker t,Opti-STRK s,Snow Mouse s|LO x,TRAK-R x,DRFT-R s|Util-Tec t,R7 x,TRI-TEK x|Haul-R t,LNG-Shot t,IG x|Mecha-Droid t,RIC-1200 t,MO-TRAK t|Mouse k,ROLL-R k,HOV-R k|BB k,R2 k,R6 k|LO k,TRAK-R k,Mecha-Droid k|Cyclo-Grav k,Opti-STRK k,RIC k|Loadlifter k,LEP k,RIC-1200 k",
    "Gonk b,Mouse b,ID10 b|Pit b,EG-58 b,2BB b|R3 b,R4 b,ROLL-R b|EG-58 g,R9 g,B1 Security g|Gonk g,R5 g,VECT-Arm g|EGL b,EG-58 d,2BB d|KT b,R4 d,R9 d|MPH b,B1 Security d,NAV-EX d|Gonk d,EGL g,Strike-Orb g|Groundmech b,TRAK-R b,AMP Walker b|B2 Super b,Haul-R b,R2 b|Gonk r,Groundmech g,TRAK-R g|KT g,MPH g,Proto-Roller b|EGL d,B2 Super g,B2-RP b|JO9-4MN b,KT d,MPH d|PLNK b,BB9 b,R7 g|EGL r,JO9-4MN g,Mecha-Droid b|KT r,MPH r,PLNK g|JO9-4MN d,Proto-Roller g,R7 d|PLNK r,Mecha-Droid r,B2-RP r|Gonk s,EGL s,Haul-R s|KT s,Groundmech s,R2 s|MPH s,JO9-4MN s,BB9 s|PLNK s,Proto-Roller s,ECG b|R7 s,IG b,Loadlifter b|MO-TRAK g,ECG g,DRFT-R d|CYCLENS r,Snow Mouse r,TRI-TEK s|EGL x,JO9-4MN x,BB9 x|KT x,PLNK x,IG s|MPH x,B2-RP x,DRFT-R x|EGL t,EG-58 s,Snow Mouse s|ECG x,TRAK-R x,CYCLENS s|KT t,Cyclo-Grav x,TRI-TEK x|MPH t,Haul-R t,IG x|JO9-4MN t,PLNK t,ECG t|Gonk k,EG-58 k,ROLL-R k|EGL k,R2 k,B2 Super k|KT k,MPH k,Snow Mouse k|JO9-4MN k,Cyclo-Grav k,TRI-TEK k|PLNK k,ECG k,DRFT-R k",
]
REBIRTH_CREDITS = ["10K", "150K", "975K", "2.95M", "5.35M", "9.85M", "14.5M", "36M", "89M", "220M", "550M", "1.36B", "3.40B", "8.45B", "21B", "52B", "130B", "325B", "810B", "2T", "3T", "4.5T", "6T", "9T", "13.5T", "21T", "32T", "45T", "68T", "100T", "150T", "230T", "345T", "520T", "778T", "1.19Qa", "2.5Qa", "4.5Qa", "8Qa", "15Qa"]
TIER_LETTERS = {"b": 0, "g": 1, "d": 2, "r": 3, "s": 4, "x": 5, "t": 6, "k": 7}
TIER_NAMES = ["Basic", "Gold", "Diamond", "Rainbow", "Beskar", "Galactic", "Stellar", "Kyber", "Scourge"]
_rb = {"info": None, "t": 0.0, "ver": None, "player": None, "error": ""}


def _inv_tier(key):
    m = re.match(r"\d+", str(key))
    return int(m.group()) if m else -1


def next_rebirth(fields):
    """What the next rebirth needs, from a website player document."""
    cycle = int(fields.get("cycle") or 1)
    cycle = min(max(cycle, 1), len(REBIRTH_CYCLES))
    done = set(int(x) for x in ((fields.get("done") or {}).get(str(cycle)) or []))
    level = next((n for n in range(1, 41) if n not in done), None)
    out = {"cycle": cycle, "level": level, "name": fields.get("name", "")}
    if level is None:
        return out
    inv = fields.get("inv") or {}
    needs = []
    for part in REBIRTH_CYCLES[cycle - 1].split("|")[level - 1].split(","):
        name, letter = part.rsplit(" ", 1)
        tier = TIER_LETTERS[letter]
        have = sum(c for k, c in (inv.get(name) or {}).items() if _inv_tier(k) >= tier and c > 0)
        best = max((_inv_tier(k) for k, c in (inv.get(name) or {}).items() if c > 0), default=-1)
        needs.append({"name": name, "tier": tier, "ok": have > 0, "best": best})
    out.update(credits=REBIRTH_CREDITS[level - 1], needs=needs)
    return out


def rebirth_watch():
    """Keep the next-rebirth info fresh for the overlay (every minute, and right away
    after F9/F10, Page Up/Down or a change in the app)."""
    while not state["quit"]:
        time.sleep(1.5)
        if not cfg.get("show_rebirth", True) or state["enabled"]:
            continue
        if not accounts_enabled() or not _account.get("refresh") or _account.get("offline"):
            continue
        ver, pl = state.get("data_version", 0), cfg.get("inventory_player")
        if time.time() - _rb["t"] < 60 and ver == _rb["ver"] and pl == _rb["player"]:
            continue
        _rb.update(t=time.time(), ver=ver, player=pl)
        try:
            player = current_player()
            if not player:
                _rb["info"] = None
                continue
            doc = _http(_players_url(player["id"]), token=id_token())
            _rb["info"] = next_rebirth({k: _fs_value(v) for k, v in doc.get("fields", {}).items()})
            _rb["error"] = ""
        except Exception as e:
            _rb["error"] = str(e)
            print("Couldn't load the next rebirth:", e)


def rebirth_lines():
    """[(text, colour)] for the overlay, or [] if there's nothing to show."""
    info = _rb["info"]
    if not info or not cfg.get("show_rebirth", True):
        return []
    who = f" \u00b7 {info['name']}" if info.get("name") and len(_players["list"]) > 1 else ""
    if info["level"] is None:
        return [(f"All 40 rebirths done (cycle {info['cycle']}){who} \u2013 time to Super Rebirth!", GREEN)]
    lines = [(f"NEXT: REBIRTH {info['level']} \u00b7 {info['credits']} credits{who}", FG)]
    for n in info["needs"]:
        if n["ok"]:
            lines.append((f"\u2714 {n['name']} \u2013 {TIER_NAMES[n['tier']]}", GREEN))
        else:
            have = f" (you have {TIER_NAMES[n['best']]})" if n["best"] >= 0 else ""
            lines.append((f"\u2718 {n['name']} \u2013 {TIER_NAMES[n['tier']]}{have}", RED))
    return lines


def overlay_pos(view, mon):
    """Screen position of the overlay ('full') or the idle badge ('mini'): top-left,
    just under the player name / ID line (about 3% down the screen)."""
    kx, ky = ("overlay_x", "overlay_y") if view == "full" else ("mini_x", "mini_y")
    y = cfg.get(ky)
    if y is None:
        y = max(24, int(mon["height"] * 0.032))
    return mon["left"] + int(cfg.get(kx) or 0), mon["top"] + int(y)


def overlay_hints():
    """Only the hotkeys that matter right now."""
    m = hk_label("menu")
    if state["enabled"]:
        if state["mode"] == "fishing":
            return f"{m} stop \u00b7 {hk_label('snapshot')} save debug pictures"
        return f"{m} stop"
    return (f"{m} menu \u00b7 {hk_label('reb_up')}/{hk_label('reb_down')} rebirth +/\u2212 \u00b7 "
            f"{hk_label('inv_add')}/{hk_label('inv_remove')} add/remove droid\n"
            f"{hk_label('area')} scan area \u00b7 {hk_label('quit')} quit")


def run_windows():
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.88)
    root.configure(bg=BG)
    root.geometry("+{}+{}".format(*overlay_pos("full", screen())))

    frame = tk.Frame(root, bg=BG, padx=14, pady=10,
                     highlightthickness=1, highlightbackground="#2b3038")
    frame.pack()

    def label(font, color=FG):
        lbl = tk.Label(frame, bg=BG, fg=color, font=font, anchor="w", justify="left")
        lbl.pack(fill="x")
        return lbl

    title = label(("Segoe UI", 12, "bold"))
    reading = label(("Segoe UI", 11))
    pressed = label(("Segoe UI", 11))
    stats = label(("Segoe UI", 10), DIM)
    picker = tk.Label(frame, bg=BG, fg=FG, font=("Consolas", 10), anchor="w", justify="left")
    message = label(("Segoe UI", 9), DIM)
    tk.Frame(frame, bg="#2b3038", height=1).pack(fill="x", pady=(6, 4))
    hints = label(("Segoe UI", 9), DIM)

    mini = tk.Frame(root, bg=BG, padx=8, pady=3,
                    highlightthickness=1, highlightbackground="#2b3038")
    mini_row = tk.Frame(mini, bg=BG)
    mini_row.pack(anchor="w")
    mini_dot = tk.Label(mini_row, bg=BG, fg=AMBER, font=("Segoe UI", 9, "bold"), text="\u25cf")
    mini_dot.pack(side="left")
    mini_text = tk.Label(mini_row, bg=BG, fg=DIM, font=("Segoe UI", 9),
                         text=f"Droid Tycoon Helper v{VERSION} \u00b7 {hk_label('menu')} = menu")
    mini_text.pack(side="left", padx=(4, 0))

    def rb_box(parent):
        """Next rebirth: a heading and one line per droid (coloured)."""
        box = tk.Frame(parent, bg=BG)
        labels = [tk.Label(box, bg=BG, fg=FG, anchor="w", justify="left",
                           font=("Segoe UI", 10, "bold" if i == 0 else "normal")) for i in range(6)]
        return box, labels

    mini_rb, mini_rb_labels = rb_box(mini)
    full_rb, full_rb_labels = rb_box(frame)
    rb_shown = {"mini": "", "full": ""}

    def show_rb(where, box, labels, lines, after=None):
        key = repr(lines)
        if key == rb_shown[where]:
            return
        rb_shown[where] = key
        if not lines:
            box.pack_forget()
            return
        for i, lab in enumerate(labels):
            if i < len(lines):
                lab.config(text=lines[i][0], fg=lines[i][1])
                lab.pack(anchor="w", fill="x")
            else:
                lab.pack_forget()
        if after is not None:
            box.pack(after=after, anchor="w", fill="x", pady=(4, 0))
        else:
            box.pack(anchor="w", fill="x", pady=(4, 0))

    make_click_through(root)
    shown = ["full"]
    placed = [""]


    def refresh():
        if state["quit"]:
            root.destroy()
            return
        if state["select_area"]:
            state["select_area"] = False
            select_area(root)
        timeout = 10 if state["menu_stage"] in ("mode", "clicker", "droids") else 60
        if state["menu_open"] and time.time() - state["menu_time"] > timeout:
            state["menu_open"] = False            # menu times out if nothing is picked
        mode = state["mode"].upper()
        picking = state["menu_open"] and state["menu_stage"] in ("traits", "droids", "clicker")
        if picking and not picker.winfo_ismapped():
            picker.pack(after=title, fill="x", pady=(4, 2))
        elif not picking and picker.winfo_ismapped():
            picker.pack_forget()
        if picking and state["menu_stage"] in ("droids", "clicker"):
            stage = state["menu_stage"]
            choices = DROID_CHOICES if stage == "droids" else CLICKER_CHOICES
            title.config(text="TRAITS \u2013 WHICH DROID?" if stage == "droids"
                         else "AUTO CLICKER \u2013 WHICH ONE?", fg=FG)
            picker.config(text="\n".join(("\u25b6 " if i == state["choice_index"] else "  ") + label
                                         for i, (_, label) in enumerate(choices)))
            reading.config(text="")
            pressed.config(text="")
            stats.config(text="Iconic droids are never rolled." if stage == "droids" else "")
            hints.config(text=f"Up/Down choose \u00b7 {hk_label('menu')} next")
        elif picking:
            title.config(text="TRAITS \u2013 STOP ON WHICH?", fg=FG)
            badge = {"off": "  \u00b7  ", "any": " ANY ", "diamond": "DIAMOND", "rainbow": "RAINBOW"}
            lines = []
            for i, n in enumerate(TRAIT_NAMES):
                mark = "\u25b6" if i == state["pick_index"] else " "
                lines.append(f"{mark} {n:<21}[{badge[cfg['trait_wanted'].get(n, 'off')]:^7}]")
            mark = "\u25b6" if state["pick_index"] == len(TRAIT_NAMES) else " "
            limit = cfg["max_cost"] if cfg["max_cost"] != "off" else "no limit"
            lines.append("")
            lines.append(f"{mark} {'Stop if cost over':<21}[{limit:^7}]")
            picker.config(text="\n".join(lines))
            reading.config(text="")
            pressed.config(text="")
            stats.config(text="")
            hints.config(text=f"Up/Down move \u00b7 Left/Right change \u00b7 {hk_label('menu')} start")
        elif state["menu_open"]:
            title.config(text="DROID TYCOON HELPER \u2013 CHOOSE MODE", fg=FG)
            rows = [("\u25b6 " if state["menu_choice"] == i else "    ") + n
                    for i, n in enumerate(MODE_NAMES)]
            reading.config(text=rows[0])
            pressed.config(text=rows[1])
            stats.config(text=rows[2], fg=FG, font=("Segoe UI", 11))
            hints.config(text=f"Up/Down choose \u00b7 {hk_label('menu')} start")
        else:
            if state["error"]:
                title.config(text="DROID TYCOON HELPER \u2013 PROBLEM", fg=RED)
            elif state["enabled"]:
                title.config(text=f"{mode} \u2013 ACTIVE", fg=GREEN)
            else:
                title.config(text="DROID TYCOON HELPER \u2013 STOPPED", fg=AMBER)
            stats.config(fg=DIM, font=("Segoe UI", 10))
            if state["mode"] == "traits":
                droid = state["trait_side"].title() + (
                    f" ({state.get('trait_droid')})" if state.get("trait_droid") else "")
                reading.config(text=f"Trait: {state['trait'] or DASH}")
                pressed.config(text=f"{droid} droid \u00b7 roll {state['rolls']}")
                limit = cfg["max_cost"] if cfg["max_cost"] != "off" else "none"
                stats.config(text=f"Rolling for: {wanted_summary()}\nCost limit: {limit}",
                             wraplength=440)
            elif state["mode"] == "clicker":
                where = (f"only while {cfg['clicker_window']} is in front"
                         if cfg["clicker_window"] else "anywhere")
                active = fmt_duration(state.get("active_secs", 0))
                if cfg["clicker_kind"] == "potion":
                    reading.config(text=f"Potion Grab \u00b7 Time active: {active}")
                    pressed.config(text=f"Every {cfg['potion_interval']}s \u00b7 "
                                        f"{state['status'] or DASH}")
                    what = f"Presses F {where}"
                else:
                    reading.config(text=f"Scrap Hit \u00b7 Time active: {active}")
                    pressed.config(text=f"Speed: {cfg['clicker_cps']}/s \u00b7 "
                                        f"{state['status'] or DASH}")
                    what = f"Clicks where your mouse is, {where}"
                stats.config(text=earnings_text() + "\n" + what, wraplength=440)
            else:
                reading.config(text=f"Reading: {state['reading'] or DASH}")
                pressed.config(text=f"Pressed: {state['pressed'] or DASH}")
                area = "custom" if cfg["search_area"] else "auto"
                stats.config(text=fishing_stats_text() + "\n"
                                  f"Scan {state['scan_ms']} ms \u00b7 "
                                  f"Delay {int(cfg['press_delay'] * 1000)} ms \u00b7 "
                                  f"Inputs {state['inputs']} \u00b7 Area {area}", wraplength=440)
            hints.config(text=overlay_hints())
        message.config(text=state["error"] or state["message"],
                       fg=RED if state["error"] else DIM)
        # F9/F10 droid box - shown inside the overlay (in place of the next-rebirth lines)
        want_inv = bool(state["inv_lines"]) and time.time() < state["inv_until"]
        full = cfg["overlay"] and (
            not cfg["hide_when_paused"] or state["enabled"] or state["error"]
            or state["menu_open"] or time.time() < state["show_until"])
        rb_idle = cfg["overlay"] and not state["enabled"] and bool(rebirth_lines())
        view = "full" if full else ("mini" if (cfg["mini_badge"] and not cfg["discreet"]) or rb_idle
                                    or want_inv else "none")
        if state["capture_hide"]:
            view = "none"
        if view != shown[0]:
            frame.pack_forget()
            mini.pack_forget()
            if view == "none":
                root.withdraw()
            else:
                if view == "full":
                    frame.pack()
                else:
                    mini.pack()
                root.deiconify()
                make_click_through(root)
            shown[0] = view
        if view != "none":                       # keep the boxes on the watched screen
            mon = screen()
            pos = "+{}+{}".format(*overlay_pos(view, mon))
            if pos != placed[0]:
                root.geometry(pos)
                placed[0] = pos
        if want_inv:
            col = state["inv_color"] or FG
            idle_lines = [(t, col if i == 0 or state["inv_color"] else (DIM if t.startswith("Wrong?") else FG))
                          for i, t in enumerate(state["inv_lines"][:6])]
        else:
            idle_lines = [] if (state["enabled"] or state["menu_open"]) else rebirth_lines()
        show_rb("mini", mini_rb, mini_rb_labels, idle_lines)
        show_rb("full", full_rb, full_rb_labels, idle_lines, after=message)
        if view == "mini":
            mini_dot.config(fg=GREEN if state["enabled"] else (RED if state["error"] else AMBER))
        if view != "none":
            root.attributes("-topmost", True)  # stay above the game
        root.after(100, refresh)

    refresh()
    root.mainloop()


# ------------------------------------------------------------------ updates ---

def version_tuple(text):
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums[:3]) or (0,)


def tray_message(msg):
    """Show a Windows notification from the tray icon (if there is one)."""
    state["last_tray"] = msg
    icon = state.get("tray")
    if icon is not None:
        try:
            icon.notify(msg, "Droid Tycoon Helper")
        except Exception:
            pass


def check_for_update(manual=False):
    """If GitHub has a newer release, download it, swap it in and restart."""
    import urllib.request
    if "YOUR-GITHUB-NAME" in UPDATE_REPO:
        if manual:
            notify("Updates aren't set up yet (UPDATE_REPO in key_helper.py)")
            tray_message("Updates aren't set up yet.")
        return
    try:
        notify(f"v{VERSION} - checking for updates...")
        req = urllib.request.Request(
            f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest",
            headers={"User-Agent": "SW-Key-Helper", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=6) as r:
            release = json.load(r)
        latest = release.get("tag_name", "")
        if version_tuple(latest) <= version_tuple(VERSION):
            notify(f"v{VERSION} - up to date")
            if manual:
                tray_message(f"You're on the latest version (v{VERSION}).")
            return
        if not getattr(sys, "frozen", False):
            notify(f"Update {latest} is available - get the new Droid Tycoon Helper.exe")
            return
        asset = next((a for a in release.get("assets", [])
                      if a["name"].lower().endswith(".exe")), None)
        if not asset:
            return
        notify(f"Downloading update {latest}...", 120)
        exe = sys.executable
        new_path = exe + ".new"
        size = asset.get("size")
        err = None
        for attempt in range(3):                  # big file - retry if the connection drops
            try:
                req = urllib.request.Request(asset["browser_download_url"],
                                             headers={"User-Agent": "SW-Key-Helper"})
                with urllib.request.urlopen(req, timeout=120) as r, open(new_path, "wb") as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                if not size or os.path.getsize(new_path) == size:
                    err = None
                    break
                err = f"download incomplete ({os.path.getsize(new_path)} of {size} bytes)"
            except Exception as e:
                err = f"download failed: {e}"
            print("Update attempt", attempt + 1, "-", err)
            time.sleep(2)
        if err:
            raise RuntimeError(err)
        install_update(exe, new_path, latest)
    except Exception as e:
        print("Update failed:", repr(e))
        notify(f"Update failed: {e}"[:120], 15)
        if manual:
            tray_message(f"Update failed: {e}"[:200])


def install_update(exe, new_path, latest):
    """Swap the new .exe in and restart. Windows can't overwrite a running .exe, but it
    can usually rename it; if that's blocked (antivirus, locks), a small helper script
    finishes the swap right after this copy closes."""
    old_path = exe + ".old"
    try:
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:                       # still locked - just park it elsewhere
                old_path = f"{exe}.old{int(time.time())}"
        os.rename(exe, old_path)
        try:
            os.rename(new_path, exe)
        except OSError:
            os.rename(old_path, exe)              # put the working copy back
            raise
        state["restart_path"] = exe
    except OSError as e:
        print("Direct swap failed, using helper script:", e)
        bat = os.path.join(os.environ.get("TEMP", BASE_DIR), "sw_key_helper_update.bat")
        with open(bat, "w") as f:
            f.write(f"""@echo off
rem Droid Tycoon Helper updater - waits for the app to close, swaps in the new version
:wait
tasklist /FI "PID eq {os.getpid()}" | find "{os.getpid()}" >nul && (timeout /t 1 >nul & goto wait)
set n=0
:swap
move /y "{new_path}" "{exe}" >nul 2>&1 && goto start
set /a n+=1
if %n% lss 20 (timeout /t 1 >nul & goto swap)
goto end
:start
start "" "{exe}"
:end
del "%~f0"
""")
        subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000000)   # no window
        state["restart_path"] = None
    notify(f"Updated to {latest} - restarting...", 10)
    tray_message(f"Updated to {latest} - restarting...")
    time.sleep(1.5)
    state["quit"] = True


def cleanup_old_versions():
    """Delete copies left behind by earlier updates."""
    if not getattr(sys, "frozen", False):
        return
    folder, name = os.path.split(sys.executable)
    for _ in range(10):
        left = [f for f in os.listdir(folder) if f.startswith(name + ".old")]
        for f in left:
            try:
                os.remove(os.path.join(folder, f))
            except OSError:
                pass
        if not [f for f in os.listdir(folder) if f.startswith(name + ".old")]:
            return
        time.sleep(0.5)


# --------------------------------------------------------------- tray icon ---

def icon_path():
    """The app icon file: inside the .exe, next to it, or downloaded once from GitHub."""
    for folder in (getattr(sys, "_MEIPASS", BASE_DIR), BASE_DIR):
        path = os.path.join(folder, "Droid Tycoon Helper.ico")
        if os.path.exists(path) and os.path.getsize(path) > 100:
            return path
    path = os.path.join(BASE_DIR, "Droid Tycoon Helper.ico")
    try:
        import urllib.request
        url = f"https://raw.githubusercontent.com/{UPDATE_REPO}/main/Droid%20Tycoon%20Helper.ico"
        with urllib.request.urlopen(urllib.request.Request(
                url, headers={"User-Agent": "SW-Key-Helper"}), timeout=10) as r:
            data = r.read()
        with open(path, "wb") as f:
            f.write(data)
        return path
    except Exception as e:
        print("No icon file:", e)
        return None


def icon_png(size=256):
    """The icon as PNG bytes (largest picture in the .ico)."""
    import io
    from PIL import Image
    path = icon_path()
    if not path:
        return None
    img = Image.open(path)
    try:
        sizes = sorted(img.ico.sizes())
        pick = min((s for s in sizes if s[0] >= size), default=sizes[-1])
        img = img.ico.getimage(pick)
    except Exception:
        img.load()
    buf = io.BytesIO()
    img.convert("RGBA").save(buf, "PNG")
    return buf.getvalue()


def load_icon_image():
    from PIL import Image, ImageDraw
    path = icon_path()
    if path:
        try:
            import io
            return Image.open(io.BytesIO(icon_png(64)))
        except Exception as e:
            print("Icon didn't load:", e)
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))          # simple fallback
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 4, 60, 60), radius=12, fill=(52, 60, 78, 255))
    d.rounded_rectangle((14, 20, 50, 48), radius=8, fill=(242, 140, 40, 255))
    d.ellipse((20, 28, 30, 38), fill=(80, 235, 215, 255))
    d.ellipse((34, 28, 44, 38), fill=(80, 235, 215, 255))
    return img


def start_tray():
    """Icon in the notification area (by the clock) with a right-click menu."""
    try:
        import pystray
    except ImportError:
        print("pystray not installed - no tray icon")
        return

    def toggle_discreet(icon, item):
        cfg["discreet"] = not cfg["discreet"]
        save_config()
        if not cfg["discreet"]:
            notify("Discreet mode off", 2)
        tray_message("Discreet mode on - the badge only shows while something is running"
                     if cfg["discreet"] else "Discreet mode off")

    def do_update(icon, item):
        threading.Thread(target=check_for_update, args=(True,), daemon=True).start()

    def open_window(icon, item):
        threading.Thread(target=open_app_window, daemon=True).start()

    def open_folder(icon, item):
        os.startfile(BASE_DIR)

    def do_quit(icon, item):
        state["quit"] = True
        icon.stop()

    def do_sign_out(icon, item):
        sign_out()
        state["quit"] = True
        icon.stop()

    account_items = []
    if accounts_enabled() and _account["username"]:
        try:
            if not _account.get("offline"):
                list_players()
        except Exception as e:
            print("Couldn't load website players:", e)

        def pick(pid):
            def handler(icon, item):
                cfg["inventory_player"] = pid
                save_config()
                name = next((p["name"] for p in _players["list"] if p["id"] == pid), pid)
                notify(f"{hk_label('inv_add')}/{hk_label('inv_remove')} now update {name}'s inventory", 4)
            return handler

        def is_current(pid):
            return lambda item: (current_player() or {}).get("id") == pid

        player_items = [pystray.MenuItem(p["name"], pick(p["id"]), checked=is_current(p["id"]),
                                         radio=True) for p in _players["list"]]
        account_items = [
            pystray.MenuItem(f"Signed in as {_account['username']}", None, enabled=False),
        ]
        if player_items:
            account_items.append(pystray.MenuItem(f"Inventory player ({hk_label('inv_add')}/{hk_label('inv_remove')})",
                                                  pystray.Menu(*player_items)))
        account_items.append(pystray.MenuItem("Sign out", do_sign_out))
    def pick_screen(value):
        def handler(icon, item):
            cfg["monitor"] = value
            save_config()
            _screen_cache["time"] = 0
            notify("Watching " + ("the screen Fortnite is on" if value == "auto"
                                  else f"screen {value}"), 4)
        return handler

    def screen_is(value):
        return lambda item: str(cfg.get("monitor", "auto")) == value

    screen_items = [pystray.MenuItem("Auto (the screen Fortnite is on)", pick_screen("auto"),
                                     checked=screen_is("auto"), radio=True)]
    for i, m in enumerate(MONITORS, 1):
        label = f"Screen {i} \u2013 {m['width']}\u00d7{m['height']}" + (" (main)" if i == 1 else "")
        screen_items.append(pystray.MenuItem(label, pick_screen(str(i)),
                                             checked=screen_is(str(i)), radio=True))
    menu = pystray.Menu(
        pystray.MenuItem(f"Droid Tycoon Helper v{VERSION}", None, enabled=False),
        pystray.MenuItem("Open app window", open_window),
        pystray.MenuItem("Screen", pystray.Menu(*screen_items)),
        *account_items,
        pystray.MenuItem("Discreet mode (hide badge when idle)", toggle_discreet,
                         checked=lambda item: cfg["discreet"], default=True),
        pystray.MenuItem("Check for updates", do_update),
        pystray.MenuItem("Open settings folder", open_folder),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", do_quit),
    )
    icon = pystray.Icon("Droid Tycoon Helper", load_icon_image(), f"Droid Tycoon Helper v{VERSION}", menu)
    state["tray"] = icon
    try:
        icon.run()
    except Exception as e:
        print("Tray icon failed:", e)


# -------------------------------------------------------------- app window ---
# A window with your website on top and the app's controls underneath. The page is
# served by this program on 127.0.0.1 only; a random token in the address keeps
# other web pages from using it.

PANEL_FILE = "app_window.html"
_panel = {"port": None, "token": None, "proc": None}


_panel_cache = {"html": None}


def _panel_html():
    """The app window page: packed inside the .exe, next to key_helper.py, or (if
    the build didn't pack it) downloaded once from GitHub for this exact version."""
    if _panel_cache["html"]:
        return _panel_cache["html"]
    cached = os.path.join(BASE_DIR, f"app_window_v{VERSION}.html")
    places = [os.path.join(getattr(sys, "_MEIPASS", BASE_DIR), PANEL_FILE), cached]
    if not getattr(sys, "frozen", False):                 # running key_helper.py itself
        places.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), PANEL_FILE))
    for path in places:
        if os.path.exists(path) and os.path.getsize(path) > 1000:
            with open(path, encoding="utf-8") as f:
                _panel_cache["html"] = f.read()
            return _panel_cache["html"]
    import urllib.request
    for ref in (f"v{VERSION}", "main"):
        try:
            url = f"https://raw.githubusercontent.com/{UPDATE_REPO}/{ref}/{PANEL_FILE}"
            req = urllib.request.Request(url, headers={"User-Agent": "SW-Key-Helper"})
            with urllib.request.urlopen(req, timeout=15) as r:
                html = r.read().decode("utf-8")
            if "Droid Tycoon Helper" not in html:
                continue
            with open(cached, "w", encoding="utf-8") as f:
                f.write(html)
            for old in os.listdir(BASE_DIR):              # drop copies for older versions
                if old.startswith("app_window_v") and old.endswith(".html") and \
                        old != os.path.basename(cached):
                    try:
                        os.remove(os.path.join(BASE_DIR, old))
                    except OSError:
                        pass
            print("Downloaded the app window page from GitHub", ref)
            _panel_cache["html"] = html
            return html
        except Exception as e:
            print("Couldn't download app_window.html from", ref, "-", e)
    return ("<body style='background:#10151d;color:#e6ebf2;font:16px sans-serif;padding:40px'>"
            "<h2>The app window page couldn't be loaded</h2><p>Make sure <b>app_window.html</b> "
            "is uploaded to your GitHub repo next to key_helper.py, and that this PC is online, "
            "then close this window and open it again from the tray icon.</p></body>")


def _fortnite_hwnd():
    if sys.platform != "win32":
        return None
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def each(hwnd, _):
        if u.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(hwnd, buf, 256)
            if "fortnite" in buf.value.lower():
                found.append(hwnd)
        return True

    u.EnumWindows(each, 0)
    return found[0] if found else None


def focus_fortnite():
    """Bring Fortnite to the front (so key presses and clicks go to the game).
    True if it was found."""
    hwnd = _fortnite_hwnd()
    if not hwnd:
        return False
    try:
        u = ctypes.windll.user32
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)                 # restore if minimised
        u.keybd_event(0x12, 0, 0, 0)              # tap Alt - lets Windows switch focus
        u.keybd_event(0x12, 0, 2, 0)
        u.SetForegroundWindow(hwnd)
    except Exception as e:
        print("Couldn't bring Fortnite to the front:", e)
    return True


def panel_start(mode):
    """Start button in the app window."""
    if state["enabled"]:
        return "Already running - stop it first"
    if mode not in MODES:
        return "Unknown mode"
    if mode == "traits" and not any(v != "off" for v in cfg["trait_wanted"].values()):
        return "Turn on at least one trait first"
    found = focus_fortnite()
    if not found and mode != "clicker" and sys.platform == "win32":
        return "Fortnite isn't open - start the game first"
    state["menu_open"] = False
    state["mode"] = mode
    save_config()

    def go():
        time.sleep(0.8)                           # let the game take focus first
        if not state["enabled"]:
            start_mode()
    threading.Thread(target=go, daemon=True).start()
    return ""


def _pending_lines():
    if state["inv_lines"] and time.time() < state["inv_until"]:
        return state["inv_lines"]
    return []


def panel_state():
    fr = state["fish_round"]
    e = state.get("earn")
    running = state["enabled"]
    earn = ""
    if e is not None and state["mode"] == "clicker":
        try:
            earn = earnings_text()
        except Exception:
            earn = ""
    try:
        odds = stats_summary()
    except Exception:
        odds = ""
    players = _players["list"]
    cur = None
    try:
        cur = (current_player() or {}).get("id") if players else None
    except Exception:
        pass
    return {
        "version": VERSION,
        "data_version": state.get("data_version", 0),
        "pictures": _pics["status"],
        "running": running, "mode": state["mode"], "menu_open": state["menu_open"],
        "error": state["error"], "message": state["message"],
        "message_age": round(time.time() - (state["show_until"] - 2.5), 1),
        "tray": state.get("last_tray", ""),
        "fishing": {
            "reading": state["reading"], "pressed": state["pressed"],
            "scan_ms": state["scan_ms"], "inputs": state["inputs"],
            "delay_ms": int(round(cfg["press_delay"] * 1000)),
            "area": "custom" if cfg["search_area"] else "auto",
            "require_prompt": cfg["require_prompt"],
            "stats": fishing_stats_text(),
            "round_active": fr["active"], "round_chips": format_amount(fr["chips"]) if fr["chips"] else "0",
        },
        "traits": {
            "side": state["trait_side"], "droid": state.get("trait_droid", ""),
            "rolls": state["rolls"], "trait": state["trait"], "status": state["status"],
            "cost": state["cost"], "spent": format_amount(state["spent"]) if state["spent"] else "",
            "wanted": {n: cfg["trait_wanted"].get(n, "off") for n in TRAIT_NAMES},
            "always_rainbow": sorted(ALWAYS_RAINBOW),
            "droids": cfg["trait_droids"], "max_cost": cfg["max_cost"], "cost_steps": COST_STEPS,
            "roll_wait": cfg["roll_wait"], "max_rolls": cfg["max_rolls"],
            "stop_if_unreadable": cfg["stop_if_unreadable"], "odds": odds,
            "odds_data": odds_data(),
        },
        "clicker": {
            "kind": cfg["clicker_kind"], "cps": cfg["clicker_cps"],
            "potion_interval": cfg["potion_interval"],
            "only_fortnite": bool(cfg["clicker_window"].strip()),
            "active": fmt_duration(state.get("active_secs", 0)), "earnings": earn,
            "status": state["status"] if state["mode"] == "clicker" else "",
        },
        "inventory": {
            "players": players, "player": cur, "kyber_default": cfg["kyber_default"],
            "pending": _pending_lines(),
        },
        "hotkeys": [{"id": a, "label": label, "key": hk(a), "pretty": hk_label(a)}
                    for a, label in HOTKEY_ACTIONS],
        "capture": state["hk_capture"], "capture_error": state["hk_error"],
        "settings": {
            "monitor": str(cfg.get("monitor", "auto")),
            "monitors": [{"id": str(i), "label": f"Screen {i} – {m['width']}×{m['height']}"
                          + (" (main)" if i == 1 else "")} for i, m in enumerate(MONITORS, 1)],
            "overlay": cfg["overlay"], "hide_when_paused": cfg["hide_when_paused"],
            "mini_badge": cfg["mini_badge"], "discreet": cfg["discreet"],
            "auto_update": cfg["auto_update"], "website_url": cfg["website_url"],
            "open_window_on_start": cfg["open_window_on_start"],
            "show_rebirth": cfg.get("show_rebirth", True),
            "panel_height": cfg["panel_height"],
        },
        "account": {"username": _account.get("username", ""),
                    "signed_in": bool(_account.get("refresh")),
                    "offline": bool(_account.get("offline"))},
    }


def _as_bool(v):
    return bool(v) if not isinstance(v, str) else v.lower() in ("1", "true", "yes", "on")


def _clamp_num(v, lo, hi, kind=float):
    return kind(min(hi, max(lo, kind(v))))


def panel_set(key, value):
    """Change one setting from the app window. Returns '' or a problem."""
    if key == "press_delay_ms":
        cfg["press_delay"] = round(_clamp_num(value, DELAY_MIN * 1000, DELAY_MAX * 1000) / 1000, 3)
    elif key in ("require_prompt", "stop_if_unreadable", "overlay", "hide_when_paused",
                 "mini_badge", "discreet", "auto_update", "open_window_on_start", "show_rebirth"):
        cfg[key] = _as_bool(value)
    elif key == "trait_wanted":
        if not isinstance(value, dict):
            return "bad value"
        for n, c in value.items():
            if n in TRAIT_NAMES and c in choices_for(n):
                cfg["trait_wanted"][n] = c
    elif key == "trait_droids":
        if value not in [c[0] for c in DROID_CHOICES]:
            return "bad value"
        cfg["trait_droids"] = value
    elif key == "max_cost":
        if value not in COST_STEPS:
            return "bad value"
        cfg["max_cost"] = value
    elif key == "roll_wait":
        cfg["roll_wait"] = round(_clamp_num(value, 3, 20), 1)
    elif key == "max_rolls":
        cfg["max_rolls"] = _clamp_num(value, 0, 1000000, int)
    elif key == "clicker_kind":
        if value not in [c[0] for c in CLICKER_CHOICES]:
            return "bad value"
        cfg["clicker_kind"] = value
    elif key == "clicker_cps":
        cfg["clicker_cps"] = _clamp_num(value, 1, 50, int)
    elif key == "potion_interval":
        cfg["potion_interval"] = _clamp_num(value, 2, 20, int)
    elif key == "only_fortnite":
        cfg["clicker_window"] = "Fortnite" if _as_bool(value) else ""
    elif key == "inventory_player":
        if value not in [p["id"] for p in _players["list"]]:
            try:
                list_players()                     # just added in the app window?
            except Exception:
                pass
        if value not in [p["id"] for p in _players["list"]]:
            return "unknown player"
        cfg["inventory_player"] = value
    elif key == "kyber_default":
        if value not in KYBER_NAMES:
            return "bad value"
        cfg["kyber_default"] = value
    elif key == "monitor":
        value = str(value)
        if value != "auto" and not (value.isdigit() and 1 <= int(value) <= len(MONITORS)):
            return "bad value"
        cfg["monitor"] = value
        _screen_cache["time"] = 0
    elif key == "website_url":
        value = str(value).strip()
        if value and not re.match(r"^https?://", value):
            value = "https://" + value
        if value and not re.match(r"^https?://[^\s/$.?#][^\s]*$", value):
            return "That doesn't look like a web address"
        cfg["website_url"] = value
    elif key == "panel_height":
        cfg["panel_height"] = _clamp_num(value, 60, 2000, int)
    elif key == "mode":
        if value not in MODES or state["enabled"]:
            return "bad value"
        state["mode"] = value
        return ""
    else:
        return "unknown setting"
    save_config()
    return ""


def panel_action(name, data):
    if name == "start":
        return panel_start(data.get("mode", state["mode"]))
    if name == "stop":
        if state["enabled"]:
            state["enabled"] = False
            notify(f"{state['mode'].title()} stopped")
        return ""
    if name == "select_area":
        if state["enabled"]:
            return "Stop first"
        if not focus_fortnite() and sys.platform == "win32":
            return "Fortnite isn't open"
        state["select_area"] = True
        return ""
    if name == "reset_area":
        cfg["search_area"] = None
        save_config()
        notify("Back to automatic scan area.")
        return ""
    if name == "capture_hotkey":
        if data.get("id") not in dict(HOTKEY_ACTIONS):
            return "unknown hotkey"
        state["hk_capture"], state["hk_error"] = data["id"], ""
        return ""
    if name == "cancel_capture":
        state["hk_capture"], state["hk_error"] = None, ""
        return ""
    if name == "reset_hotkeys":
        cfg["hotkeys"] = dict(DEFAULTS["hotkeys"])
        cfg["inv_add_key"], cfg["inv_remove_key"] = hk("inv_add"), hk("inv_remove")
        save_config()
        notify("Hotkeys reset to the defaults", 3)
        return ""
    if name == "check_update":
        threading.Thread(target=check_for_update, args=(True,), daemon=True).start()
        return ""
    if name == "refresh_players":
        try:
            list_players()
        except Exception as e:
            return f"Couldn't load players: {e}"
        return ""
    if name == "reload_pictures":
        threading.Thread(target=build_picture_index, args=(True,), daemon=True).start()
        return ""
    if name == "refresh_odds":
        fetch_shared()
        return ""
    if name == "open_folder":
        if sys.platform == "win32":
            os.startfile(BASE_DIR)
        return ""
    if name == "sign_out":
        sign_out()
        state["quit"] = True
        return ""
    if name == "quit":
        state["quit"] = True
        return ""
    return "unknown action"


# ---- website data in the app window (same Firestore documents as the website) ----

def _to_fs(v):
    """Python/JSON value -> Firestore REST value."""
    if v is None:
        return {"nullValue": None}
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, float):
        return {"integerValue": str(int(v))} if v.is_integer() and abs(v) < 2 ** 53 \
            else {"doubleValue": v}
    if isinstance(v, str):
        return {"stringValue": v}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [_to_fs(x) for x in v]}}
    if isinstance(v, dict):
        return {"mapValue": {"fields": {str(k): _to_fs(x) for k, x in v.items()}}}
    return {"stringValue": str(v)}


def _players_url(pid=None):
    base = _firestore_base() + f"/users/{_account['uid']}/players"
    return base + (f"/{pid}" if pid else "")


def data_load_players():
    """Every player document on this login, as the website stores them."""
    out, page = {}, ""
    while True:
        r = _http(_players_url() + "?pageSize=100" + (f"&pageToken={page}" if page else ""),
                  token=id_token())
        for d in r.get("documents", []):
            pid = d["name"].rsplit("/", 1)[-1]
            out[pid] = {k: _fs_value(x) for k, x in d.get("fields", {}).items()}
        page = r.get("nextPageToken", "")
        if not page:
            break
    _players["list"] = sorted(({"id": k, "name": v.get("name", k)} for k, v in out.items()),
                              key=lambda p: p["name"].lower())
    return out


def data_save_player(pid, data):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", pid or ""):
        raise ValueError("bad player id")
    data = dict(data or {})
    data["updatedAt"] = int(time.time() * 1000)
    body = {"fields": {k: _to_fs(v) for k, v in data.items()}}
    _http(_players_url(pid), body, token=id_token(), method="PATCH")   # whole document
    return data["updatedAt"]


def data_delete_player(pid):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", pid or ""):
        raise ValueError("bad player id")
    _http(_players_url(pid), token=id_token(), method="DELETE")


# ---- droid pictures: looked up on the Droid Tycoon wiki, saved next to the app ----
WIKI_API = "https://star-wars-droid-tycoon.fandom.com/api.php"
PIC_DIR = "droid_pictures"
PIC_MATCH_VERSION = 2          # raise when the matching changes, so saved lists are redone
_pics = {"index": None, "status": "", "loading": False}
_VARIANT_WORDS = {"default": "0", "base": "0", "basic": "0", "normal": "0", "regular": "0",
                  "gold": "1", "golden": "1", "diamond": "2", "rainbow": "3", "beskar": "4",
                  "galactic": "5", "stellar": "6", "kyber": "7", "scourge": "8"}
_KYBER_WORDS = {"green": "g", "blue": "b", "purple": "p", "inactive": "n", "unactivated": "n",
                "activated": "", "not": "n"}
# other spellings the wiki may use -> the app's droid name
PIC_ALIASES = {"GNK": "Gonk", "GNK Droid": "Gonk", "GNK Power Droid": "Gonk", "Power Droid": "Gonk",
               "IG-11 Marshall": "IG-11 Marshal", "IG-11": "IG-11 Marshal", "IG11": "IG-11 Marshal",
               "IG11 Marshal": "IG-11 Marshal", "BU4D": "BU-4D", "BU 4 D": "BU-4D", "BU4-D": "BU-4D",
               "ScourgePrime": "Scourge Prime"}
_NOISE_WORDS = {"worker", "astromech", "battle", "protocol", "common", "rare", "epic", "legendary",
                "mythic", "iconic", "series", "unit", "power", "droid", "png", "jpg", "jpeg", "webp", "gif", "icon", "render", "image", "img",
                "portrait", "card", "fortnite", "dt", "sw", "variant", "tycoon", "model",
                "full", "art", "the", "star", "wars", "version", "skin", "large", "big"}


def _pic_tokens(text):
    text = re.sub(r"\.(png|jpe?g|webp|gif)$", "", text.lower())
    text = re.sub(r"^file:", "", text)
    return [t for t in re.split(r"[^a-z0-9]+", text) if t]


def match_picture(title, names):
    """(droid name, variant key) a wiki file shows, or None. Strict: every word in
    the file name must be the droid's name, a variant word or a harmless word, so
    'BB-8 Gold' is never taken for 'BB Gold'."""
    toks = _pic_tokens(title)
    best = None
    for name in names:
        nt = _pic_tokens(name)
        n = len(nt)
        for i in range(len(toks) - n + 1):
            if toks[i:i + n] != nt:
                continue
            rest = toks[:i] + toks[i + n:]
            var, kyc, ok = None, None, True
            for t in rest:
                if t in _VARIANT_WORDS:
                    if var not in (None, _VARIANT_WORDS[t]):
                        ok = False
                    var = _VARIANT_WORDS[t]
                elif t in _KYBER_WORDS:
                    kyc = _KYBER_WORDS[t] if kyc is None else kyc
                elif t not in _NOISE_WORDS:
                    ok = False
                if not ok:
                    break
            if not ok:
                continue
            if kyc and var is None:
                var = "7"
            key = var or "0"
            if key == "7":
                key = "7" + (kyc if kyc in ("g", "b", "p", "n") else "")
            elif kyc:                                  # colour words on a non-Kyber
                continue
            score = (n, -len(rest))                    # longer name match, fewer extras
            if best is None or score > best[0]:
                best = (score, name, key)
    return (PIC_ALIASES.get(best[1], best[1]), best[2]) if best else None


def _wiki_get(params):
    import urllib.request
    import urllib.parse
    url = WIKI_API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) SW-Key-Helper/{VERSION}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


DROIDEX_PAGE = "Droidex"


class _Node:
    __slots__ = ("tag", "attrs", "kids", "parent", "text")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.kids, self.text = tag, attrs, parent, [], []


def _parse_dom(html):
    """Tiny DOM from wiki HTML (enough to find each picture's table cell / caption)."""
    from html.parser import HTMLParser
    VOID = {"img", "br", "hr", "meta", "link", "input", "source", "wbr", "col"}
    root = _Node("root", {}, None)

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.cur = root

        def handle_starttag(self, tag, attrs):
            n = _Node(tag, dict(attrs), self.cur)
            self.cur.kids.append(n)
            if tag not in VOID:
                self.cur = n

        def handle_endtag(self, tag):
            c = self.cur
            while c is not root and c.tag != tag:
                c = c.parent
            if c is not root:
                self.cur = c.parent

        def handle_data(self, data):
            self.cur.kids.append(data)

    P().feed(html)
    return root


def _text(n):
    out = []

    def walk(x):
        if isinstance(x, str):
            out.append(x)
        else:
            for k in x.kids:
                walk(k)
    walk(n)
    return re.sub(r"\s+", " ", " ".join(out)).strip()


def _find_name_in(text, names):
    toks = _pic_tokens(text)
    best = None
    for name in names:
        nt = _pic_tokens(name)
        for i in range(len(toks) - len(nt) + 1):
            if toks[i:i + len(nt)] == nt:
                # don't take "BB" out of "BB 8" / "BB9": the next word mustn't continue the name
                nxt = toks[i + len(nt)] if i + len(nt) < len(toks) else ""
                if nxt.isdigit() and len(nxt) <= 2:
                    continue
                if best is None or len(nt) > len(_pic_tokens(best)):
                    best = name
    return best


def _find_variant_in(text):
    toks = _pic_tokens(text)
    var = next((_VARIANT_WORDS[t] for t in toks if t in _VARIANT_WORDS), None)
    kyc = next((_KYBER_WORDS[t] for t in toks if t in _KYBER_WORDS and _KYBER_WORDS[t]), None)
    if var == "7" or (var is None and kyc):
        return "7" + (kyc or "")
    return var


def droidex_pictures(html, names):
    """{'Name|key': url} from the wiki's Droidex page: each picture is matched by its
    file name, or else by the droid name / variant written in its table cell, row,
    column heading or caption."""
    root = _parse_dom(html)
    found = {}

    def img_url(a):
        for k in ("data-src", "src"):
            u = a.get(k) or ""
            if u.startswith("http") and "wikia" in u or u.startswith("http") and "nocookie" in u:
                return re.sub(r"/revision/latest/[^?]*", "/revision/latest", u)
        return None

    def ancestors(n):
        while n is not None:
            yield n
            n = n.parent

    def walk(n):
        for k in n.kids:
            if isinstance(k, str):
                continue
            if k.tag == "img":
                handle(k)
            walk(k)

    def handle(img):
        url = img_url(img.attrs)
        if not url:
            return
        fname = img.attrs.get("data-image-name") or img.attrs.get("data-image-key") or \
            url.split("/revision/")[0].rsplit("/", 1)[-1]
        fname = re.sub(r"%[0-9A-Fa-f]{2}", " ", fname)
        m = match_picture(fname, names)
        explicit = m is not None and any(t in _VARIANT_WORDS or t in _KYBER_WORDS
                                         for t in _pic_tokens(fname))
        if m is None or not explicit:
            ctx_cell, ctx_row, ctx_col = "", "", ""
            alt = (img.attrs.get("alt") or "") + " " + \
                next((a.attrs.get("title", "") for a in ancestors(img) if a.tag == "a"), "")
            cell = next((a for a in ancestors(img) if a.tag in ("td", "th", "li", "figure")
                         or "gallery" in a.attrs.get("class", "")), None)
            if cell is not None:
                ctx_cell = _text(cell)
                if cell.tag in ("td", "th"):
                    row = cell.parent
                    cells = [c for c in row.kids if not isinstance(c, str) and c.tag in ("td", "th")]
                    col = cells.index(cell) if cell in cells else -1
                    ctx_row = " ".join(_text(c) for c in cells[:2])
                    table = next((a for a in ancestors(row) if a.tag == "table"), None)
                    if table is not None and col >= 0:
                        first = next(_iter_tag(table, "tr"), None)
                        if first is not None and first is not row:
                            hdr = [c for c in first.kids if not isinstance(c, str) and c.tag in ("td", "th")]
                            if col < len(hdr):
                                ctx_col = _text(hdr[col])
            var = (_find_variant_in(fname) or _find_variant_in(alt) or _find_variant_in(ctx_cell)
                   or _find_variant_in(ctx_col) or _find_variant_in(_tab_label(img)))
            if m is not None:
                m = (m[0], var or m[1])
            else:
                name = (_find_name_in(alt, names) or _find_name_in(ctx_cell, names)
                        or _find_name_in(ctx_row, names))
                if not name:
                    return
                m = (PIC_ALIASES.get(name, name), var or "0")
        key = f"{m[0]}|{m[1]}"
        found.setdefault(key, url)

    walk(root)
    return found


def _tab_label(n):
    """Label of the tab (Fandom 'tabber') a picture sits in, e.g. 'Gold'."""
    labels = []
    while n is not None:
        cls = n.attrs.get("class", "") if not isinstance(n, str) else ""
        if "wds-tab__content" in cls or "tabbertab" in cls:
            if n.attrs.get("title"):
                labels.append(n.attrs["title"])
            else:
                par = n.parent
                contents = [k for k in par.kids if not isinstance(k, str)
                            and "wds-tab__content" in k.attrs.get("class", "")]
                tabs = [t for t in _iter_tag(par, "li") if "wds-tabs__tab" in t.attrs.get("class", "")]
                i = contents.index(n) if n in contents else -1
                if 0 <= i < len(tabs):
                    labels.append(tabs[i].attrs.get("data-hash") or _text(tabs[i]))
        n = n.parent
    return " ".join(labels)


def _iter_tag(n, tag):
    for k in n.kids:
        if isinstance(k, str):
            continue
        if k.tag == tag:
            yield k
        yield from _iter_tag(k, tag)


def build_picture_index(force=False):
    """List the wiki's pictures once (refreshed weekly) and work out which droid and
    variant each one shows."""
    path = os.path.join(PIC_DIR, "index.json")
    if not force and _pics["index"] is None:
        try:
            with open(path) as f:
                saved = json.load(f)
            if time.time() - saved.get("t", 0) < 7 * 86400 and saved.get("map") \
                    and saved.get("v") == PIC_MATCH_VERSION:
                _pics["index"] = saved["map"]
                have = {k.split("|")[0] for k in saved["map"]}
                missing = sorted(set(DROID_RARITY) - have)
                _pics["status"] = (f"{len(saved['map'])} pictures for {len(have)} of {len(DROID_RARITY)} droids"
                                   + (f". No picture yet: {', '.join(missing)}" if missing else ""))
                return
        except Exception:
            pass
    if _pics["loading"]:
        return
    _pics["loading"] = True
    _pics["status"] = "Looking up droid pictures on the wiki…"
    try:
        files, cont = [], {}
        try:
            for _ in range(40):                      # up to 20,000 files
                r = _wiki_get({"action": "query", "list": "allimages", "ailimit": "500",
                               "aiprop": "url", "format": "json", **cont})
                files += r.get("query", {}).get("allimages", [])
                cont = r.get("continue") or {}
                if not cont:
                    break
        except Exception as e:
            print("Couldn't list the wiki's pictures:", e)
        names = list(DROID_RARITY) + list(PIC_ALIASES)
        found = {}
        try:
            r = _wiki_get({"action": "parse", "page": DROIDEX_PAGE, "prop": "text",
                           "format": "json", "formatversion": "2"})
            page_html = r.get("parse", {}).get("text", "")
            if isinstance(page_html, dict):
                page_html = page_html.get("*", "")
            for k, u in droidex_pictures(page_html, names).items():
                found[k] = ("", u)
            print("Droidex page pictures:", len(found))
        except Exception as e:
            print("Couldn't read the Droidex page:", e)
        for f in files:
            m = match_picture(f.get("name", ""), names)
            if not m or not f.get("url"):
                continue
            key = f"{m[0]}|{m[1]}"
            prev = found.get(key)
            if prev is not None and prev[0] == "":
                continue                             # the Droidex page already has one
            if prev is None or len(f["name"]) < len(prev[0]):
                found[key] = (f["name"], f["url"])
        mapping = {k: v[1] for k, v in found.items()}
        for f in os.listdir(PIC_DIR) if os.path.isdir(PIC_DIR) else []:
            if f.endswith(".png") and force:
                try:
                    os.remove(os.path.join(PIC_DIR, f))   # "Look again": fetch fresh copies
                except OSError:
                    pass
        os.makedirs(PIC_DIR, exist_ok=True)
        with open(path, "w") as fh:
            json.dump({"t": time.time(), "v": PIC_MATCH_VERSION, "files": len(files),
                       "map": mapping}, fh)
        _pics["index"] = mapping
        have = {k.split("|")[0] for k in mapping}
        missing = sorted(set(DROID_RARITY) - have)
        _pics["status"] = (f"{len(mapping)} pictures for {len(have)} of {len(DROID_RARITY)} droids from the wiki"
                           + (f". No picture yet: {', '.join(missing)}" if missing else "")
                           if mapping else "Couldn't match any wiki pictures to droids")
        print("Droid pictures:", _pics["status"])
    except Exception as e:
        print("Couldn't load droid pictures:", e)
        _pics["status"] = f"Couldn't reach the wiki for pictures ({e})"[:140]
        if _pics["index"] is None:
            _pics["index"] = {}
    finally:
        _pics["loading"] = False
    state["data_version"] = state.get("data_version", 0) + 1     # let the window redraw


_custom = {"t": 0.0, "map": {}}


def custom_picture(name, key):
    """Your own pictures: put files like "Gonk.png" or "BU-4D Gold.png" in
    droid_pictures/custom and they're used before the wiki's."""
    folder = os.path.join(PIC_DIR, "custom")
    if time.time() - _custom["t"] > 10:
        _custom["t"] = time.time()
        found = {}
        try:
            os.makedirs(folder, exist_ok=True)
            names = list(DROID_RARITY) + list(PIC_ALIASES)
            for f in os.listdir(folder):
                if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    m = match_picture(f, names)
                    if m:
                        found[f"{m[0]}|{m[1]}"] = os.path.join(folder, f)
        except OSError:
            pass
        _custom["map"] = found
    m = _custom["map"]
    return m.get(f"{name}|{key}") or (m.get(f"{name}|7") if key.startswith("7") else None) \
        or m.get(f"{name}|0")


def picture_file(name, key):
    """Local file for a droid picture (downloaded on first use), or None."""
    own = custom_picture(name, key)
    if own:
        return own
    idx = _pics["index"] or {}
    tries = [key]
    if key.startswith("7") and len(key) == 2:
        tries += ["7", "7n", "7g"]                   # any Kyber picture beats none
    elif key == "7":
        tries += ["7n", "7g", "7b", "7p"]
    if "0" not in tries:
        tries.append("0")                            # no picture of that variant: use Default
    url = None
    for k in tries:
        url = idx.get(f"{name}|{k}")
        if url:
            key = k
            break
    if not url:
        return None
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{name}_{key}")
    path = os.path.join(PIC_DIR, safe + ".png")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    import urllib.request
    small = url
    if "/revision/latest" in url and "scale-to-width" not in url:
        small = url.replace("/revision/latest", "/revision/latest/scale-to-width-down/320", 1)
    try:
        req = urllib.request.Request(small, headers={"User-Agent": "Mozilla/5.0 SW-Key-Helper"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read(5_000_000)
        os.makedirs(PIC_DIR, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path
    except Exception as e:
        print("Picture download failed:", name, key, e)
        return None


def start_panel_server():
    import http.server
    import secrets
    import urllib.parse
    token = secrets.token_urlsafe(18)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authed(self):
            return secrets.compare_digest(self.headers.get("X-Token", ""), token)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path in ("/", "/index.html"):
                self._send(200, _panel_html(), "text/html")
            elif path == "/api/state" and self._authed():
                self._send(200, json.dumps(panel_state()))
            elif path == "/img":
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                name, key = q.get("d", [""])[0], q.get("v", ["0"])[0]
                f = picture_file(name, key) if name in DROID_RARITY else None
                if f:
                    with open(f, "rb") as fh:
                        data = fh.read()
                    ctype = "image/webp" if data[:4] == b"RIFF" else (
                        "image/jpeg" if data[:2] == b"\xff\xd8" else "image/png")
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Cache-Control", "max-age=86400")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._send(404, "{}")
            elif path == "/api/players" and self._authed():
                try:
                    self._send(200, json.dumps({"ok": True, "players": data_load_players(),
                                                "player": cfg["inventory_player"]}))
                except Exception as e:
                    self._send(200, json.dumps({"ok": False, "error": str(e)}))
            elif path == "/logo.png":
                try:
                    data = icon_png(256)
                except Exception as e:
                    print("Logo failed:", e)
                    data = None
                if data:
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Cache-Control", "max-age=86400")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._send(404, "{}")
            elif path == "/icon.ico":
                try:
                    with open(icon_path(), "rb") as f:
                        self._send(200, f.read(), "image/x-icon")
                except (OSError, TypeError):
                    self._send(404, "{}")
            else:
                self._send(404 if path != "/api/state" else 403, "{}")

        def do_POST(self):
            if not self._authed():
                self._send(403, "{}")
                return
            try:
                n = min(int(self.headers.get("Content-Length", 0)), 100000)
                data = json.loads(self.rfile.read(n) or b"{}")
                path = urllib.parse.urlsplit(self.path).path
                if path == "/api/set":
                    err = panel_set(data.get("key"), data.get("value"))
                elif path == "/api/action":
                    err = panel_action(data.get("action"), data)
                elif path == "/api/player":
                    t = data_save_player(data.get("id"), data.get("data"))
                    self._send(200, json.dumps({"ok": True, "updatedAt": t}))
                    return
                elif path == "/api/player_delete":
                    data_delete_player(data.get("id"))
                    err = ""
                else:
                    self._send(404, "{}")
                    return
                self._send(200, json.dumps({"ok": not err, "error": err}))
            except Exception as e:
                print("App window request failed:", e)
                self._send(200, json.dumps({"ok": False, "error": str(e)}))

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    _panel.update(port=srv.server_address[1], token=token)
    threading.Thread(target=build_picture_index, daemon=True).start()
    fetch_shared()                               # trait odds for the app window
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("App window page on port", _panel["port"])
    return srv


def panel_url():
    return f"http://127.0.0.1:{_panel['port']}/#t={_panel['token']}"


def open_app_window():
    """Show the app window (its own small process, so a problem there can't stop
    the hotkeys). Falls back to Edge's app mode, then the normal browser."""
    if not _panel["port"]:
        return
    p = _panel.get("proc")
    if p is not None and p.poll() is None:
        try:                                     # already open - bring it forward
            u = ctypes.windll.user32
            hwnd = u.FindWindowW(None, APP_WINDOW_TITLE)
            if hwnd:
                if u.IsIconic(hwnd):
                    u.ShowWindow(hwnd, 9)
                u.keybd_event(0x12, 0, 0, 0)
                u.keybd_event(0x12, 0, 2, 0)
                u.SetForegroundWindow(hwnd)
                return
        except Exception:
            pass
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--window", panel_url()]
    else:
        cmd = [sys.executable, os.path.abspath(__file__), "--window", panel_url()]
    try:
        _panel["proc"] = subprocess.Popen(cmd, cwd=BASE_DIR)
    except OSError as e:
        print("Couldn't open the app window:", e)
        _open_in_browser(panel_url())


def _open_in_browser(url):
    edge = [os.path.join(os.environ.get(k, ""), "Microsoft", "Edge", "Application", "msedge.exe")
            for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    for path in edge:
        if os.path.exists(path):
            try:
                subprocess.Popen([path, f"--app={url}", "--window-size=1400,950",
                                  "--user-data-dir=" + os.path.join(BASE_DIR, "app_data")])
                return
            except OSError:
                pass
    import webbrowser
    webbrowser.open(url)


def run_window_process(url):
    """'Droid Tycoon Helper.exe --window <address>': just the app window."""
    data_dir = os.path.join(BASE_DIR, "app_data")
    set_app_id()
    try:
        import webview
    except Exception as e:
        print("pywebview not available:", e)
        _open_in_browser(url)
        return

    def watchdog(win):
        """Close the window when the main app quits."""
        import urllib.request
        misses = 0
        while True:
            time.sleep(2)
            try:
                urllib.request.urlopen(url.split("#")[0], timeout=2).read(16)
                misses = 0
            except Exception:
                misses += 1
                if misses >= 3:
                    win.destroy()
                    return

    try:
        win = webview.create_window(APP_WINDOW_TITLE, url, width=1400, height=950,
                                    min_size=(820, 560), background_color="#0e1014")
        webview.start(func=watchdog, args=(win,), private_mode=False, storage_path=data_dir)
    except Exception as e:
        print("App window failed:", e)
        _open_in_browser(url)


APP_EXE = "Droid Tycoon Helper.exe"


def move_to_new_name():
    """The app used to be "SW Key Helper.exe". Updates keep the old file name, and
    Windows keeps showing the old icon for it - so copy ourselves to the new name
    once and start that. The old file then just opens the new one."""
    if not getattr(sys, "frozen", False) or sys.platform != "win32":
        return False
    cur = os.path.abspath(sys.executable)
    if os.path.basename(cur).lower() != "sw key helper.exe":
        return False
    want = os.path.join(os.path.dirname(cur), APP_EXE)
    try:
        if not os.path.exists(want) or version_tuple(VERSION) > version_tuple(
                open(want + ".version").read() if os.path.exists(want + ".version") else "0"):
            import shutil
            shutil.copy2(cur, want)
            with open(want + ".version", "w") as f:
                f.write(VERSION)
    except OSError as e:
        print("Couldn't make", APP_EXE, "-", e)
        return False
    print("Starting", want)
    state["restart_path"] = want
    restart_new_version()
    return True


def set_app_id():
    """Own taskbar identity, so Windows uses this app's icon (not a cached one)."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LMS.DroidTycoonHelper")
        except Exception:
            pass


def main():
    print(__doc__)
    print("Version", VERSION, "\u00b7 certificates:", CERT_SOURCE)
    if move_to_new_name():
        return
    set_app_id()
    if getattr(sys, "frozen", False):
        threading.Thread(target=cleanup_old_versions, daemon=True).start()
        if cfg["auto_update"]:
            threading.Thread(target=check_for_update, daemon=True).start()
    if not require_login():
        print("Not signed in - closing.")
        state["quit"] = True
        if state.get("restart_path"):            # an update arrived while signing in
            restart_new_version()
        return
    if cfg["tray_icon"]:
        threading.Thread(target=start_tray, daemon=True).start()
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        state["error"] = "Tesseract not found - check tesseract_path in config.json"
        print(state["error"])
    listener = keyboard.Listener(on_press=on_key)
    listener.start()
    threading.Thread(target=rebirth_watch, daemon=True).start()
    threading.Thread(target=scanner, daemon=True).start()
    try:
        start_panel_server()
        if cfg["open_window_on_start"]:
            open_app_window()
    except Exception as e:
        print("App window unavailable:", e)
    try:
        run_windows()
    except KeyboardInterrupt:
        pass
    state["quit"] = True
    listener.stop()
    p = _panel.get("proc")
    if p is not None and p.poll() is None:
        try:
            p.terminate()
        except Exception:
            pass
    if state.get("tray") is not None:
        try:
            state["tray"].stop()
        except Exception:
            pass
    restart_new_version()
    print("Bye.")


def restart_new_version():
    if state.get("restart_path"):
        # Start the new version with a clean environment, so it unpacks its own
        # files (incl. Tesseract) instead of reusing this copy's temp folder,
        # which is deleted as soon as this copy closes.
        for k in list(os.environ):
            if k.startswith("_PYI") or k in ("_MEIPASS2", "TESSDATA_PREFIX"):
                del os.environ[k]
        os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        try:
            subprocess.Popen([state["restart_path"]], cwd=BASE_DIR, env=dict(os.environ))
        except OSError:
            os.startfile(state["restart_path"])   # lets Windows ask for admin if needed


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--window":
        run_window_process(sys.argv[2])
    else:
        main()
