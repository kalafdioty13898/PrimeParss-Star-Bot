# -*- coding: utf-8 -*-
"""
🤖 PrimePars Star Bot — ربات گیفت و استارز رایگان (رفرال محور)

اجرا:  python bot.py
راهنما: README.md

⚠️ همه‌ی متن‌های ربات در فایل config.py هستند؛ برای تغییر پیام‌ها آن فایل را ویرایش کن.
"""

import html
import os
import signal
import time

import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException

import config
import storage

# ────────────────────── وضعیت‌های موقت (حافظه) ──────────────────────
STATE = {}          # uid -> حالت فعلی (پشتیبانی، تبدیل دلخواه، ورودی‌های ادمین و...)
PENDING_CONV = {}   # uid -> تعداد رفرالی که میخواد تبدیل کند (در انتظار تایید)
PENDING_BC = {}     # uid -> (chat_id, message_id) پیام همگانی در انتظار تایید
REPLY_MAP = {}      # (admin_id, message_id) -> user_id  برای پاسخ ادمین به کاربر
BOT_USERNAME = "PrimePars_bot"

BOT = telebot.TeleBot(config.BOT_TOKEN or "123456:STUB-TOKEN", parse_mode="HTML")


# ───────────────────────────────── کیبوردها ─────────────────────────────────
def kb(rows):
    m = types.InlineKeyboardMarkup()
    for row in rows:
        m.row(*[types.InlineKeyboardButton(t, callback_data=d) for t, d in row])
    return m


def main_menu_kb():
    return kb([
        [("👥 زیرمجموعه گیری", "m:refs"), ("⭐️ استارز رایگان", "m:free")],
        [("🔄 تبدیل رفرال ها به استارز", "m:conv")],
        [("📊 تعداد رفرال های من", "m:myrefs"), ("📚 اموزش دریافت استارز", "m:tut")],
        [("💌 پشتیبانی", "m:sup")],
    ])


def back_menu_kb():
    return kb([[("🔙 بازگشت به منو", "m:back")]])


def esc(s):
    return html.escape(str(s or ""))


# ───────────────────────────────── ادمین‌ها ─────────────────────────────────
def all_admins():
    ids = set(config.ADMIN_IDS)
    owner = storage.load().get("owner")
    if owner:
        ids.add(owner)
    return sorted(ids)


def is_admin(uid):
    return uid in all_admins()


def notify_admins(text, markup=None):
    for aid in all_admins():
        try:
            BOT.send_message(aid, text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass


# ────────────────────── عضویت اجباری (فورس جوین) ──────────────────────
def needs_join(uid):
    s = storage.load()["settings"]
    if not s.get("force_join") or is_admin(uid):
        return False
    for ch in s.get("channels", []):
        try:
            member = BOT.get_chat_member(ch, uid)
            if member.status not in ("member", "administrator", "creator"):
                return True
        except Exception:
            return True
    return False


def join_kb():
    s = storage.load()["settings"]
    m = types.InlineKeyboardMarkup()
    for ch in s.get("channels", []):
        ch = str(ch)
        if ch.startswith("@"):
            m.row(types.InlineKeyboardButton(f"📢 {ch}", url="https://t.me/" + ch[1:]))
    m.row(types.InlineKeyboardButton("✅ بررسی عضویت", callback_data="fj:check"))
    return m


# ────────────────────── منو و پیام‌های عمومی ──────────────────────
def menu_text(uid):
    u = storage.get_user(uid) or {}
    return config.TEXTS["start_menu"].format(name=esc(u.get("name") or "دوست عزیز"))


def show_menu(chat_id, uid, text=None):
    BOT.send_message(chat_id, text or menu_text(uid),
                     reply_markup=main_menu_kb(), disable_web_page_preview=True)


def refresh(c, text, markup):
    """پیام قبلی را پاک می‌کند و پیام جدید می‌فرستد."""
    try:
        BOT.delete_message(c.message.chat.id, c.message.message_id)
    except Exception:
        pass
    BOT.send_message(c.message.chat.id, text, reply_markup=markup,
                     disable_web_page_preview=True)


def welcome(chat_id, uid):
    """پیام خوش‌آمد + پیام هدیه (فقط تا وقتی هدیه‌ی عضویت گرفته نشده)."""
    u = storage.get_user(uid) or {}
    name = esc(u.get("name") or "دوست عزیز")
    BOT.send_message(chat_id, config.TEXTS["start_menu"].format(name=name),
                     reply_markup=main_menu_kb(), disable_web_page_preview=True)
    if not u.get("welcome_claimed"):
        n = storage.get_setting("welcome_stars")
        BOT.send_message(chat_id, config.TEXTS["gift_new"].format(name=name, n=n),
                         reply_markup=kb([[("🎁 استارز رایگان", "m:free")]]),
                         disable_web_page_preview=True)


def credit_ref(new_uid):
    """رفرالِ در انتظار را به معرف می‌دهد (بلافاصله یا بعد از تایید عضویت)."""
    ref_id = storage.take_pending_ref(new_uid)
    if not ref_id:
        return
    per = int(storage.get_setting("ref_per_invite") or 1)
    try:
        storage.add_refs(ref_id, per)
    except Exception:
        return
    ru = storage.get_user(ref_id)
    if ru:
        try:
            BOT.send_message(int(ref_id),
                             config.TEXTS["new_ref_notify"].format(refs=ru.get("refs", 0)))
        except Exception:
            pass


def order_text(o):
    t = "🔄 تبدیل رفرال به استارز" if o["type"] == "convert" else "💸 برداشت استارز"
    uname = f"\n⚠️ یوزرنیم: @{o.get('username')}" if o.get("username") else ""
    return (f"📦 سفارش جدید | {t}\n"
            f"🎟 کد پیگیری: <code>{o['code']}</code>\n"
            f"👤 {esc(o.get('name'))} | 🆔 <code>{o['user_id']}</code>{uname}\n"
            f"🔢 تعداد: {o['amount']}\n"
            f"⭐️ استارز پرداختی: {o['stars_out']}\n"
            f"🕒 {o['time']}")


# ═════════════════════════ /start و دستورات ═════════════════════════
@BOT.message_handler(commands=["start"])
def cmd_start(m):
    uid = m.from_user.id
    name = m.from_user.first_name or ""
    username = m.from_user.username or ""
    u, created = storage.ensure_user(uid, name, username)

    # اگر ادمینی تعریف نشده باشد، اولین کاربر مالک ربات می‌شود
    if not config.ADMIN_IDS and not storage.load().get("owner"):
        storage.set_owner(uid)
        try:
            BOT.send_message(uid, "👑 شما به عنوان ادمین ربات ثبت شدید!\n"
                                  "با دستور /panel وارد پنل مدیریت شو.")
        except Exception:
            pass

    # پردازش لینک رفرال
    ref_id = None
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) > 1:
        payload = parts[1].strip()
        if payload.startswith(config.REF_PREFIX) and payload[len(config.REF_PREFIX):].isdigit():
            ref_id = int(payload[len(config.REF_PREFIX):])

    if created and ref_id and ref_id != uid and storage.get_user(ref_id) and not u.get("pending_ref"):
        u["pending_ref"] = str(ref_id)
        storage.save()
        if not storage.get_setting("force_join"):
            credit_ref(uid)  # بدون فورس جوین → رفرال بلافاصله ثبت می‌شود

    if created:
        notify_admins(config.TEXTS["new_user_admin"].format(
            name=esc(name), uid=uid,
            ref=f"<code>{ref_id}</code>" if ref_id else "—"))

    if u.get("banned") and not is_admin(uid):
        BOT.reply_to(m, config.TEXTS["banned"])
        return

    STATE.pop(uid, None)

    if needs_join(uid):
        BOT.send_message(m.chat.id, config.TEXTS["joined_needed"], reply_markup=join_kb())
        return
    welcome(m.chat.id, uid)


@BOT.message_handler(commands=["id"])
def cmd_id(m):
    BOT.reply_to(m, f"🆔 ایدی عددی شما: <code>{m.from_user.id}</code>")


# ═════════════════════════ روتر دکمه‌های شیشه‌ای ═════════════════════════
@BOT.callback_query_handler(func=lambda c: True)
def cb_router(c):
    uid = c.from_user.id
    data = c.data or ""
    u = storage.get_user(uid)

    if u and u.get("banned") and not is_admin(uid):
        try:
            BOT.answer_callback_query(c.id, config.TEXTS["banned"], show_alert=True)
        except Exception:
            pass
        return

    if data != "fj:check" and needs_join(uid):
        try:
            BOT.delete_message(c.message.chat.id, c.message.message_id)
        except Exception:
            pass
        BOT.send_message(c.message.chat.id, config.TEXTS["joined_needed"],
                         reply_markup=join_kb())
        try:
            BOT.answer_callback_query(c.id)
        except Exception:
            pass
        return

    # هر دکمه‌ای حالت قبلی را پاک می‌کند (دکمه‌های جدید در صورت نیاز دوباره ست می‌کنند)
    STATE.pop(uid, None)

    if not data.startswith(("fj:check", "bc:")):
        try:
            BOT.answer_callback_query(c.id)
        except Exception:
            pass

    try:
        route_cb(c, data)
    except ApiTelegramException as e:
        print("[callback] api error:", e)
    except Exception as e:
        print("[callback] error:", e)


def route_cb(c, data):
    uid = c.from_user.id

    if data in ("m:back", "m:home"):
        PENDING_CONV.pop(uid, None)
        PENDING_BC.pop(uid, None)
        refresh(c, menu_text(uid), main_menu_kb())

    elif data in ("m:free", "m:gift"):
        section_free(c)

    elif data == "m:refs":
        section_refs(c)

    elif data == "m:tut":
        refresh(c, config.TEXTS["tutorial"].format(
            per=storage.get_setting("ref_per_invite"),
            min=storage.get_setting("min_convert_ref"),
            wel=storage.get_setting("welcome_stars"),
            wd=storage.get_setting("min_withdraw_stars")), back_menu_kb())

    elif data == "m:myrefs":
        u = storage.get_user(uid) or {}
        refresh(c, config.TEXTS["my_refs"].format(
            uid=uid, name=esc(u.get("name")),
            refs=u.get("refs", 0), stars=u.get("stars", 0)), back_menu_kb())

    elif data == "m:sup":
        STATE[uid] = "support"
        refresh(c, config.TEXTS["support_intro"], kb([[("❌ لغو", "m:back")]]))

    elif data == "m:conv":
        section_convert(c)

    elif data in ("conv:50", "conv:100"):
        ask_convert_confirm(c, int(data.split(":")[1]))

    elif data == "conv:custom":
        STATE[uid] = "conv_custom"
        refresh(c, config.TEXTS["convert_custom"].format(
            cmin=storage.get_setting("custom_min"),
            cmax=storage.get_setting("custom_max")),
            kb([[("❌ لغو", "m:back")]]))

    elif data == "conv:yes":
        finish_convert(c)

    elif data == "conv:no":
        PENDING_CONV.pop(uid, None)
        refresh(c, menu_text(uid), main_menu_kb())

    elif data == "free:claim":
        claim_welcome(c)

    elif data == "free:wd":
        do_withdraw(c)

    elif data == "fj:check":
        if needs_join(uid):
            try:
                BOT.answer_callback_query(c.id, "هنوز عضو همه‌ی کانال‌ها نشدی! ❌",
                                          show_alert=True)
            except Exception:
                pass
        else:
            credit_ref(uid)
            try:
                BOT.delete_message(c.message.chat.id, c.message.message_id)
            except Exception:
                pass
            welcome(c.message.chat.id, uid)
            try:
                BOT.answer_callback_query(c.id, "✅ عضویت تایید شد")
            except Exception:
                pass

    elif data.split(":")[0] in ("p", "set", "u", "ord", "bc"):
        admin_route(c, data)

    else:
        refresh(c, menu_text(uid), main_menu_kb())


# ═════════════════════════ بخش‌های کاربری ═════════════════════════
def section_refs(c):
    uid = c.from_user.id
    u = storage.get_user(uid) or {}
    link = f"https://t.me/{BOT_USERNAME}?start={config.REF_PREFIX}{uid}"
    refresh(c, config.TEXTS["ref_section"].format(
        link=link, refs=u.get("refs", 0),
        per=storage.get_setting("ref_per_invite")), back_menu_kb())


def section_free(c):
    uid = c.from_user.id
    u = storage.get_user(uid) or {}
    s = storage.load()["settings"]
    if not u.get("welcome_claimed"):
        refresh(c, config.TEXTS["free_first"].format(n=s["welcome_stars"]), kb([
            [(f"🎁 دریافت {s['welcome_stars']} استارز", "free:claim")],
            [("🔙 بازگشت به منو", "m:back")],
        ]))
    elif u.get("stars", 0) >= int(s["min_withdraw_stars"]):
        refresh(c, config.TEXTS["free_balance"].format(stars=u.get("stars", 0)), kb([
            [(f"💸 برداشت {u.get('stars', 0)} استارز", "free:wd")],
            [("🔙 بازگشت به منو", "m:back")],
        ]))
    else:
        refresh(c, config.TEXTS["free_low"].format(min=s["min_withdraw_stars"]), kb([
            [("👥 زیرمجموعه گیری و دریافت رفرال", "m:refs")],
            [("🔙 بازگشت به منو", "m:back")],
        ]))


def claim_welcome(c):
    uid = c.from_user.id
    u = storage.get_user(uid)
    if not u or u.get("welcome_claimed"):
        section_free(c)
        return
    n = int(storage.get_setting("welcome_stars") or 5)
    storage.add_stars(uid, n)
    storage.mark_welcome(uid)
    # طبق مشخصات: بعد از دریافت هدیه، پیام حداقل برداشت نمایش داده می‌شود
    refresh(c, config.TEXTS["free_low"].format(
        min=storage.get_setting("min_withdraw_stars")), kb([
        [("👥 زیرمجموعه گیری و دریافت رفرال", "m:refs")],
        [("🔙 بازگشت به منو", "m:back")],
    ]))


def do_withdraw(c):
    uid = c.from_user.id
    u = storage.get_user(uid) or {}
    s = storage.load()["settings"]
    if u.get("stars", 0) < int(s["min_withdraw_stars"]):
        refresh(c, config.TEXTS["free_low"].format(min=s["min_withdraw_stars"]), kb([
            [("👥 زیرمجموعه گیری و دریافت رفرال", "m:refs")],
            [("🔙 بازگشت به منو", "m:back")],
        ]))
        return
    n = u.get("stars", 0)
    storage.add_stars(uid, -n)
    code = storage.create_order(storage.get_user(uid), "withdraw", n, n)
    refresh(c, config.TEXTS["withdraw_ok"].format(n=n, code=code), back_menu_kb())
    notify_admins(order_text(storage.get_order(code)))


def section_convert(c):
    uid = c.from_user.id
    u = storage.get_user(uid) or {}
    s = storage.load()["settings"]
    if u.get("refs", 0) < int(s["min_convert_ref"]):
        refresh(c, config.TEXTS["convert_min"].format(min=s["min_convert_ref"]),
                back_menu_kb())
        return
    refresh(c, config.TEXTS["convert_choose"].format(refs=u.get("refs", 0)), kb([
        [("50 رفرال", "conv:50"), ("100 رفرال", "conv:100")],
        [("🔢 مقدار دلخواه", "conv:custom")],
        [("🔙 بازگشت به منو", "m:back")],
    ]))


def ask_convert_confirm(c, n):
    uid = c.from_user.id
    u = storage.get_user(uid) or {}
    if u.get("refs", 0) < n:
        refresh(c, config.TEXTS["no_balance"], back_menu_kb())
        return
    PENDING_CONV[uid] = n
    stars = n * int(storage.get_setting("convert_rate") or 1)
    refresh(c, config.TEXTS["convert_confirm"].format(n=n, stars=stars),
            kb([[("✅ بله", "conv:yes"), ("❌ خیر", "conv:no")]]))


def finish_convert(c):
    uid = c.from_user.id
    n = PENDING_CONV.pop(uid, None)
    if not n:
        refresh(c, menu_text(uid), main_menu_kb())
        return
    u = storage.get_user(uid) or {}
    if u.get("refs", 0) < n:
        refresh(c, config.TEXTS["no_balance"], back_menu_kb())
        return
    rate = int(storage.get_setting("convert_rate") or 1)
    storage.add_refs(uid, -n)
    storage.add_stars(uid, n * rate)
    code = storage.create_order(storage.get_user(uid), "convert", n, n * rate)
    refresh(c, config.TEXTS["order_ok"].format(code=code), back_menu_kb())
    notify_admins(order_text(storage.get_order(code)))


# ═══════════ پاسخ ادمین به کاربر (کافیست روی پیام فورواردشده ریپلای کند) ═══════════
@BOT.message_handler(func=lambda m: is_admin(m.from_user.id)
                     and m.reply_to_message is not None
                     and (m.from_user.id, m.reply_to_message.message_id) in REPLY_MAP,
                     content_types=["text", "photo", "video", "video_note", "document",
                                    "audio", "voice", "animation", "sticker"])
def admin_reply(m):
    target = REPLY_MAP[(m.from_user.id, m.reply_to_message.message_id)]
    try:
        BOT.copy_message(target, m.chat.id, m.message_id)
        BOT.reply_to(m, "✅ برای کاربر ارسال شد")
    except Exception as e:
        BOT.reply_to(m, f"⚠️ ارسال نشد: {e}")


# ═══════════════════ روتر حالت‌ها (ورودی‌های متنی و فایل‌ها) ═══════════════════
@BOT.message_handler(func=lambda m: m.from_user.id in STATE,
                     content_types=["text", "photo", "video", "video_note", "document",
                                    "audio", "voice", "animation", "sticker"])
def state_router(m):
    uid = m.from_user.id
    st = STATE.get(uid)
    u = storage.get_user(uid)

    if u and u.get("banned") and not is_admin(uid):
        BOT.reply_to(m, config.TEXTS["banned"])
        return
    if st and not st.startswith("admin") and needs_join(uid):
        BOT.send_message(m.chat.id, config.TEXTS["joined_needed"], reply_markup=join_kb())
        return

    if st == "support":
        forward_support(m)
    elif st == "conv_custom":
        handle_custom_convert(m)
    elif st and st.startswith("admin_set:"):
        handle_admin_setting(m, st.split(":", 1)[1])
    elif st == "admin_addch":
        handle_admin_addch(m)
    elif st == "admin_uid":
        handle_admin_uid(m)
    elif st and (st.startswith("admin_stars:") or st.startswith("admin_refs:")):
        handle_admin_amount(m, st)
    elif st == "admin_bc":
        handle_admin_bc(m)
    else:
        STATE.pop(uid, None)


def forward_support(m):
    """پیام کاربر را برای همه‌ی ادمین‌ها فوروارد می‌کند."""
    uid = m.from_user.id
    u = storage.get_user(uid) or {}
    header = (f"📩 پیام پشتیبانی\n👤 {esc(u.get('name'))}"
              + (f" | @{esc(u.get('username'))}" if u.get("username") else "")
              + f"\n🆔 <code>{uid}</code>")
    ok = False
    for aid in all_admins():
        try:
            h = BOT.send_message(aid, header, disable_web_page_preview=True)
            f = BOT.forward_message(aid, m.chat.id, m.message_id)
            REPLY_MAP[(aid, h.message_id)] = uid
            REPLY_MAP[(aid, f.message_id)] = uid
            ok = True
        except Exception:
            pass
    BOT.send_message(m.chat.id,
                     config.TEXTS["support_sent"] if ok
                     else "⚠️ ارسال به پشتیبانی ممکن نشد؛ کمی بعد دوباره امتحان کن.",
                     reply_markup=kb([[("❌ لغو", "m:back")]]))


def handle_custom_convert(m):
    uid = m.from_user.id
    s = storage.load()["settings"]
    txt = (m.text or "").strip().replace(",", "").replace("٬", "")
    if not txt.lstrip("-").isdigit():
        BOT.reply_to(m, "🔢 لطفاً فقط عدد بفرست (مثال: 75)")
        return
    n = int(txt)
    if n < int(s["custom_min"]):
        BOT.reply_to(m, config.TEXTS["convert_custom_low"].format(cmin=s["custom_min"]))
        return
    if n > int(s["custom_max"]):
        BOT.reply_to(m, config.TEXTS["convert_custom_high"].format(cmax=s["custom_max"]))
        return
    u = storage.get_user(uid) or {}
    if u.get("refs", 0) < n:
        BOT.reply_to(m, config.TEXTS["no_balance"])
        return
    STATE.pop(uid, None)
    PENDING_CONV[uid] = n
    stars = n * int(s.get("convert_rate") or 1)
    BOT.send_message(m.chat.id, config.TEXTS["convert_confirm"].format(n=n, stars=stars),
                     reply_markup=kb([[("✅ بله", "conv:yes"), ("❌ خیر", "conv:no")]]))


# پیام معمولی بدون حالت → نمایش منو
@BOT.message_handler(func=lambda m: True,
                     content_types=["text", "photo", "video", "video_note", "document",
                                    "audio", "voice", "animation", "sticker"])
def fallback(m):
    uid = m.from_user.id
    u = storage.get_user(uid)
    if u and u.get("banned") and not is_admin(uid):
        BOT.reply_to(m, config.TEXTS["banned"])
        return
    if needs_join(uid):
        BOT.send_message(m.chat.id, config.TEXTS["joined_needed"], reply_markup=join_kb())
        return
    show_menu(m.chat.id, uid)


# ═════════════════════════════ پنل ادمین ═════════════════════════════
SETTING_LABELS = {
    "welcome_stars": "⭐️ استارز هدیه‌ی عضویت (استارز اول)",
    "ref_per_invite": "👥 رفرال به ازای هر زیرمجموعه‌ی موفق",
    "min_convert_ref": "🔄 حداقل رفرال برای تبدیل",
    "min_withdraw_stars": "💰 حداقل استارز برای برداشت",
    "custom_min": "🔢 حداقلِ مقدار دلخواه",
    "custom_max": "🔢 حداکثرِ مقدار دلخواه",
    "convert_rate": "💱 استارز به ازای هر رفرالِ فروخته‌شده",
}


@BOT.message_handler(commands=["panel"])
def cmd_panel(m):
    if not is_admin(m.from_user.id):
        BOT.reply_to(m, "⛔️ این بخش فقط برای ادمین ربات است.")
        return
    STATE.pop(m.from_user.id, None)
    BOT.send_message(m.chat.id, panel_text(), reply_markup=panel_kb(),
                     disable_web_page_preview=True)


def panel_text():
    st = storage.stats()
    return (f"🛡 پنل مدیریت ربات\n\n"
            f"👥 کاربران: {st['users']} | ⏳ سفارش در انتظار: {st['pending']}\n\n"
            "یکی از گزینه‌های زیر را انتخاب کن 👇")


def panel_kb():
    return kb([
        [("📊 آمار ربات", "p:stats"), ("📦 سفارش‌ها", "p:orders")],
        [("📢 پیام همگانی", "p:bc"), ("🛠 ابزار کاربران", "p:tools")],
        [("⚙️ تنظیمات", "p:settings"), ("🔒 عضویت اجباری", "p:fj")],
        [("🔙 خروج از پنل", "p:exit")],
    ])


def admin_route(c, data):
    uid = c.from_user.id
    if not is_admin(uid):
        try:
            BOT.answer_callback_query(c.id, "⛔️ فقط ادمین!", show_alert=True)
        except Exception:
            pass
        return

    if data == "p:exit":
        STATE.pop(uid, None)
        refresh(c, menu_text(uid), main_menu_kb())

    elif data == "p:main":
        refresh(c, panel_text(), panel_kb())

    elif data == "p:stats":
        st = storage.stats()
        refresh(c, ("📊 آمار ربات\n\n"
                    f"👥 کل کاربران: {st['users']}\n"
                    f"🆕 کاربران امروز: {st['today']}\n"
                    f"🚫 بن‌شده: {st['banned']}\n"
                    f"🔗 کل رفرال‌ها: {st['refs']}\n"
                    f"⭐️ استارز در کیف کاربران: {st['stars']}\n"
                    f"⏳ سفارش در انتظار: {st['pending']}\n"
                    f"✅ سفارش پرداخت‌شده: {st['paid']}"),
                kb([[("🔙 بازگشت به پنل", "p:main")]]))

    elif data == "p:orders":
        show_orders(c)

    elif data.startswith("ord:view:"):
        show_order(c, data.split(":")[2])

    elif data.startswith("ord:app:") or data.startswith("ord:rej:"):
        act, code = data.split(":")[1], data.split(":")[2]
        o = storage.get_order(code)
        if not o or o["status"] != "pending":
            try:
                BOT.answer_callback_query(c.id, "این سفارش قبلاً پردازش شده است",
                                          show_alert=True)
            except Exception:
                pass
        else:
            if act == "app":
                storage.set_order_status(code, "paid")
                try:
                    BOT.send_message(o["user_id"],
                                     f"✅ سفارش کد <code>{code}</code> تایید و پرداخت شد!\n"
                                     f"⭐️ {o['stars_out']} استارز به زودی برایت ارسال می‌شود 🎁")
                except Exception:
                    pass
            else:
                if o["type"] == "convert":
                    storage.add_refs(o["user_id"], o["amount"])
                else:
                    storage.add_stars(o["user_id"], o["stars_out"])
                storage.set_order_status(code, "rejected")
                try:
                    BOT.send_message(o["user_id"],
                                     f"❌ سفارش کد <code>{code}</code> رد شد و"
                                     " موجودی‌ات برگشت داده شد 🙏")
                except Exception:
                    pass
            show_orders(c)

    elif data == "p:bc":
        STATE[uid] = "admin_bc"
        refresh(c, "📢 پیام یا فایلی که میخوای برای «همه‌ی کاربران» ارسال بشه رو همینجا بفرست:\n\n"
                   "(برای انصراف دکمه‌ی پایین رو بزن)",
                kb([[("❌ لغو", "p:main")]]))

    elif data == "bc:go":
        do_broadcast(c)

    elif data == "bc:no":
        PENDING_BC.pop(uid, None)
        refresh(c, panel_text(), panel_kb())

    elif data == "p:tools":
        STATE[uid] = "admin_uid"
        refresh(c, "🆔 ایدی عددی کاربر مورد نظر رو بفرست:",
                kb([[("🔙 بازگشت به پنل", "p:main")]]))

    elif data.startswith("u:info:"):
        tuid = data.split(":")[2]
        u = storage.get_user(tuid)
        if u:
            refresh(c, user_card_text(u), user_card_kb(u))
        else:
            refresh(c, "❌ کاربر پیدا نشد.", kb([[("🔙 بازگشت به پنل", "p:main")]]))

    elif data.startswith("u:ban:") or data.startswith("u:unban:"):
        tuid = data.split(":")[2]
        u = storage.get_user(tuid)
        if u:
            u["banned"] = data.startswith("u:ban:")
            storage.save()
        u = storage.get_user(tuid)
        if u:
            refresh(c, user_card_text(u), user_card_kb(u))

    elif data.startswith("u:stars:") or data.startswith("u:refs:"):
        kind, tuid = data.split(":")[1], data.split(":")[2]
        STATE[uid] = f"admin_{kind}:{tuid}"
        unit = "استارز" if kind == "stars" else "رفرال"
        refresh(c, f"🔢 عدد مورد نظر برای {unit} کاربر رو بفرست:\n"
                   "(عدد مثبت = افزایش، عدد منفی = کاهش)",
                kb([[("🔙 بازگشت به پنل", "p:main")]]))

    elif data == "p:settings":
        show_settings(c)

    elif data.startswith("set:"):
        key = data.split(":", 1)[1]
        if key in SETTING_LABELS:
            STATE[uid] = f"admin_set:{key}"
            refresh(c, f"✏️ مقدار جدید برای:\n{SETTING_LABELS[key]}\n\n"
                       f"مقدار فعلی: <b>{storage.get_setting(key)}</b>",
                    kb([[("🔙 بازگشت", "p:settings")]]))

    elif data == "p:fj":
        show_force_join(c)

    elif data == "p:fj:toggle":
        s = storage.load()["settings"]
        s["force_join"] = not s.get("force_join", False)
        storage.save()
        show_force_join(c)

    elif data == "p:fj:add":
        STATE[uid] = "admin_addch"
        refresh(c, "➕ یوزرنیم کانال رو بفرست (مثال: @mychannel)\n\n"
                   "⚠️ مهم: ربات باید ادمین کانال باشه تا عضویت قابل بررسی باشه!",
                kb([[("🔙 بازگشت", "p:fj")]]))

    elif data.startswith("p:ch:del:"):
        ch = data.split(":", 3)[3]
        s = storage.load()["settings"]
        if ch in s.get("channels", []):
            s["channels"].remove(ch)
            storage.save()
        show_force_join(c)

    else:
        refresh(c, panel_text(), panel_kb())


# ─────────────────────────── زیربخش‌های پنل ادمین ───────────────────────────
def show_orders(c):
    pend = storage.pending_orders()
    if not pend:
        refresh(c, "📦 سفارش در انتظاری وجود نداره ✅",
                kb([[("🔙 بازگشت به پنل", "p:main")]]))
        return
    lines = ["📦 سفارش‌های در انتظار:\n"]
    m = types.InlineKeyboardMarkup()
    for o in pend[:20]:
        t = "🔄" if o["type"] == "convert" else "💸"
        lines.append(f"{t} کد <code>{o['code']}</code> | {esc(o.get('name'))}"
                     f" | {o['stars_out']} ⭐️")
        m.row(types.InlineKeyboardButton(
            f"🎟 {o['code']} — {o['stars_out']}⭐️",
            callback_data=f"ord:view:{o['code']}"))
    m.row(types.InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="p:main"))
    refresh(c, "\n".join(lines), m)


def show_order(c, code):
    o = storage.get_order(code)
    if not o:
        refresh(c, "❌ سفارش پیدا نشد", kb([[("🔙 بازگشت", "p:orders")]]))
        return
    t = "🔄 تبدیل رفرال" if o["type"] == "convert" else "💸 برداشت استارز"
    status = {"pending": "⏳ در انتظار", "paid": "✅ پرداخت‌شده",
              "rejected": "❌ رد‌شده"}.get(o["status"], o["status"])
    uname = f"\n⚠️ یوزرنیم: @{o.get('username')}" if o.get("username") else ""
    txt = (f"📦 سفارش {t}\n"
           f"🎟 کد: <code>{o['code']}</code>\n"
           f"👤 {esc(o.get('name'))}\n"
           f"🆔 <code>{o['user_id']}</code>{uname}\n"
           f"🔢 تعداد: {o['amount']}\n"
           f"⭐️ استارز پرداختی: {o['stars_out']}\n"
           f"📅 {o['time']}\n"
           f"وضعیت: {status}")
    rows = [[("🔙 بازگشت به سفارش‌ها", "p:orders")]]
    if o["status"] == "pending":
        rows.insert(0, [("✅ تایید", f"ord:app:{code}"), ("❌ رد", f"ord:rej:{code}")])
    refresh(c, txt, kb(rows))


def user_card_text(u):
    st = "⛔️ بن‌شده" if u.get("banned") else "✅ فعال"
    uname = f"\n⚠️ یوزرنیم: @{u.get('username')}" if u.get("username") else ""
    return (f"👤 اطلاعات کاربر\n\n"
            f"نام: {esc(u.get('name'))}\n"
            f"🆔 <code>{u['id']}</code>{uname}\n"
            f"👥 رفرال: {u.get('refs', 0)}\n"
            f"⭐️ استارز: {u.get('stars', 0)}\n"
            f"📅 عضویت: {u.get('joined')}\n"
            f"وضعیت: {st}")


def user_card_kb(u):
    ban_btn = (("✅ آنبن کاربر", f"u:unban:{u['id']}") if u.get("banned")
               else ("⛔️ بن کاربر", f"u:ban:{u['id']}"))
    return kb([
        [ban_btn],
        [("⭐️ مدیریت استارز", f"u:stars:{u['id']}"),
         ("👥 مدیریت رفرال", f"u:refs:{u['id']}")],
        [("🔙 بازگشت به پنل", "p:main")],
    ])


def show_settings(c):
    s = storage.load()["settings"]
    m = types.InlineKeyboardMarkup()
    lines = ["⚙️ تنظیمات ربات\n"]
    for key, label in SETTING_LABELS.items():
        lines.append(f"{label}: <b>{s.get(key)}</b>")
        m.row(types.InlineKeyboardButton(f"✏️ {label}", callback_data=f"set:{key}"))
    m.row(types.InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="p:main"))
    refresh(c, "\n".join(lines), m)


def show_force_join(c):
    s = storage.load()["settings"]
    on = "🟢 روشن" if s.get("force_join") else "🔴 خاموش"
    m = types.InlineKeyboardMarkup()
    m.row(types.InlineKeyboardButton(f"🔒 عضویت اجباری: {on}", callback_data="p:fj:toggle"))
    for ch in s.get("channels", []):
        m.row(types.InlineKeyboardButton(f"🗑 حذف {ch}", callback_data=f"p:ch:del:{ch}"))
    m.row(types.InlineKeyboardButton("➕ افزودن کانال", callback_data="p:fj:add"))
    m.row(types.InlineKeyboardButton("🔙 بازگشت به پنل", callback_data="p:main"))
    txt = ("🔒 عضویت اجباری\n\n"
           f"وضعیت: {on}\n\n"
           "کانال‌های اجباری: " + (", ".join(s.get("channels", [])) or "—") + "\n\n"
           "ℹ️ وقتی روشن باشد، کاربر باید در همه‌ی کانال‌ها عضو شود تا به ربات دسترسی داشته باشد"
           " و رفرال‌ها فقط بعد از تایید عضویت ثبت می‌شوند (ضد تقلب).")
    refresh(c, txt, m)


def do_broadcast(c):
    uid = c.from_user.id
    src = PENDING_BC.pop(uid, None)
    if not src:
        refresh(c, panel_text(), panel_kb())
        return
    users = list(storage.all_users().values())
    ok = fail = 0
    for u in users:
        if u.get("banned"):
            continue
        try:
            BOT.copy_message(u["id"], src[0], src[1])
            ok += 1
        except Exception:
            fail += 1
        time.sleep(0.05)
    refresh(c, f"📢 پیام همگانی ارسال شد ✅\n\nارسال‌شده: {ok}\nناموفق: {fail}",
            kb([[("🔙 بازگشت به پنل", "p:main")]]))


def handle_admin_setting(m, key):
    uid = m.from_user.id
    if key not in SETTING_LABELS:
        STATE.pop(uid, None)
        return
    txt = (m.text or "").strip().replace(",", "").replace("٬", "")
    if not txt.isdigit():
        BOT.reply_to(m, "🔢 لطفاً یک عدد مثبت بفرست:")
        return
    val = int(txt)
    storage.set_setting(key, val)
    STATE.pop(uid, None)
    BOT.send_message(m.chat.id, f"✅ ذخیره شد!\n{SETTING_LABELS[key]}: <b>{val}</b>",
                     reply_markup=panel_kb())


def handle_admin_addch(m):
    uid = m.from_user.id
    ch = (m.text or "").strip()
    if not ch.startswith("@") or len(ch) < 5:
        BOT.reply_to(m, "فرمت درست: @username کانال")
        return
    try:
        BOT.get_chat(ch)
    except Exception:
        BOT.reply_to(m, "⚠️ نتونستم کانال رو پیدا کنم؛ مطمئن شو یوزرنیم درسته"
                        " و ربات ادمین کانال هست.")
        return
    s = storage.load()["settings"]
    if ch not in s.setdefault("channels", []):
        s["channels"].append(ch)
        storage.save()
    STATE.pop(uid, None)
    BOT.send_message(m.chat.id, f"✅ کانال {ch} اضافه شد.", reply_markup=panel_kb())


def handle_admin_uid(m):
    uid = m.from_user.id
    txt = (m.text or "").strip()
    if not txt.lstrip("-").isdigit():
        BOT.reply_to(m, "🆔 لطفاً ایدی عددی معتبر بفرست:")
        return
    STATE.pop(uid, None)
    u = storage.get_user(txt)
    if not u:
        BOT.send_message(m.chat.id, "❌ کاربر پیدا نشد (باید اول ربات رو استارت کرده باشه)",
                         reply_markup=panel_kb())
        return
    BOT.send_message(m.chat.id, user_card_text(u), reply_markup=user_card_kb(u))


def handle_admin_amount(m, st):
    uid = m.from_user.id
    _, kind, tuid = st.split(":", 2)
    txt = (m.text or "").strip().replace(",", "").replace("٬", "")
    if not txt.lstrip("-").isdigit():
        BOT.reply_to(m, "🔢 عدد معتبر بفرست (مثال: 50 یا -20)")
        return
    n = int(txt)
    u = storage.get_user(tuid)
    if not u:
        STATE.pop(uid, None)
        BOT.send_message(m.chat.id, "❌ کاربر پیدا نشد", reply_markup=panel_kb())
        return
    if kind == "stars":
        storage.add_stars(tuid, n)
    else:
        storage.add_refs(tuid, n)
    STATE.pop(uid, None)
    u = storage.get_user(tuid)
    BOT.send_message(m.chat.id, f"✅ انجام شد! ({'+' if n >= 0 else ''}{n})")
    BOT.send_message(m.chat.id, user_card_text(u), reply_markup=user_card_kb(u))


def handle_admin_bc(m):
    uid = m.from_user.id
    PENDING_BC[uid] = (m.chat.id, m.message_id)
    STATE.pop(uid, None)
    BOT.send_message(m.chat.id, "👀 پیامت ذخیره شد (همون پیام بالا ☝️)\n"
                                "برای همه‌ی کاربرا ارسال بشه؟",
                     reply_markup=kb([[("📤 ارسال به همه", "bc:go"), ("❌ لغو", "bc:no")]]))


# ═════════════════════════════ اجرای ربات ═════════════════════════════
def _shutdown(signum, frame):
    print("⏳ در حال ذخیره اطلاعات...")
    try:
        storage.save()
        storage.git_commit()
    except Exception:
        pass
    os._exit(0)


def main():
    global BOT_USERNAME
    if not config.BOT_TOKEN:
        print("❌ توکن ربات پیدا نشد! متغیر محیطی BOT_TOKEN را تنظیم کن"
              " (روی گیت‌هاب: Settings → Secrets → BOT_TOKEN)")
        return
    try:
        me = BOT.get_me()
        BOT_USERNAME = me.username
        print(f"🤖 ربات @{BOT_USERNAME} روشن شد ✅")
    except ApiTelegramException as e:
        print("❌ توکن نامعتبر است:", e)
        return
    storage.start_autosave()
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    BOT.infinity_polling(timeout=30, long_polling_timeout=30)


if __name__ == "__main__":
    main()
