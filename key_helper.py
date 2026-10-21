"""
SW Key Helper - two helpers for the Droid Tycoon map:
  Fishing  finds the yellow key tiles, reads them and presses them.
  Traits   keeps rerolling the trait on your first (left) droid until it gets
           Overclocked or Chip Finder. Stand at the trait scanner first.

Hotkeys (work while the game is focused):
  Insert      open the mode menu / start the chosen mode / stop
  Up / Down   pick Fishing or Traits while the menu is open
              (Traits then shows the trait list: Up/Down move, Left/Right sets
              Off / Any / Rainbow-only, Insert starts rolling)
  Page Up     faster  (less delay between presses)
  Page Down   slower  (more delay between presses)
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
VERSION = "1.0.3"
# Your GitHub "username/repository" where the .exe releases are published.
UPDATE_REPO = "sclawson21/sw-key-helper"

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
    "interact_key": "f",
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
    "menu_stage": "mode", "pick_index": 0,
}
MODES = ["fishing", "traits"]

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
            if state["mode"] == "traits":         # traits -> show the trait picker first
                state["menu_stage"] = "traits"
                state["menu_time"] = time.time()
            else:
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
        else:
            state["pick_index"] = (state["pick_index"] + step) % len(TRAIT_NAMES)
    elif key in (K.left, K.right) and state["menu_open"] and state["menu_stage"] == "traits":
        state["menu_time"] = time.time()
        name = TRAIT_NAMES[state["pick_index"]]
        cur = cfg["trait_wanted"].get(name, "off")
        step = 1 if key == K.right else -1
        cfg["trait_wanted"][name] = CHOICES[(CHOICES.index(cur) + step) % len(CHOICES)]
    elif key in (K.page_up, K.page_down):
        step = -DELAY_STEP if key == K.page_up else DELAY_STEP
        cfg["press_delay"] = round(min(DELAY_MAX, max(DELAY_MIN, cfg["press_delay"] + step)), 3)
        save_config()
        notify(f"Delay between presses: {int(cfg['press_delay'] * 1000)} ms")
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


def find_menu(bgr):
    """('select', left SELECT button) or ('panel', credit button) or None."""
    buttons = yellow_buttons(bgr)
    guide = [b for b in buttons if fuzzy_contains(b["text"], "GUIDE", 0.75)]
    if guide:
        g = guide[0]
        above = [b for b in buttons if b["y"] < g["y"] and abs(b["cx"] - g["cx"]) < g["w"] * 0.3]
        if above:
            return "panel", max(above, key=lambda b: b["y"])
    selects = [b for b in buttons if fuzzy_contains(b["text"], "SELECT", 0.75)]
    if selects:
        return "select", min(selects, key=lambda b: b["x"])
    return None


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


def wait_for_menu(sct, want, timeout):
    """Wait until the given menu ('select' / 'panel' / either) shows up."""
    end = time.time() + timeout
    while time.time() < end and traits_active():
        found = find_menu(grab_screen(sct))
        if found and (want == "either" or found[0] == want):
            return found
        time.sleep(0.15)
    return None


def stop_traits(msg):
    state["enabled"] = False
    state["status"] = msg
    notify(msg, 30)


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

            # 2. Pick the left droid
            if found[0] == "select":
                state["status"] = "Selecting left droid"
                if not traits_wait(0.4):          # let the menu finish its pop-in
                    return
                settled = wait_for_menu(sct, "select", 1.5) or found
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
            texts, rainbow, name = [], False, ""
            for _ in range(5):                    # a few tries if the text is hard to read
                img = grab_screen(sct)
                box = (0.2, 0.56, 0.5, 0.70)
                texts += [white_text(img, box), white_text(img, box, any_bright=True)]
                rainbow = rainbow or rainbow_trait(img)
                name = identify_trait(texts)
                if name or not traits_active():
                    break
                time.sleep(0.3)
            tier = " (rainbow)" if rainbow else ""
            state["trait"] = (name + tier) if name else (
                (max(texts, key=len)[:30] + tier) if any(texts) else "(none yet)")
            if name and is_wanted(name, rainbow):
                stop_traits(f"GOT {name.upper()}{tier.upper()}! "
                            f"Stopped after {state['rolls']} rolls.")
                return
            if not name and cfg["stop_if_unreadable"] and could_be_wanted(rainbow):
                stop_traits("Couldn't read this trait and it might be one you want - "
                            "stopped to be safe. Check it!")
                return
            if cfg["max_rolls"] and state["rolls"] >= cfg["max_rolls"]:
                stop_traits(f"Reached max_rolls ({cfg['max_rolls']}) - stopped.")
                return

            # 4. Click the credit button and make sure the roll started
            panel = wait_for_menu(sct, "panel", 1.5)
            if not panel:
                stop_traits("Lost the trait panel before rolling")
                return
            state["status"] = f"Rolling (#{state['rolls'] + 1})"
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

            # 5. Wait for the roll animation, then go again
            state["status"] = f"Waiting for roll #{state['rolls']}"
            if not traits_wait(cfg["roll_wait"]):
                return
    except pytesseract.TesseractNotFoundError:
        stop_traits("Tesseract not found - check tesseract_path in config.json")
    except Exception as e:
        stop_traits(f"Traits error: {e}")


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
        timeout = 10 if state["menu_stage"] == "mode" else 60
        if state["menu_open"] and time.time() - state["menu_time"] > timeout:
            state["menu_open"] = False            # menu times out if nothing is picked
        mode = state["mode"].upper()
        picking = state["menu_open"] and state["menu_stage"] == "traits"
        if picking and not picker.winfo_ismapped():
            picker.pack(after=title, fill="x", pady=(4, 2))
        elif not picking and picker.winfo_ismapped():
            picker.pack_forget()
        if picking:
            title.config(text="TRAITS \u2013 STOP ON WHICH?", fg=FG)
            badge = {"off": "  \u00b7  ", "any": " ANY ", "rainbow": "RAINBOW"}
            lines = []
            for i, n in enumerate(TRAIT_NAMES):
                mark = "\u25b6" if i == state["pick_index"] else " "
                lines.append(f"{mark} {n:<21}[{badge[cfg['trait_wanted'].get(n, 'off')]:^7}]")
            picker.config(text="\n".join(lines))
            reading.config(text="")
            pressed.config(text="")
            stats.config(text="")
            hints.config(text="Up/Down move \u00b7 Left/Right Off/Any/Rainbow \u00b7 Insert start")
        elif state["menu_open"]:
            title.config(text="SW KEY HELPER \u2013 CHOOSE MODE", fg=FG)
            names = ["Fishing", "Traits (reroll left droid)"]
            reading.config(text=("\u25b6 " if state["menu_choice"] == 0 else "    ") + names[0])
            pressed.config(text=("\u25b6 " if state["menu_choice"] == 1 else "    ") + names[1])
            stats.config(text="")
            hints.config(text="Up/Down choose \u00b7 Insert start")
        else:
            if state["error"]:
                title.config(text="SW KEY HELPER \u2013 PROBLEM", fg=RED)
            elif state["enabled"]:
                title.config(text=f"{mode} \u2013 ACTIVE", fg=GREEN)
            else:
                title.config(text="SW KEY HELPER \u2013 STOPPED", fg=AMBER)
            if state["mode"] == "traits":
                reading.config(text=f"Trait: {state['trait'] or DASH}")
                pressed.config(text=f"Rolls: {state['rolls']} \u00b7 {state['status'] or DASH}")
                stats.config(text="Stops on: " + wanted_summary(), wraplength=420)
            else:
                reading.config(text=f"Reading: {state['reading'] or DASH}")
                pressed.config(text=f"Pressed: {state['pressed'] or DASH}")
                area = "custom" if cfg["search_area"] else "auto"
                stats.config(text=f"Scan {state['scan_ms']} ms \u00b7 "
                                  f"Delay {int(cfg['press_delay'] * 1000)} ms \u00b7 "
                                  f"Inputs {state['inputs']} \u00b7 Area {area}")
            hints.config(text="Insert menu/start/stop \u00b7 PgUp/PgDn faster/slower\n"
                              "Home set area \u00b7 Delete debug pics \u00b7 End quit")
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

    menu = pystray.Menu(
        pystray.MenuItem(f"SW Key Helper v{VERSION}", None, enabled=False),
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
    print("Bye.")


if __name__ == "__main__":
    main()
