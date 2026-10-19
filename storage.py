# -*- coding: utf-8 -*-
"""
💾 ذخیره‌سازی اطلاعات ربات (فایل data.json)
+ آپلود خودکار data.json روی خود گیت‌هاب تا با ری‌استارت شدن ربات چیزی از دست نره.
"""

import json
import os
import random
import subprocess
import threading
import time
from datetime import datetime

import config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data.json")

_lock = threading.RLock()
_data = None
_dirty = False


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _default():
    return {
        "owner": None,
        "settings": dict(config.DEFAULT_SETTINGS),
        "users": {},
        "orders": {},
    }


def load():
    global _data
    with _lock:
        if _data is None:
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    _data = json.load(f)
            except Exception:
                _data = None
            # 🛡 فایل خراب / خالی / null بود؟ خودش را ترمیم کن
            if not isinstance(_data, dict):
                _data = _default()
            for k, v in _default().items():
                if not isinstance(_data.get(k), type(v)):
                    _data[k] = v
            for k, v in config.DEFAULT_SETTINGS.items():
                if k not in _data["settings"]:
                    _data["settings"][k] = v
            if not isinstance(_data["settings"].get("channels"), list):
                _data["settings"]["channels"] = []
        return _data


def save():
    global _dirty
    with _lock:
        if _data is None:  # 🛡 قبل از لود شدن هیچی، فایل را خراب نکن!
            return
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)
        _dirty = True


# ───────────────────── آپلود خودکار data.json روی گیت‌هاب ─────────────────────

def _git_ready():
    return bool(os.environ.get("GITHUB_TOKEN")) and bool(os.environ.get("GITHUB_REPOSITORY"))


def git_commit():
    """اگر data.json عوض شده باشد، آن را داخل خود ریپو کامیت و پوش می‌کند."""
    global _dirty
    with _lock:
        if not _dirty or not _git_ready():
            return
    try:
        token = os.environ["GITHUB_TOKEN"]
        repo = os.environ["GITHUB_REPOSITORY"]
        branch = os.environ.get("GITHUB_REF_NAME") or "main"
        url = f"https://x-access-token:{token}@github.com/{repo}.git"

        def run(*cmd):
            subprocess.run(list(cmd), cwd=BASE_DIR, check=True,
                           timeout=120, capture_output=True)

        run("git", "config", "user.name", "star-bot-data")
        run("git", "config", "user.email", "bot@users.noreply.github.com")
        run("git", "add", "data.json")
        chk = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=BASE_DIR)
        if chk.returncode == 0:  # چیزی عوض نشده بود
            with _lock:
                _dirty = False
            return
        run("git", "commit", "-m", f"🔄 update data.json | {now_str()}")
        try:
            run("git", "pull", "--rebase", url, branch)
        except Exception:
            pass
        run("git", "push", url, f"HEAD:refs/heads/{branch}")
        with _lock:
            _dirty = False
        print("[storage] ✅ data.json روی گیت‌هاب ذخیره شد")
    except Exception as e:
        with _lock:
            _dirty = True
        print("[storage] git commit failed:", e)


def _autosave_loop():
    while True:
        time.sleep(120)
        try:
            save()
            git_commit()
        except Exception:
            pass


def start_autosave():
    """هر ۲ دقیقه اگر اطلاعات عوض شده باشد، روی گیت‌هاب ذخیره می‌کند."""
    threading.Thread(target=_autosave_loop, daemon=True).start()


# ───────────────────────────────── کاربران ─────────────────────────────────

def ensure_user(uid, name="", username=""):
    d = load()
    key = str(uid)
    created = key not in d["users"]
    if created:
        d["users"][key] = {
            "id": int(uid),
            "name": name or "",
            "username": username or "",
            "refs": 0,
            "stars": 0,
            "welcome_claimed": False,
            "pending_ref": None,
            "joined": now_str(),
            "banned": False,
        }
    else:
        if name:
            d["users"][key]["name"] = name
        d["users"][key]["username"] = username or d["users"][key].get("username", "")
    save()
    return d["users"][key], created


def get_user(uid):
    return load()["users"].get(str(uid))


def all_users():
    return load()["users"]


def set_owner(uid):
    d = load()
    if not d.get("owner"):
        d["owner"] = int(uid)
        save()


def add_refs(uid, n):
    u = load()["users"][str(uid)]
    u["refs"] = max(0, int(u.get("refs", 0)) + n)
    save()


def add_stars(uid, n):
    u = load()["users"][str(uid)]
    u["stars"] = max(0, int(u.get("stars", 0)) + n)
    save()


def take_pending_ref(uid):
    u = load()["users"][str(uid)]
    ref = u.get("pending_ref")
    u["pending_ref"] = None
    save()
    return ref


def mark_welcome(uid):
    load()["users"][str(uid)]["welcome_claimed"] = True
    save()


# ───────────────────────────────── تنظیمات ─────────────────────────────────

def set_setting(key, value):
    load()["settings"][key] = value
    save()


def get_setting(key):
    return load()["settings"].get(key, config.DEFAULT_SETTINGS.get(key))


# ───────────────────────────────── سفارش‌ها ─────────────────────────────────

def create_order(user, otype, amount, stars_out):
    d = load()
    while True:
        code = str(random.randint(1000, 9999))
        if code not in d["orders"]:
            break
    d["orders"][code] = {
        "code": code,
        "user_id": user["id"],
        "name": user.get("name", ""),
        "username": user.get("username", ""),
        "type": otype,          # convert = تبدیل رفرال | withdraw = برداشت استارز
        "amount": amount,
        "stars_out": stars_out,
        "status": "pending",    # pending | paid | rejected
        "time": now_str(),
    }
    save()
    return code


def get_order(code):
    return load()["orders"].get(str(code))


def set_order_status(code, status):
    load()["orders"][str(code)]["status"] = status
    save()


def pending_orders():
    d = load()["orders"]
    return sorted([o for o in d.values() if o["status"] == "pending"],
                  key=lambda o: o["time"])


# ───────────────────────────────── آمار ─────────────────────────────────

def stats():
    d = load()
    users = list(d["users"].values())
    today = now_str()[:10]
    return {
        "users": len(users),
        "today": sum(1 for u in users if str(u.get("joined", "")).startswith(today)),
        "refs": sum(int(u.get("refs", 0)) for u in users),
        "stars": sum(int(u.get("stars", 0)) for u in users),
        "banned": sum(1 for u in users if u.get("banned")),
        "pending": sum(1 for o in d["orders"].values() if o["status"] == "pending"),
        "paid": sum(1 for o in d["orders"].values() if o["status"] == "paid"),
    }
