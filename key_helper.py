"""
SW Key Helper - two helpers for the Droid Tycoon map:
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
  Page Up     faster  (fishing: less delay between presses / clicker: more clicks)
  Page Down   slower
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

# Always work in the folder this program lives in (double-clicking or starting as
# admin can otherwise start it in C:\Windows\System32 and lose config.json)
BASE_DIR = os.path.dirname(os.path.abspath(
    sys.executable if getattr(sys, "frozen", False) else __file__))
os.chdir(BASE_DIR)

# With no console window (double-click / .exe), keep messages in a log file
if sys.stdout is None or sys.stderr is None or not sys.stdout.isatty():
    try:
        _log = open(os.path.join(BASE_DIR, "key_helper.log"), "w", buffering=1,
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
VERSION = "1.3.0"
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
    "inv_add_key": "f9",
    "inv_remove_key": "f10",
    # --- Overlay ---
    "overlay": True,            # show the on-screen info box
    "hide_when_paused": True,   # hide the box while paused (it pops up briefly on changes)
    "overlay_x": 20,
    "overlay_y": 120,
    "mini_badge": True,         # small "running" badge in the top-left when idle
    "mini_x": 8,
    "mini_y": 8,
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
    PRIMARY = dict(_s.monitors[1])  # main screen

state = {
    "enabled": False, "quit": False, "snapshot": False, "select_area": False,
    "reading": "", "pressed": "", "scan_ms": 0, "inputs": 0,
    "message": "", "error": "", "show_until": 0.0,
    "mode": "fishing", "menu_open": False, "menu_choice": 0, "menu_time": 0.0,
    "trait": "", "rolls": 0, "status": "",
    "menu_stage": "mode", "pick_index": 0, "choice_index": 0,
    "trait_queue": [], "trait_side": "left", "side_checked": None,
    "inv_pending": None, "inv_busy": False,
    "clicks": 0, "spent": 0.0, "cost": "", "pending_roll": None, "roll_times": [],
}
MODES = ["fishing", "traits", "clicker"]
MODE_NAMES = ["Fishing", "Traits (droid scanner)", "Auto Clicker"]
DROID_CHOICES = [("left", "Left droid"), ("right", "Right droid"), ("both", "Both (left, then right)")]
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
TRAIT_NAMES = list(TRAIT_WORDS)
# these only ever come in rainbow, so an unreadable non-rainbow trait can't be one
ALWAYS_RAINBOW = {"Overclocked", "Chip Finder", "Passive Credits",
                  "Fusion Preservation", "Golden Splash Refill"}
CHOICES = ["off", "any", "rainbow"]


def notify(msg, seconds=2.5):
    print(msg)
    state["message"] = msg
    state["show_until"] = time.time() + seconds  # pop the overlay up for a while


# ---------------------------------------------------------------- hotkeys ---

def start_mode():
    state["menu_open"] = False
    state["rolls"], state["trait"], state["status"] = 0, "", ""
    state["clicks"], state["spent"], state["cost"] = 0, 0.0, ""
    state["pending_roll"], state["roll_times"] = None, []
    state["last_cost"] = None
    if state["mode"] == "traits":
        state["trait_queue"] = {"left": ["left"], "right": ["right"],
                                "both": ["left", "right"]}.get(cfg["trait_droids"], ["left"])
        state["trait_side"] = state["trait_queue"].pop(0)
        state["side_checked"] = None
        fetch_shared()
    state["enabled"] = True
    notify(f"{state['mode'].title()} started")


def on_key(key):
    K = keyboard.Key
    if key == K.insert:
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
            cur = cfg["trait_wanted"].get(name, "off")
            cfg["trait_wanted"][name] = CHOICES[(CHOICES.index(cur) + step) % len(CHOICES)]
    elif key in (K.page_up, K.page_down) and state["mode"] == "clicker":
        step = 1 if key == K.page_up else -1
        if cfg["clicker_kind"] == "potion":       # Page Up = faster = shorter wait
            cfg["potion_interval"] = int(min(20, max(2, cfg["potion_interval"] - step)))
            notify(f"Potion Grab: F every {cfg['potion_interval']} seconds")
        else:
            cfg["clicker_cps"] = int(min(50, max(1, cfg["clicker_cps"] + step)))
            notify(f"Scrap Hit: {cfg['clicker_cps']} clicks per second")
        save_config()
    elif key in (K.page_up, K.page_down):
        step = -DELAY_STEP if key == K.page_up else DELAY_STEP
        cfg["press_delay"] = round(min(DELAY_MAX, max(DELAY_MIN, cfg["press_delay"] + step)), 3)
        save_config()
        notify(f"Delay between presses: {int(cfg['press_delay'] * 1000)} ms")
    elif key == getattr(K, cfg["inv_add_key"], None):
        inventory_hotkey("add")
    elif key == getattr(K, cfg["inv_remove_key"], None):
        inventory_hotkey("remove")
    elif key == K.home:
        state["select_area"] = True
    elif key == K.delete:
        state["snapshot"] = True
    elif key == K.end:
        state["quit"] = True
        return False


# ---------------------------------------------------------- tile detection ---

def get_search_area():
    if cfg["search_area"]:
        return cfg["search_area"]
    m = PRIMARY
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
    return np.array(sct.grab(PRIMARY))[:, :, :3]


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
        pick = max if side == "right" else min
        return "select", pick(selects, key=lambda b: b["x"])
    return None


def droid_card_check(bgr, button):
    """Is the droid above this SELECT button Iconic? -> 'iconic', 'ok' or 'unsure'."""
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
    if any("ICONIC" in t or _ratio(t, "ICONIC") >= 0.75 for t in words):
        return "iconic", name or ""
    if name and sc >= 0.7 and DROID_RARITY.get(name) == "Iconic":
        return "iconic", name
    other = [r for r in RARITY_WORDS if r != "ICONIC"]
    if any(r in t or _ratio(t, r) >= 0.8 for t in words for r in other):
        return "ok", name or ""
    if name and sc >= 0.85:
        return "ok", name
    return "unsure", name or ""


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


def is_wanted(name, rainbow):
    choice = cfg["trait_wanted"].get(name, "off")
    return choice == "any" or (choice == "rainbow" and rainbow)


def could_be_wanted(rainbow):
    """For a trait we couldn't read: might it be one we want?"""
    for name, choice in cfg["trait_wanted"].items():
        if choice == "rainbow" and rainbow:
            return True
        if choice == "any" and (rainbow or name not in ALWAYS_RAINBOW):
            return True
    return False


def wanted_summary():
    parts = [f"{n} ({'rainbow' if c == 'rainbow' else 'any'})"
             for n, c in cfg["trait_wanted"].items() if c != "off"]
    return ", ".join(parts) or "nothing"


def click_at(x, y):
    x, y = int(PRIMARY["left"] + x), int(PRIMARY["top"] + y)
    pydirectinput.moveTo(x, y)
    time.sleep(0.06)
    pydirectinput.moveTo(x + 2, y)       # small wiggle so the game notices the cursor
    time.sleep(0.06)
    pydirectinput.moveTo(x, y)
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
    if not suf and "." in num and num[-1] in "68" and len(num.split(".")[1]) == 3:
        num, suf = num[:-1], "B"                # "33.608" / "67.206" -> ...B
    if suf not in SUFFIX:
        return None
    return float(num) * SUFFIX[suf]


def format_amount(value):
    for suf, mult in sorted(SUFFIX.items(), key=lambda kv: -kv[1]):
        if value >= mult and mult > 1:
            return f"{value / mult:.2f}{suf.title() if len(suf) > 1 else suf}"
    return f"{value:.0f}"


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
    text = pytesseract.image_to_string(bw, config="--psm 8").strip()   # best for "67.20B"
    value = parse_amount(text)
    if value is None:
        text2 = pytesseract.image_to_string(
            bw, config="--psm 7 -c tessedit_char_whitelist=0123456789.KMBTQaiSxp").strip()
        value = parse_amount(text2)
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
        notify("Signed out - SW Key Helper will close", 5)


def login_window(message=""):
    """Small sign-in window. Returns True once signed in, False if closed."""
    win = tk.Tk()
    win.title("SW Key Helper - Sign in")
    win.configure(bg=BG)
    win.resizable(False, False)
    win.attributes("-topmost", True)
    for folder in (getattr(sys, "_MEIPASS", BASE_DIR), BASE_DIR):
        ico = os.path.join(folder, "SW Key Helper.ico")
        if os.path.exists(ico):
            try:
                win.iconbitmap(ico)
            except Exception:
                pass
            break
    box = tk.Frame(win, bg=BG, padx=28, pady=22)
    box.pack()
    tk.Label(box, text="SW KEY HELPER", bg=BG, fg=FG,
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
    hits = sum(c for (trait, tier), c in tally.items() if is_wanted(trait, tier == "rainbow"))
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


def run_traits(sct):
    try:
        while traits_active():
            # 1. Press F at the scanner (retry - the last roll may still be animating)
            state["status"] = "Opening droid menu"
            found = None
            for _ in range(5):
                press(cfg["interact_key"])
                found = wait_for_menu(sct, "either", 2.5)
                if found or not traits_active():
                    break
            if not traits_active():
                return
            if not found:
                stop_traits("Couldn't open the droid menu - stand at the trait scanner")
                return

            # 2. Pick the droid (left or right) - never an Iconic one
            side = state["trait_side"]
            if found[0] != "select" and state["side_checked"] != side:
                stop_traits("Couldn't see the Select Droid screen to check the droid's "
                            "rarity - stopped to be safe")
                return
            if found[0] == "select":
                state["status"] = f"Selecting {side} droid"
                if not traits_wait(0.4):          # let the menu finish its pop-in
                    return
                settled = wait_for_menu(sct, "select", 1.5) or found
                if state["side_checked"] != side:
                    verdict, dname = droid_card_check(grab_screen(sct), settled[1])
                    if verdict == "iconic":
                        stop_traits(f"The {side} droid is Iconic ({dname or 'Iconic'}) - "
                                    "never rolling Iconics. Stopped.")
                        return
                    if verdict == "unsure":
                        stop_traits(f"Couldn't tell if the {side} droid is Iconic - "
                                    "stopped to be safe")
                        return
                    state["side_checked"] = side
                    state["trait_droid"] = dname
                click_at(settled[1]["cx"], settled[1]["cy"])
                found = wait_for_menu(sct, "panel", 3)
                if not found and traits_active():
                    sel = wait_for_menu(sct, "select", 0.5)
                    if sel:
                        click_at(sel[1]["cx"], sel[1]["cy"])
                    found = wait_for_menu(sct, "panel", 3)
                if not traits_active():
                    return
                if not found:
                    stop_traits("The trait panel didn't open after selecting the droid")
                    return

            # 3. Read the droid's current trait BEFORE spending anything
            state["status"] = "Reading trait"
            if not traits_wait(0.5):              # let the panel finish sliding in
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
                time.sleep(0.3)
            rainbow = tier_name == "rainbow"
            if state["pending_roll"] is not None:   # this is the result of our last roll
                record_roll(name or "?", tier_name, state["pending_roll"])
                state["pending_roll"] = None
            tier = f" ({tier_name})" if tier_name and tier_name != "default" else ""
            state["trait"] = (name + tier) if name else (
                (max(texts, key=len)[:30] + tier) if any(texts) else "(none yet)")
            if name and is_wanted(name, rainbow):
                if next_droid(f"GOT {name.upper()}{tier.upper()} on the {side} droid "
                              f"after {state['rolls']} rolls!"):
                    continue
                return
            if not name and not tier_name:      # grey "?" - this droid has no trait yet
                state["trait"] = "(no trait yet)"
            elif not name and cfg["stop_if_unreadable"] and could_be_wanted(rainbow):
                stop_traits("Couldn't read this trait and it might be one you want - "
                            "stopped to be safe. Check it!")
                return
            if cfg["max_rolls"] and state["rolls"] >= cfg["max_rolls"]:
                if next_droid(f"Reached max_rolls ({cfg['max_rolls']})."):
                    continue
                return

            # 4. Click the credit button and make sure the roll started
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
            if cost is not None:
                state["last_cost"] = cost
            limit = parse_amount(cfg["max_cost"] if "." in cfg["max_cost"] else
                                 re.sub(r"^(\d+)", r"\1.00", cfg["max_cost"]))
            if cfg["max_cost"] != "off" and limit:
                last = state.get("last_cost")
                if cost is None and not (last and last * 3 < limit):
                    stop_traits("Couldn't read the roll cost - stopped because the cost "
                                "limit is on")
                    return
                if cost is not None and cost > limit:
                    if next_droid(f"Next roll on the {side} droid costs {format_amount(cost)} - "
                                  f"over your {cfg['max_cost']} limit."):
                        continue
                    return
            state["status"] = f"Rolling (#{state['rolls'] + 1}) \u00b7 {state['cost']}"
            click_at(panel[1]["cx"], panel[1]["cy"])
            started = False
            end = time.time() + 3
            while time.time() < end and traits_active():
                time.sleep(0.2)
                if not find_menu(grab_screen(sct)):
                    started = True
                    break
            if not traits_active():
                return
            if not started:
                stop_traits("Roll didn't start - not enough credits?")
                return
            state["rolls"] += 1
            state["inputs"] += 1
            state["pending_roll"] = cost
            state["spent"] += cost or 0
            state["roll_times"] = (state["roll_times"] + [time.time()])[-50:]

            # 5. Wait for the roll animation, then go again
            state["status"] = f"Waiting for roll #{state['rolls']}"
            if not traits_wait(cfg["roll_wait"]):
                return
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
    "Iconic": "WG-22 B,D-O W,DJ R-3X W,BB-8 A,CB-23 A,Chopper A,R2-D2 A,IG-11 Marshal B,Mister Bones B,C-3PO P",
}
DROID_RARITY = {x[:x.rfind(" ")]: r for r, s in DROID_RAW.items() for x in s.split(",")}
DROID_NAMES = list(DROID_RARITY)
VARIANTS = ["DEFAULT", "GOLD", "DIAMOND", "RAINBOW", "BESKAR", "GALACTIC", "STELLAR", "KYBER"]
VARIANT_LABEL = {"DEFAULT": "Basic", "GOLD": "Gold", "DIAMOND": "Diamond", "RAINBOW": "Rainbow",
                 "BESKAR": "Beskar", "GALACTIC": "Galactic", "STELLAR": "Stellar", "KYBER": "Kyber"}
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


def _match_name(text, pool=None):
    toks = [_sq(t) for t in text.split() if _sq(t)]
    cands = toks + [a + b for a, b in zip(toks, toks[1:])] + ["".join(toks)]
    sc, name = 0, None
    for n in (pool or DROID_NAMES):
        for t in cands:
            q = _ratio(t, _sq(n))
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
               "blue": ["DIAMOND"], "lavender": ["GALACTIC"], "red": ["RAINBOW"]}


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


def _companions_bar(bgr):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (95, 150, 150), (110, 255, 255))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(m)
    best = None
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if w > 6 * h and w > 150 and a / (w * h) > 0.6 and (best is None or w > best[2]):
            best = (x, y, w, h)
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
    nh = cv2.cvtColor(namez, cv2.COLOR_BGR2HSV)
    wm = ((nh[:, :, 1] < 70) & (nh[:, :, 2] > 200)).astype(np.uint8) * 255
    pool = [n for n, r in DROID_RARITY.items() if not rar or r.upper() == rar]
    name, sc = _match_name(_ocr(255 - wm, 7) + " " + _ocr(255 - wm, 8), pool)
    if sc < 0.5:
        return None
    return {"name": name, "variant": var, "source": "droid screen"}


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


def _read_card(bgr):
    """A blueprint card (in the shop or on the ground)."""
    H, W = bgr.shape[:2]
    out = []
    for r in _name_rows(bgr):
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
        if not var:
            continue                                 # not sure it's a card - skip
        centre = abs(cx - W / 2) / W + abs(cy - H / 2) / H     # prefer the card you're looking at
        out.append((sc, var is not None, -centre, {"name": name, "variant": var, "source": "blueprint"}))
    out.sort(key=lambda o: o[:3], reverse=True)
    return out[0][3] if out else None


def read_droid(bgr):
    """{'name', 'variant', 'source'} for the droid on screen, or None."""
    return _read_droid_screen(bgr) or _read_card(bgr)

# -------------------------------------------------------------- inventory ---
# F9 = add the droid on screen to your website inventory, F10 = remove it.
# Press once to read it, again within 8 seconds to confirm.

VARIANT_KEY = {"DEFAULT": "0", "GOLD": "1", "DIAMOND": "2", "RAINBOW": "3",
               "BESKAR": "4", "GALACTIC": "5", "STELLAR": "6"}
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


def change_inventory(name, variant, delta):
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
            key = "7" + cfg["kyber_default"]
        else:
            key = next((k for k in KYBER_KEYS if entry.get(k, 0) > 0), None)
        label = variant_label(variant, key) + " "
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
    return f"{verb} {label}{name} – {player['name']} now has {new}"


def inventory_hotkey(action):
    """Called from the hotkey listener: read the droid, then confirm on a 2nd press."""
    if state.get("inv_busy"):
        return
    if not accounts_enabled() or not _account["refresh"]:
        notify("Sign in to use the inventory keys", 4)
        return
    if _account.get("offline"):
        notify("Offline - inventory changes need internet", 4)
        return
    pend = state.get("inv_pending")
    key_name = cfg["inv_add_key" if action == "add" else "inv_remove_key"].upper()

    def work():
        state["inv_busy"] = True
        try:
            if pend and pend["action"] == action and time.time() < pend["expires"]:
                state["inv_pending"] = None
                notify("Saving…", 3)
                notify(change_inventory(pend["name"], pend["variant"],
                                        1 if action == "add" else -1), 6)
                return
            notify("Reading droid…", 4)
            with mss.mss() as sct:
                img = grab_screen(sct)
            r = read_droid(img)
            if not r:
                notify("No droid found - open the droid screen or look straight at a "
                       "blueprint card, then try again", 6)
                return
            if not r["variant"]:
                notify(f"Found {r['name']} but couldn't read its variant - try again", 6)
                return
            player = current_player()
            if not player:
                notify("No players on your website login yet", 5)
                return
            label = "" if DROID_RARITY.get(r["name"]) == "Iconic" else variant_label(r["variant"]) + " "
            state["inv_pending"] = {"action": action, "name": r["name"], "variant": r["variant"],
                                    "expires": time.time() + 8}
            verb = "Add" if action == "add" else "Remove"
            prep = "to" if action == "add" else "from"
            notify(f"{verb} {label}{r['name']} {prep} {player['name']}? "
                   f"Press {key_name} again to confirm", 8)
        except AuthError as e:
            notify(f"Website said no: {e}", 6)
        except OSError:
            notify("Couldn't reach the website - check your internet", 6)
        except Exception as e:
            print("Inventory error:", e)
            notify(f"Inventory error: {e}", 6)
        finally:
            state["inv_busy"] = False

    threading.Thread(target=work, daemon=True).start()


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


def run_clicker():
    """Scrap Hit: left-click continuously. Potion Grab: press F every few seconds."""
    next_click = time.perf_counter()
    while state["enabled"] and state["mode"] == "clicker" and not state["quit"]:
        want = cfg["clicker_window"].strip().lower()
        if want and want not in foreground_title().lower():
            state["status"] = f"Paused - {cfg['clicker_window']} isn't in front"
            time.sleep(0.25)
            next_click = time.perf_counter()
            continue
        if cfg["clicker_kind"] == "potion":
            press(cfg["interact_key"])
            state["clicks"] += 1
            end = time.time() + cfg["potion_interval"]
            while (time.time() < end and state["enabled"] and state["mode"] == "clicker"
                   and not state["quit"] and cfg["clicker_kind"] == "potion"):
                state["status"] = f"Pressed F \u00b7 next in {end - time.time():.0f}s"
                time.sleep(0.1)
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
    """Let mouse clicks pass through the overlay to the game (Windows only)."""
    if sys.platform != "win32":
        return
    try:
        hwnd = hwnd_of(win)
        style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
        style |= 0x80000 | 0x20 | 0x80 | 0x08000000  # layered, transparent, toolwindow, noactivate
        ctypes.windll.user32.SetWindowLongW(hwnd, -20, style)
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
    m = PRIMARY
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
        if x1 - x0 < 20 or y1 - y0 < 20:
            notify("Box too small - press Home to try again.")
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


def run_windows():
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.88)
    root.configure(bg=BG)
    root.geometry(f"+{cfg['overlay_x']}+{cfg['overlay_y']}")

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
    mini_dot = tk.Label(mini, bg=BG, fg=AMBER, font=("Segoe UI", 9, "bold"), text="\u25cf")
    mini_dot.pack(side="left")
    mini_text = tk.Label(mini, bg=BG, fg=DIM, font=("Segoe UI", 9),
                         text=f"SW Key Helper v{VERSION} \u00b7 Insert = menu")
    mini_text.pack(side="left", padx=(4, 0))

    make_click_through(root)
    shown = ["full"]

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
            hints.config(text="Up/Down choose \u00b7 Insert next")
        elif picking:
            title.config(text="TRAITS \u2013 STOP ON WHICH?", fg=FG)
            badge = {"off": "  \u00b7  ", "any": " ANY ", "rainbow": "RAINBOW"}
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
            hints.config(text="Up/Down move \u00b7 Left/Right change \u00b7 Insert start")
        elif state["menu_open"]:
            title.config(text="SW KEY HELPER \u2013 CHOOSE MODE", fg=FG)
            rows = [("\u25b6 " if state["menu_choice"] == i else "    ") + n
                    for i, n in enumerate(MODE_NAMES)]
            reading.config(text=rows[0])
            pressed.config(text=rows[1])
            stats.config(text=rows[2], fg=FG, font=("Segoe UI", 11))
            hints.config(text="Up/Down choose \u00b7 Insert start")
        else:
            if state["error"]:
                title.config(text="SW KEY HELPER \u2013 PROBLEM", fg=RED)
            elif state["enabled"]:
                title.config(text=f"{mode} \u2013 ACTIVE", fg=GREEN)
            else:
                title.config(text="SW KEY HELPER \u2013 STOPPED", fg=AMBER)
            stats.config(fg=DIM, font=("Segoe UI", 10))
            if state["mode"] == "traits":
                reading.config(text=f"Trait: {state['trait'] or DASH}")
                spent = f" \u00b7 spent {format_amount(state['spent'])}" if state["spent"] else ""
                droid = state["trait_side"].title() + (
                    f" ({state.get('trait_droid')})" if state.get("trait_droid") else "")
                pressed.config(text=f"{droid} droid \u00b7 Rolls: {state['rolls']}{spent} \u00b7 "
                                    f"{state['status'] or DASH}")
                limit = cfg["max_cost"] if cfg["max_cost"] != "off" else "no limit"
                stats.config(text=f"Stops on: {wanted_summary()} \u00b7 cost limit {limit}\n"
                                  + stats_summary(), wraplength=440)
            elif state["mode"] == "clicker":
                where = (f"only while {cfg['clicker_window']} is in front"
                         if cfg["clicker_window"] else "anywhere")
                if cfg["clicker_kind"] == "potion":
                    reading.config(text=f"Potion Grab \u00b7 F presses: {state['clicks']:,}")
                    pressed.config(text=f"Every {cfg['potion_interval']}s \u00b7 "
                                        f"{state['status'] or DASH}")
                    stats.config(text=f"Presses F {where}", wraplength=440)
                else:
                    reading.config(text=f"Scrap Hit \u00b7 Clicks: {state['clicks']:,}")
                    pressed.config(text=f"Speed: {cfg['clicker_cps']}/s \u00b7 "
                                        f"{state['status'] or DASH}")
                    stats.config(text=f"Clicks where your mouse is, {where}", wraplength=440)
            else:
                reading.config(text=f"Reading: {state['reading'] or DASH}")
                pressed.config(text=f"Pressed: {state['pressed'] or DASH}")
                area = "custom" if cfg["search_area"] else "auto"
                stats.config(text=f"Scan {state['scan_ms']} ms \u00b7 "
                                  f"Delay {int(cfg['press_delay'] * 1000)} ms \u00b7 "
                                  f"Inputs {state['inputs']} \u00b7 Area {area}")
            hints.config(text="Insert menu/start/stop \u00b7 PgUp/PgDn faster/slower\n"
                              f"{cfg['inv_add_key'].upper()}/{cfg['inv_remove_key'].upper()} add/remove droid "
                              "\u00b7 Home set area \u00b7 End quit")
        message.config(text=state["error"] or state["message"],
                       fg=RED if state["error"] else DIM)
        full = cfg["overlay"] and (
            not cfg["hide_when_paused"] or state["enabled"] or state["error"]
            or state["menu_open"] or time.time() < state["show_until"])
        view = "full" if full else ("mini" if cfg["mini_badge"] else "none")
        if view != shown[0]:
            frame.pack_forget()
            mini.pack_forget()
            if view == "none":
                root.withdraw()
            else:
                if view == "full":
                    frame.pack()
                    root.geometry(f"+{cfg['overlay_x']}+{cfg['overlay_y']}")
                else:
                    mini.pack()
                    root.geometry(f"+{cfg['mini_x']}+{cfg['mini_y']}")
                root.deiconify()
                make_click_through(root)
            shown[0] = view
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
    icon = state.get("tray")
    if icon is not None:
        try:
            icon.notify(msg, "SW Key Helper")
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
            notify(f"Update {latest} is available - get the new SW Key Helper.exe")
            return
        asset = next((a for a in release.get("assets", [])
                      if a["name"].lower().endswith(".exe")), None)
        if not asset:
            return
        notify(f"Downloading update {latest}...", 60)
        exe = sys.executable
        new_path, old_path = exe + ".new", exe + ".old"
        req = urllib.request.Request(asset["browser_download_url"],
                                     headers={"User-Agent": "SW-Key-Helper"})
        with urllib.request.urlopen(req, timeout=60) as r, open(new_path, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        if os.path.getsize(new_path) != asset.get("size", os.path.getsize(new_path)):
            os.remove(new_path)
            notify("Update download was incomplete - will try again next start")
            return
        # A running .exe can't be overwritten on Windows, but it can be renamed
        if os.path.exists(old_path):
            os.remove(old_path)
        os.rename(exe, old_path)
        os.rename(new_path, exe)
        notify(f"Updated to {latest} - restarting...", 10)
        tray_message(f"Updated to {latest} - restarting...")
        state["restart_path"] = exe
        time.sleep(1.5)
        state["quit"] = True
    except Exception as e:
        print("Update check failed:", e)
        notify(f"v{VERSION} - couldn't check for updates")
        if manual:
            tray_message("Couldn't check for updates - are you online?")


# --------------------------------------------------------------- tray icon ---

def load_icon_image():
    from PIL import Image, ImageDraw
    for folder in (getattr(sys, "_MEIPASS", BASE_DIR), BASE_DIR):
        path = os.path.join(folder, "SW Key Helper.ico")
        if os.path.exists(path):
            try:
                return Image.open(path)
            except Exception:
                pass
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

    def do_update(icon, item):
        threading.Thread(target=check_for_update, args=(True,), daemon=True).start()

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
                notify(f"F9/F10 now update {name}'s inventory", 4)
            return handler

        def is_current(pid):
            return lambda item: (current_player() or {}).get("id") == pid

        player_items = [pystray.MenuItem(p["name"], pick(p["id"]), checked=is_current(p["id"]),
                                         radio=True) for p in _players["list"]]
        account_items = [
            pystray.MenuItem(f"Signed in as {_account['username']}", None, enabled=False),
        ]
        if player_items:
            account_items.append(pystray.MenuItem("Inventory player (F9/F10)",
                                                  pystray.Menu(*player_items)))
        account_items.append(pystray.MenuItem("Sign out", do_sign_out))
    menu = pystray.Menu(
        pystray.MenuItem(f"SW Key Helper v{VERSION}", None, enabled=False),
        *account_items,
        pystray.MenuItem("Check for updates", do_update),
        pystray.MenuItem("Open settings folder", open_folder),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", do_quit),
    )
    icon = pystray.Icon("SW Key Helper", load_icon_image(), f"SW Key Helper v{VERSION}", menu)
    state["tray"] = icon
    try:
        icon.run()
    except Exception as e:
        print("Tray icon failed:", e)


def cleanup_old_version():
    if getattr(sys, "frozen", False):
        old = sys.executable + ".old"
        for _ in range(10):                       # the old copy may still be closing
            try:
                if os.path.exists(old):
                    os.remove(old)
                return
            except OSError:
                time.sleep(0.5)


def main():
    print(__doc__)
    print("Version", VERSION)
    if getattr(sys, "frozen", False):
        threading.Thread(target=cleanup_old_version, daemon=True).start()
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
    threading.Thread(target=scanner, daemon=True).start()
    try:
        run_windows()
    except KeyboardInterrupt:
        pass
    state["quit"] = True
    listener.stop()
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
    main()
