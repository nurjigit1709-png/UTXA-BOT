import telebot
from telebot import types
import json
import os
import time
import re
from typing import Optional

from keep_alive import keep_alive
# =========================
# 🎨 COLORED INLINE BUTTONS (Telegram Bot API 9.4+)
# В обычном telebot InlineKeyboardButton нет параметра style,
# поэтому мы "переводим" InlineKeyboardMarkup -> dict и добавляем поле style.
# Стиль виден только у пользователей с обновлённым Telegram.
# =========================

def _guess_btn_style(btn_text: str, cb_data: str = "", url: str = "") -> Optional[str]:
    """Heuristic mapping for button colors.
    Returns one of: 'primary' (blue), 'success' (green), 'danger' (red), 'default' (grey), or None.
    """
    t = (btn_text or "").strip().lower()
    d = (cb_data or "").strip().lower()

    # --- SPECIAL CASES (игры) ---

    # 🎲 Рулетка: диапазоны 1-3 / 4-6 / 7-9 / 10-12
    if d.startswith("range_") or re.fullmatch(r"\d+\s*[-–]\s*\d+", t or ""):
        return "primary"

    # 🎲 Рулетка: ставки 1к на цвет
    # bet_1000_k (красное), bet_1000_ch (черное), bet_1000_z (зеленое)
    if d.startswith("bet_1000_") or "1к на" in t:
        if "k" in d or "🔴" in (btn_text or "") or "крас" in t:
            return "danger"
        if "_z" in d or "💚" in (btn_text or "") or "зел" in t:
            return "success"
        # чёрное / ⚫️
        return "default"

    # 🪙 Орёл/Решка: выбор стороны
    if d.startswith("coin_pick|"):
        # coin_pick|gid|o / coin_pick|gid|r
        if d.endswith("|o") or "орёл" in t:
            return "primary"
        if d.endswith("|r") or "решка" in t:
            return "success"
        return "primary"

    # --- GENERIC RULES ---

    # Красные: отмена/нет/удаление/закрыть
    red_words = ["отмена", "отменить", "назад", "закрыть", "❌", "нет", "удал", "стоп", "выход",
                 "cancel", "decl", "no", "back"]
    if any(w in t for w in red_words) or any(w in d for w in ["cancel", "decl", "back", "off", "stop", "close", "delete"]):
        return "danger"

    # Зелёные: принять/да/получить/подтвердить/купить/открыть
    green_words = ["✅", "да", "принять", "подтверд", "получить", "забрать", "купить", "открыть", "start", "go", "ok"]
    if any(w in t for w in green_words) or any(w in d for w in ["acc", "yes", "ok", "buy", "open", "start", "accept"]):
        return "success"

    # Синие: основные действия / меню / управление
    blue_words = ["меню", "помощь", "профиль", "топ", "донат", "магазин", "банк", "крутить", "играть",
                  "повторить", "удвоить", "spin", "help", "menu"]
    if any(w in t for w in blue_words) or any(w in d for w in ["spin", "repeat", "double", "double_all", "menu", "help"]):
        return "primary"

    # Для url-кнопок часто красиво сделать primary
    if url:
        return "primary"

    return "default"



def _inline_markup_to_styled_dict(markup: 'types.InlineKeyboardMarkup') -> dict:
    """Convert telebot InlineKeyboardMarkup to dict and inject 'style' into every button."""
    try:
        kb = []
        for row in getattr(markup, "keyboard", []) or []:
            row_out = []
            for btn in row:
                try:
                    # telebot button -> dict
                    b = btn.to_dict() if hasattr(btn, "to_dict") else dict(btn)
                except Exception:
                    b = {}
                txt = b.get("text", "")
                cb = b.get("callback_data", "") or ""
                url = b.get("url", "") or ""
                b["style"] = _guess_btn_style(txt, cb, url) or "default"
                row_out.append(b)
            kb.append(row_out)
        return {"inline_keyboard": kb}
    except Exception:
        # если что-то пошло не так — вернём как есть
        try:
            return markup.to_dict()
        except Exception:
            return {"inline_keyboard": []}


def _styled(reply_markup):
    """If markup is InlineKeyboardMarkup -> return JSON string with styles, else passthrough.

    IMPORTANT for pyTelegramBotAPI (telebot):
    - send_message expects reply_markup to be a telebot markup object OR a JSON string.
    - Passing a raw dict may break serialization and cause:
      'Bad Request: can't parse reply keyboard markup JSON object'
    """
    try:
        # Native InlineKeyboardMarkup -> convert to styled dict then JSON-encode
        if isinstance(reply_markup, types.InlineKeyboardMarkup):
            d = _inline_markup_to_styled_dict(reply_markup)
            return json.dumps(d, ensure_ascii=False)
        # Already dict-like
        if isinstance(reply_markup, dict) and "inline_keyboard" in reply_markup:
            return json.dumps(reply_markup, ensure_ascii=False)
        # Already JSON string
        if isinstance(reply_markup, str):
            return reply_markup
    except Exception:
        # If anything goes wrong, don't touch markup
        return reply_markup
    return reply_markup


# ====== STARTUP GUARD (игнорируем сообщения, отправленные когда бот был выключен) ======
BOT_START_UNIX = int(time.time())


# --- Anti-spam cooldown for bot commands (2.5s) ---
_BOT_CMD_COOLDOWN = 2.5
_bot_last_cmd_ts = {}  # (chat_id,user_id) -> ts

def _bot_cd_ok(message) -> bool:
    """Cooldown only for messages directed to the bot (commands/buttons).
    Также игнорируем сообщения, которые были отправлены, пока бот был выключен.
    """
    try:
        # Игнорируем старые апдейты (когда бот был оффлайн)
        try:
            if hasattr(message, 'date') and int(getattr(message, 'date', 0) or 0) < int(BOT_START_UNIX):
                return False
        except Exception:
            pass

        key = (str(message.chat.id), str(message.from_user.id))
        now = time.time()
        last = float(_bot_last_cmd_ts.get(key, 0))
        diff = now - last
        if diff < _BOT_CMD_COOLDOWN:
            left = _BOT_CMD_COOLDOWN - diff
            name = getattr(message.from_user, 'first_name', 'Игрок')
            bot.reply_to(message, f"<a href='tg://user?id={message.from_user.id}'>{safe_html(name)}</a>, вы не можете использовать бота ещё {left:.3f} секунды", parse_mode="HTML")
            return False
        _bot_last_cmd_ts[key] = now
        return True
    except Exception:
        return True


def _cb_is_fresh(call) -> bool:
    """Игнорируем старые callback-и, пришедшие после простоя бота."""
    try:
        msg = getattr(call, 'message', None)
        if msg and hasattr(msg, 'date') and int(getattr(msg, 'date', 0) or 0) < int(BOT_START_UNIX):
            return False
    except Exception:
        pass
    return True

import threading
import random
import re

history_data = {}  # user_id: list of {time, event, change}

chat_participants = {}  # chat_id: {user_id: name}
chat_titles = {}  # chat_id: chat title (for !бот ищи)

# =========================
# 💾 PERSIST chat participants/titles (so TOP works after restart)
# =========================
CHAT_PARTICIPANTS_FILE = 'chat_participants_cache.json'
_chat_participants_lock = threading.Lock()

def _load_chat_participants_cache():
    try:
        if os.path.exists(CHAT_PARTICIPANTS_FILE) and os.path.getsize(CHAT_PARTICIPANTS_FILE) > 0:
            with open(CHAT_PARTICIPANTS_FILE, 'r', encoding='utf-8') as f:
                d = json.load(f)
                if isinstance(d, dict):
                    return d
    except Exception:
        pass
    return {"participants": {}, "titles": {}}

def _save_chat_participants_cache(participants: dict, titles: dict):
    tmp = CHAT_PARTICIPANTS_FILE + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({"participants": participants, "titles": titles}, f, ensure_ascii=False, indent=2)
        import shutil as _sh
        _sh.move(tmp, CHAT_PARTICIPANTS_FILE)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

# Загружаем кэш на старте
try:
    _cache = _load_chat_participants_cache()
    if isinstance(_cache, dict):
        chat_participants = _cache.get("participants") or {}
        chat_titles = _cache.get("titles") or {}
except Exception:
    pass

# Debounce сохранения, чтобы не писать на диск на каждое сообщение
_chat_cache_dirty = False
_chat_cache_last_save = 0.0

def _mark_chat_cache_dirty():
    global _chat_cache_dirty, _chat_cache_last_save
    try:
        _chat_cache_dirty = True
        now_ts = time.time()
        # сохраняем не чаще 1 раза в 20 секунд
        if now_ts - float(_chat_cache_last_save or 0) >= 20:
            with _chat_participants_lock:
                _save_chat_participants_cache(chat_participants, chat_titles)
            _chat_cache_dirty = False
            _chat_cache_last_save = now_ts
    except Exception:
        pass


from collections import deque

# =========================
# =========================
# 🔥 FIRE: хранение последних 100 сообщений
# =========================

_recent_messages = {}  # chat_id(str) -> deque([user_id(int), ...], maxlen=100)

def _track_recent_message(chat_id: int, user_id: int):
    """Запоминаем автора каждого сообщения (последние 100)"""
    try:
        cid = str(chat_id)

        if cid not in _recent_messages:
            _recent_messages[cid] = deque(maxlen=100)

        _recent_messages[cid].append(int(user_id))
    except Exception:
        pass

# =========================
# 📊 СТАТИСТИКА СООБЩЕНИЙ (24 часа) — по чатам
# Команда: !стата
# =========================
MESSAGE_STATS_FILE = 'message_stats_24h.json'
_message_stats_lock = threading.Lock()

def _load_message_stats():
    try:
        if os.path.exists(MESSAGE_STATS_FILE) and os.path.getsize(MESSAGE_STATS_FILE) > 0:
            with open(MESSAGE_STATS_FILE, 'r', encoding='utf-8') as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}

def _save_message_stats(d):
    tmp = MESSAGE_STATS_FILE + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=4)
        import shutil as _sh
        _sh.move(tmp, MESSAGE_STATS_FILE)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

def _msg_bucket(ts: float) -> str:
    # часовой бакет (unix_ts_hours)
    return str(int(ts // 3600) * 3600)

def _cleanup_old_buckets(user_buckets: dict, now_ts: float):
    # держим 30 часов, чтобы точно покрыть последние 24 часа (с запасом)
    cutoff = int((now_ts - 30 * 3600) // 3600) * 3600
    dead = []
    for b in user_buckets.keys():
        try:
            if int(b) < cutoff:
                dead.append(b)
        except Exception:
            dead.append(b)
    for b in dead:
        user_buckets.pop(b, None)

def inc_message_stat(chat_id: int, user_id: int, first_name: str):
    if not chat_id or not user_id:
        return
    now_ts = time.time()
    cid = str(chat_id)
    uid = str(user_id)
    bucket = _msg_bucket(now_ts)

    with _message_stats_lock:
        d = _load_message_stats()
        if cid not in d:
            d[cid] = {}
        if uid not in d[cid]:
            d[cid][uid] = {'name': str(first_name), 'buckets': {}}

        d[cid][uid]['name'] = str(first_name)  # обновляем имя
        buckets = d[cid][uid].get('buckets', {})
        if not isinstance(buckets, dict):
            buckets = {}
        buckets[bucket] = int(buckets.get(bucket, 0)) + 1

        _cleanup_old_buckets(buckets, now_ts)
        d[cid][uid]['buckets'] = buckets
        _save_message_stats(d)

def top_message_stats_24h(chat_id: int, limit: int = 10):
    now_ts = time.time()
    cid = str(chat_id)
    start_cut = now_ts - 24 * 3600

    with _message_stats_lock:
        d = _load_message_stats()

    items = []
    chat_map = d.get(cid, {})
    for uid, info in (chat_map or {}).items():
        try:
            name = info.get('name', f'Юзер {uid}')
            buckets = info.get('buckets', {})
            total = 0
            for b, c in (buckets or {}).items():
                try:
                    ts = int(b)
                    if ts >= start_cut:
                        total += int(c)
                except Exception:
                    continue
            if total > 0:
                items.append((uid, name, total))
        except Exception:
            continue

    items.sort(key=lambda x: x[2], reverse=True)
    return items[:limit]




last_user_bets = {}

import shutil  # Добавь в начало файла, если нет


def save_data(data):
    """Безопасное сохранение балансов:
    - пишем во временный файл
    - атомарно подменяем основной
    - держим бэкап .bak
    - защищаем от одновременных записей потоками
    """
    tmp_file = DATA_FILE + '.tmp'
    # Защита от случайного "обнуления" базы (например, если load_data() вернул {} из-за сбоя чтения)
    if isinstance(data, dict) and len(data) == 0 and os.path.exists(DATA_FILE) and os.path.exists(BACKUP_FILE) and os.path.getsize(BACKUP_FILE) > 0:
        print("⚠️ Сохранение отменено: попытка записать пустую базу при наличии бэкапа.")
        return
    with _DATA_LOCK:
        try:
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)

            # атомарная замена
            shutil.move(tmp_file, DATA_FILE)

            # бэкап последней успешной версии
            try:
                shutil.copyfile(DATA_FILE, BACKUP_FILE)
            except Exception:
                pass

        except Exception as e:
            print(f"Критическая ошибка при сохранении балансов: {e}")
            # если tmp остался — пробуем убрать
            try:
                if os.path.exists(tmp_file):
                    os.remove(tmp_file)
            except Exception:
                pass

def format_balance(amount: int) -> str:
    return f"{amount:,}".replace(",", " ")
def parse_human_amount(raw: str) -> int:
    """Парсит суммы вида 5000000 / 5к / 5кк / 2м / 2млн. Возвращает int или 0."""
    try:
        s = (raw or "").strip().lower().replace(" ", "")
        if not s:
            return 0

        mult = 1
        # поддержка 'млн'
        if s.endswith("млн"):
            mult = 1_000_000
            s = s[:-3]
        elif s.endswith(("kk", "кк")):
            mult = 1_000_000
            s = s[:-2]
        elif s.endswith(("k", "к")):
            mult = 1_000
            s = s[:-1]
        elif s.endswith(("m", "м")):
            mult = 1_000_000
            s = s[:-1]

        if not s.isdigit():
            return 0
        return int(s) * mult
    except Exception:
        return 0



def track_user_in_chat(chat_id, user_id, name, username: str = "", chat_title: str = ""):
    """Запоминаем, кто встречался в каком чате.
    Нужно для команды !бот ищи и для свадьбы (поиск @username в пределах чата).
    Храним: name + username. Совместимо со старым форматом (где было только имя строкой).
    """
    cid = str(chat_id)
    uid = str(user_id)
    if cid not in chat_participants:
        chat_participants[cid] = {}

    # совместимость: если раньше хранили строкой — превращаем в dict
    prev = chat_participants[cid].get(uid)
    if isinstance(prev, dict):
        info = prev
    else:
        info = {'name': str(prev) if prev is not None else str(name), 'username': ''}

    info['name'] = str(name)
    if username:
        info['username'] = str(username)
    chat_participants[cid][uid] = info

    if chat_title:
        chat_titles[cid] = str(chat_title)
    # persist cache for tops across restarts
    _mark_chat_cache_dirty()

def get_chat_users(chat_id):
    users = chat_participants.get(chat_id, {})
    out = []
    for uid, info in (users or {}).items():
        if isinstance(info, dict):
            out.append({'id': uid, 'name': info.get('name', ''), 'username': info.get('username', '')})
        else:
            out.append({'id': uid, 'name': info})
    return out


# --- SECURITY: token must NOT be hardcoded ---
# Set BOT_TOKEN in environment OR create a .env file рядом с ботом:
# BOT_TOKEN=123:ABC
def _load_env_file(path: str = ".env") -> dict:
    env = {}
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception as e:
        print(f"⚠️ Не удалось прочитать .env: {e}")
    return env

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8814745770:AAF0-qwY0JC2o49C64hElY-BKUGlrc4q-rg")
if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")
CHANNEL = '@utxa_news'
ADMIN_IDS = [8522752836]  # <-- замените на свой Telegram ID

bot = telebot.TeleBot(TOKEN)


# =========================
# ✨ PREMIUM / CUSTOM EMOJI
# Формат для Telegram HTML: <tg-emoji emoji-id="ID">🙂</tg-emoji>
# Новые emoji-id можно добавлять в PREMIUM_EMOJI.
# =========================
PREMIUM_EMOJI = {
    "🎲": "5260547274957672345",
    "🔄": "5264727218734524899",
    "💰": "5375296873982604963",
    "🏆": "5226431245918942763",
    "🎰": "5235989279024373566",
    "🦵": "5217818964612108191",
    "🔪": "5373239082136650704",
    "👊": "5233552787027017862",
    "🔥": "5424972470023104089",
    "📊": "5231200819986047254",
    "🆔": "5884366771913233289",
    "💸": "5472030678633684592",
    "✅": "5427009714745517609",
    "❌": "5210952531676504517",
    "🪙": "5379600444098093058",
    "👤": "5373012449597335010",
    "❓": "5436113877181941026",
    "💳": "5366223171454278937",
    "‼️": "5440660757194744323",
    "🎮": "5319247469165433798",
    "🫂": "5370867268051806190",
    "💋": "5433888551546662318",
    "🔞": "5980785424749039265",
    "📈": "5231200819986047254",
}


def premiumize_emoji(text: str) -> str:
    """Заменяет обычные эмодзи на Telegram Premium Emoji-теги.
    Работает для текста/подписей сообщений, где включён parse_mode='HTML'.
    Уже готовые <tg-emoji>...</tg-emoji> не трогает.
    """
    try:
        if not isinstance(text, str) or not text:
            return text

        parts = re.split(r'(<tg-emoji[^>]*>.*?</tg-emoji>)', text, flags=re.S)
        out_parts = []
        items = sorted(PREMIUM_EMOJI.items(), key=lambda x: len(x[0]), reverse=True)

        for part in parts:
            if not part:
                continue
            if part.startswith("<tg-emoji"):
                out_parts.append(part)
                continue
            for emoji, emoji_id in items:
                part = part.replace(emoji, f'<tg-emoji emoji-id="{emoji_id}">{emoji}</tg-emoji>')
            out_parts.append(part)

        return "".join(out_parts)
    except Exception:
        return text


def _premiumize_text_kwargs(text, kwargs):
    """Возвращает (new_text, new_kwargs, changed, added_parse_mode)."""
    new_kwargs = dict(kwargs or {})
    new_text = premiumize_emoji(text)
    changed = isinstance(text, str) and new_text != text
    added_parse_mode = False
    if changed and not new_kwargs.get("parse_mode"):
        new_kwargs["parse_mode"] = "HTML"
        added_parse_mode = True
    return new_text, new_kwargs, changed, added_parse_mode


# ====== BROADCAST STATE (admin рассылка) ======
_BROADCAST_STATE = {}  # admin_id(str) -> {'mode': 'text'|'photo'|'forward'}


# =========================
# 📢 ADMIN BROADCAST (исправлено)
# Админ в панели выбирает тип рассылки -> бот ждёт следующее сообщение в ЛС
# и рассылает его всем пользователям из базы (только numeric user_id ключи).
# =========================
@bot.message_handler(
    func=lambda m: (
        m.chat and m.chat.type == 'private' and
        getattr(m, 'from_user', None) and
        int(getattr(m.from_user, 'id', 0)) in ADMIN_IDS and
        str(getattr(m.from_user, 'id', '')) in _BROADCAST_STATE
    ),
    content_types=['text', 'photo', 'document', 'video', 'audio', 'voice', 'sticker', 'animation']
)
def _broadcast_catcher(message):
    try:
        admin_id = int(message.from_user.id)
        st = _BROADCAST_STATE.get(str(admin_id)) or {}
        mode = st.get('mode')
        if not mode:
            return

        # собираем айди пользователей (в базе есть и служебные ключи типа whitelist)
        data = load_data()
        user_ids = []
        for k, v in (data or {}).items():
            try:
                if str(k).isdigit():
                    user_ids.append(int(k))
            except Exception:
                continue

        sent = 0
        failed = 0

        if mode == 'text':
            txt = (message.text or '').strip()
            if not txt:
                bot.reply_to(message, "❗ Отправь текст одним сообщением для рассылки.")
                return
            for uid in user_ids:
                try:
                    bot.send_message(uid, txt, parse_mode="HTML", disable_web_page_preview=True)
                    sent += 1
                except Exception:
                    failed += 1

        elif mode == 'photo':
            if not getattr(message, 'photo', None):
                bot.reply_to(message, "❗ Отправь фото (можно с подписью) для рассылки.")
                return
            photo_id = message.photo[-1].file_id
            caption = message.caption
            for uid in user_ids:
                try:
                    bot.send_photo(uid, photo_id, caption=caption, parse_mode="HTML")
                    sent += 1
                except Exception:
                    failed += 1

        else:  # forward / copy
            for uid in user_ids:
                try:
                    if hasattr(bot, 'copy_message'):
                        bot.copy_message(uid, message.chat.id, message.message_id)
                    else:
                        bot.forward_message(uid, message.chat.id, message.message_id)
                    sent += 1
                except Exception:
                    failed += 1

        _BROADCAST_STATE.pop(str(admin_id), None)
        bot.send_message(admin_id, f"✅ <b>Рассылка завершена.</b>\nОтправлено: <b>{sent}</b>\nОшибок: <b>{failed}</b>", parse_mode="HTML")
    except Exception:
        try:
            _BROADCAST_STATE.pop(str(getattr(message.from_user, 'id', '')), None)
        except Exception:
            pass
        try:
            bot.reply_to(message, "❌ Ошибка рассылки. Попробуй ещё раз.")
        except Exception:
            pass

# === Глобальная база @username -> user_id (чтобы регистратор мог !поженить даже если игрок сейчас молчит) ===
USERNAME_MAP_FILE = 'username_map.json'
_username_map_cache = None
_username_map_dirty = False
_username_map_last_save = 0

def _load_username_map() -> dict:
    global _username_map_cache
    if isinstance(_username_map_cache, dict):
        return _username_map_cache
    try:
        if os.path.exists(USERNAME_MAP_FILE) and os.path.getsize(USERNAME_MAP_FILE) > 0:
            with open(USERNAME_MAP_FILE, 'r', encoding='utf-8') as f:
                d = json.load(f)
                if isinstance(d, dict):
                    _username_map_cache = d
                    return _username_map_cache
    except Exception:
        pass
    _username_map_cache = {}
    return _username_map_cache

def _save_username_map(d: dict):
    tmp = USERNAME_MAP_FILE + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=4)
        import shutil as _sh
        _sh.move(tmp, USERNAME_MAP_FILE)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

def remember_username(user_obj):
    """Сохраняем соответствие username -> id + имя (в файл и в users data)."""
    global _username_map_dirty, _username_map_last_save
    try:
        if not user_obj:
            return
        if getattr(user_obj, 'is_bot', False):
            return
        uid = int(getattr(user_obj, 'id', 0))
        if uid <= 0:
            return
        uname = getattr(user_obj, 'username', '') or ''
        if not uname:
            return
        key = uname.strip().lstrip('@').lower()
        if not key:
            return

        d = _load_username_map()
        cur = d.get(key)
        new = {
            "id": uid,
            "username": uname,
            "first_name": getattr(user_obj, 'first_name', '') or 'Игрок'
        }
        if cur != new:
            d[key] = new
            _username_map_dirty = True

        # Параллельно сохраняем username в основной базе пользователей (если она есть)
        try:
            data = load_data()
            u = get_user(data, str(uid), getattr(user_obj, 'first_name', 'Игрок'))
            u['username'] = uname
            save_data(data)
        except Exception:
            pass

        # не пишем на диск каждое сообщение: раз в ~30 секунд, если есть изменения
        now_ts = time.time()
        if _username_map_dirty and (now_ts - float(_username_map_last_save or 0) >= 30):
            _save_username_map(d)
            _username_map_dirty = False
            _username_map_last_save = now_ts
    except Exception:
        pass

def resolve_user_token(token: str, chat_id=None):
    """Пытаемся получить (user_id, display_name) по токену: @username или числовой id.
    Работает даже если игрок сейчас молчит в чате (берём из username_map / users data).
    """
    tok = (token or '').strip()
    if not tok:
        return None
    # числовой id
    if tok.isdigit():
        uid = int(tok)
        try:
            data = load_data()
            u = get_user(data, str(uid), "Игрок")
            name = u.get('first_name') or "Игрок"
        except Exception:
            name = "Игрок"
        return {"id": uid, "name": name}

    # @username
    if tok.startswith('@'):
        key = tok.lstrip('@').strip().lower()
        # 1) пробуем найти прямо в чате (если есть кэш участников)
        try:
            if chat_id is not None:
                u_obj = _resolve_username_in_chat(chat_id, tok)
                if u_obj:
                    return {"id": int(u_obj.id), "name": getattr(u_obj, 'first_name', None) or getattr(u_obj, 'username', None) or "Игрок"}
        except Exception:
            pass

        # 2) глобальная карта username -> id
        d = _load_username_map()
        if key in d:
            uid = int(d[key].get("id", 0))
            if uid > 0:
                name = d[key].get("first_name") or "Игрок"
                # если в users data имя есть — берем оттуда (актуальнее)
                try:
                    data = load_data()
                    u = get_user(data, str(uid), name)
                    name = u.get('first_name') or name
                except Exception:
                    pass
                return {"id": uid, "name": name}

        # 3) если username уже сохранён внутри основной базы пользователей
        try:
            data = load_data()
            for uid_str, u in (data or {}).items():
                try:
                    if isinstance(u, dict) and (u.get('username', '') or '').strip().lstrip('@').lower() == key:
                        return {"id": int(uid_str), "name": u.get('first_name') or "Игрок"}
                except Exception:
                    continue
        except Exception:
            pass

    return None

# === Авто-раскраска всех inline-кнопок в боте ===
# Ничего в логике функций не меняем: просто перехватываем отправку/редактирование сообщений
# и подмешиваем поле style в reply_markup (если это inline клавиатура).
try:
    _orig_send_message = bot.send_message
    _orig_reply_to = bot.reply_to
    _orig_edit_message_text = bot.edit_message_text
    _orig_edit_message_reply_markup = bot.edit_message_reply_markup
    _orig_send_photo = bot.send_photo
    _orig_send_video = bot.send_video
    _orig_send_animation = bot.send_animation

    def _send_message_patched(chat_id, text, *args, **kwargs):
        if "reply_markup" in kwargs:
            kwargs["reply_markup"] = _styled(kwargs["reply_markup"])
        new_text, new_kwargs, changed, added_parse_mode = _premiumize_text_kwargs(text, kwargs)
        try:
            return _orig_send_message(chat_id, new_text, *args, **new_kwargs)
        except Exception:
            # Если Telegram не принял HTML/custom emoji, отправляем исходный текст, чтобы бот не падал.
            if changed:
                fallback_kwargs = dict(kwargs)
                if added_parse_mode:
                    fallback_kwargs.pop("parse_mode", None)
                return _orig_send_message(chat_id, text, *args, **fallback_kwargs)
            raise

    def _reply_to_patched(message, text, *args, **kwargs):
        if "reply_markup" in kwargs:
            kwargs["reply_markup"] = _styled(kwargs["reply_markup"])
        new_text, new_kwargs, changed, added_parse_mode = _premiumize_text_kwargs(text, kwargs)
        try:
            return _orig_reply_to(message, new_text, *args, **new_kwargs)
        except Exception:
            if changed:
                fallback_kwargs = dict(kwargs)
                if added_parse_mode:
                    fallback_kwargs.pop("parse_mode", None)
                return _orig_reply_to(message, text, *args, **fallback_kwargs)
            raise

    def _edit_message_text_patched(text, chat_id, message_id, *args, **kwargs):
        if "reply_markup" in kwargs:
            kwargs["reply_markup"] = _styled(kwargs["reply_markup"])
        new_text, new_kwargs, changed, added_parse_mode = _premiumize_text_kwargs(text, kwargs)
        try:
            return _orig_edit_message_text(new_text, chat_id, message_id, *args, **new_kwargs)
        except Exception:
            if changed:
                fallback_kwargs = dict(kwargs)
                if added_parse_mode:
                    fallback_kwargs.pop("parse_mode", None)
                return _orig_edit_message_text(text, chat_id, message_id, *args, **fallback_kwargs)
            raise

    def _edit_message_reply_markup_patched(chat_id, message_id, *args, **kwargs):
        if "reply_markup" in kwargs:
            kwargs["reply_markup"] = _styled(kwargs["reply_markup"])
        return _orig_edit_message_reply_markup(chat_id, message_id, *args, **kwargs)


    def _send_photo_patched(chat_id, photo, *args, **kwargs):
        if "reply_markup" in kwargs:
            kwargs["reply_markup"] = _styled(kwargs["reply_markup"])
        if kwargs.get("caption"):
            new_caption, new_kwargs, changed, added_parse_mode = _premiumize_text_kwargs(kwargs.get("caption"), kwargs)
            new_kwargs["caption"] = new_caption
            try:
                return _orig_send_photo(chat_id, photo, *args, **new_kwargs)
            except Exception:
                if changed:
                    fallback_kwargs = dict(kwargs)
                    if added_parse_mode:
                        fallback_kwargs.pop("parse_mode", None)
                    return _orig_send_photo(chat_id, photo, *args, **fallback_kwargs)
                raise
        return _orig_send_photo(chat_id, photo, *args, **kwargs)

    def _send_video_patched(chat_id, video, *args, **kwargs):
        if "reply_markup" in kwargs:
            kwargs["reply_markup"] = _styled(kwargs["reply_markup"])
        if kwargs.get("caption"):
            new_caption, new_kwargs, changed, added_parse_mode = _premiumize_text_kwargs(kwargs.get("caption"), kwargs)
            new_kwargs["caption"] = new_caption
            try:
                return _orig_send_video(chat_id, video, *args, **new_kwargs)
            except Exception:
                if changed:
                    fallback_kwargs = dict(kwargs)
                    if added_parse_mode:
                        fallback_kwargs.pop("parse_mode", None)
                    return _orig_send_video(chat_id, video, *args, **fallback_kwargs)
                raise
        return _orig_send_video(chat_id, video, *args, **kwargs)

    def _send_animation_patched(chat_id, animation, *args, **kwargs):
        if "reply_markup" in kwargs:
            kwargs["reply_markup"] = _styled(kwargs["reply_markup"])
        if kwargs.get("caption"):
            new_caption, new_kwargs, changed, added_parse_mode = _premiumize_text_kwargs(kwargs.get("caption"), kwargs)
            new_kwargs["caption"] = new_caption
            try:
                return _orig_send_animation(chat_id, animation, *args, **new_kwargs)
            except Exception:
                if changed:
                    fallback_kwargs = dict(kwargs)
                    if added_parse_mode:
                        fallback_kwargs.pop("parse_mode", None)
                    return _orig_send_animation(chat_id, animation, *args, **fallback_kwargs)
                raise
        return _orig_send_animation(chat_id, animation, *args, **kwargs)

    bot.send_message = _send_message_patched
    bot.reply_to = _reply_to_patched
    bot.edit_message_text = _edit_message_text_patched
    bot.edit_message_reply_markup = _edit_message_reply_markup_patched
    bot.send_photo = _send_photo_patched
    bot.send_video = _send_video_patched
    bot.send_animation = _send_animation_patched
except Exception:
    pass

# === Fallback для статистики сообщений (если middleware не поддерживается) ===
def _update_listener_fallback(new_messages):
    try:
        for m in new_messages or []:
            try:
                if not m or not getattr(m, "from_user", None):
                    continue
                if getattr(m.from_user, "is_bot", False):
                    continue
                if m.chat and m.chat.type in ["group", "supergroup"]:
                    inc_message_stat(m.chat.id, m.from_user.id, getattr(m.from_user, "first_name", "Игрок"))
                    _track_recent_message(m.chat.id, m.from_user.id)
                    try:
                        track_user_in_chat(m.chat.id, m.from_user.id, getattr(m.from_user, "first_name", "Игрок"), getattr(m.from_user, "username", "") or "", getattr(m.chat, "title", "") or "")
                        remember_username(m.from_user)
                    except Exception:
                        pass
            except Exception:
                continue
    except Exception:
        pass

try:
    bot.set_update_listener(_update_listener_fallback)
except Exception:
    pass





# Middleware: считаем каждое сообщение в группе (не блокирует другие хендлеры)
try:
    @bot.middleware_handler(update_types=['message'])
    def _mw_count_messages(bot_instance, message):
        try:
            if not message or not getattr(message, 'from_user', None):
                return
            if getattr(message.from_user, 'is_bot', False):
                return
            if message.chat and message.chat.type in ['group', 'supergroup']:
                inc_message_stat(message.chat.id, message.from_user.id, message.from_user.first_name)
                _track_recent_message(message.chat.id, message.from_user.id)
                # параллельно отмечаем участника (для топа по чату)
                try:
                    track_user_in_chat(message.chat.id, message.from_user.id, message.from_user.first_name, getattr(message.from_user, 'username', '') or '', getattr(message.chat, 'title', '') or '')
                    remember_username(message.from_user)
                except Exception:
                    pass
        except Exception:
            pass
except Exception:
    # если middleware не поддерживается в вашей версии — статистика может считаться не полностью
    pass


# =========================
# 🪙 ОРЁЛ И РЕШКА (дуэль) — по чатам
# Команда: !монетка 50000 (ответом на сообщение игрока)
# =========================
_coin_lock = threading.Lock()
coin_games_by_chat = {}   # chat_id -> game_id (активная игра в чате)
coin_games = {}           # game_id -> dict(game_state)

def _new_game_id() -> str:
    return str(int(time.time() * 1000)) + str(random.randint(100, 999))

def _coin_cleanup_expired():
    # чистим просроченные ожидания/игры (периодически)
    now_ts = time.time()
    dead = []
    for gid, g in list(coin_games.items()):
        try:
            if now_ts - float(g.get('created_at', now_ts)) > 10 * 60:
                dead.append(gid)
        except Exception:
            dead.append(gid)
    for gid in dead:
        try:
            chat_id = str(coin_games[gid].get('chat_id'))
            if coin_games_by_chat.get(chat_id) == gid:
                coin_games_by_chat.pop(chat_id, None)
        except Exception:
            pass
        coin_games.pop(gid, None)

def _coin_end_game(chat_id: str, gid: str):
    if coin_games_by_chat.get(chat_id) == gid:
        coin_games_by_chat.pop(chat_id, None)
    coin_games.pop(gid, None)

def _coin_edit_remove_kb(chat_id: int, message_id: int):
    try:
        bot.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
    except Exception:
        pass


def _coin_expire_invite(gid: str):
    """Авто-истечение приглашения через 1 минуту (если не приняли)."""
    try:
        with _coin_lock:
            g = coin_games.get(gid)
            if not g:
                return
            if g.get('stage') != 'pending':
                return
            chat_id_str = str(g.get('chat_id'))
            chat_id_int = int(chat_id_str)
            cleanup_ids = list(g.get('cleanup_msg_ids') or [])
            msg_id = g.get('msg_id')

        # Убираем кнопки (на всякий)
        try:
            if msg_id:
                _coin_edit_remove_kb(chat_id_int, int(msg_id))
        except Exception:
            pass

        # Удаляем сообщения вызова/команды
        for mid in cleanup_ids:
            try:
                bot.delete_message(chat_id_int, int(mid))
            except Exception:
                pass

        # Возвращаем резерв приглашающему (если резервировали при создании)
        try:
            with _coin_lock:
                g2 = coin_games.get(gid)
            if g2 and g2.get('inviter_reserved'):
                amount = int(g2.get('amount', 0))
                inviter_id = int(g2.get('inviter_id', 0))
                inviter_name = str(g2.get('inviter_name', 'Игрок'))
                data = load_data()
                u1 = get_user(data, inviter_id, inviter_name)
                u1['balance'] = int(u1.get('balance', 0)) + amount
                save_data(data)
        except Exception:
            pass

        with _coin_lock:
            _coin_end_game(chat_id_str, gid)
    except Exception:
        # никогда не валим бота из-за авто-таймера
        try:
            with _coin_lock:
                g = coin_games.get(gid)
                if g:
                    _coin_end_game(str(g.get('chat_id')), gid)
        except Exception:
            pass

@bot.message_handler(func=lambda m: m.text and (
    m.text.strip().lower().startswith('!монетка') or
    m.text.strip().lower().startswith('!мон ') or
    m.text.strip().lower() == '!мон'
))
def coin_invite_cmd(message):
    if message.chat.type == 'private':
        return

    # нужно ответом на сообщение
    if not message.reply_to_message or not message.reply_to_message.from_user:
        bot.reply_to(message, "❗ Используй так: ответь на сообщение игрока и напиши <code>!монетка 50000</code> или <code>!мон 50000</code>", parse_mode="HTML")
        return

    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(message, "❗ Сумма нужна числом. Пример: <code>!монетка 50000</code>", parse_mode="HTML")
        return

    amount = int(parts[1])
    if amount <= 0:
        return

    inviter = message.from_user
    invitee = message.reply_to_message.from_user

    # запрет играть с ботами / с самим собой
    if getattr(invitee, 'is_bot', False):
        bot.reply_to(message, "🤖 С ботами играть нельзя 🙂")
        return
    if inviter.id == invitee.id:
        bot.reply_to(message, "🤔 С самим собой нельзя.")
        return

    chat_id = str(message.chat.id)

    with _coin_lock:
        _coin_cleanup_expired()
        if chat_id in coin_games_by_chat:
            bot.reply_to(message, "⚠️ В этом чате уже идёт игра 🪙. Дождитесь конца.")
            return

        # проверяем баланс приглашающего сейчас (у приглашённого проверим при принятии, чтобы не было бага)
        data = load_data()
        u1 = get_user(data, inviter.id, inviter.first_name)
        if int(u1.get('balance', 0)) < amount:
            bot.reply_to(message, "❌ Недостаточно 💰 для ставки!")
            return

        # Резервируем ставку приглашающего сразу (чтобы нельзя было потратить в другой игре)
        try:
            u1['balance'] = int(u1.get('balance', 0)) - amount
            save_data(data)
        except Exception:
            bot.reply_to(message, "❌ Ошибка списания ставки. Попробуйте ещё раз.")
            return

        gid = _new_game_id()
        coin_games_by_chat[chat_id] = gid
        coin_games[gid] = {
            'created_at': time.time(),
            'chat_id': chat_id,
            'amount': amount,
            'inviter_reserved': True,
            'inviter_id': int(inviter.id),
            'inviter_name': str(inviter.first_name),
            'invitee_id': int(invitee.id),
            'invitee_name': str(invitee.first_name),
            'stage': 'pending',   # pending -> choose -> flip -> done
            'chooser_id': None,
            'choice': None,       # 'орел'|'решка'
            'msg_id': None,
            'cleanup_msg_ids': []
        }

    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(
        types.InlineKeyboardButton("✅ Принять", callback_data=f"coin_acc|{gid}"),
        types.InlineKeyboardButton("❌ Отказаться", callback_data=f"coin_dec|{gid}")
    )

    txt = (
        f"🪙 <b>Орел и Решка</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"👤 <a href='tg://user?id={inviter.id}'>{safe_html(inviter.first_name)}</a> пригласил(а)\n"
        f"👤 <a href='tg://user?id={invitee.id}'>{safe_html(invitee.first_name)}</a>\n"
        f"Ставка: <b>{format_balance(amount)}</b> 💰\n\n"
        f"<i>{safe_html(invitee.first_name)}, принимаешь?</i>"
    )
    chat_id_int = int(message.chat.id)
    sent = bot.send_message(
        chat_id_int,
        txt,
        parse_mode="HTML",
        reply_markup=mk
    )
    try:
        with _coin_lock:
            if gid in coin_games:
                coin_games[gid].setdefault('cleanup_msg_ids', []).append(int(sent.message_id))
                coin_games[gid]['msg_id'] = int(sent.message_id)
                # сообщение с командой тоже уберём после игры (если получится)
                coin_games[gid].setdefault('cleanup_msg_ids', []).append(int(message.message_id))
    except Exception:
        pass

    # авто-истечение вызова через 1 минуту (если не приняли)
    try:
        threading.Timer(60, lambda gid=gid: _coin_expire_invite(gid)).start()
    except Exception:
        pass





def _coin_start_choose(chat_id_int: int, gid: str):
    # выбираем, кто выбирает сторону, и показываем кнопки Орёл/Решка
    with _coin_lock:
        g = coin_games.get(gid)
        if not g:
            return
        if g.get('stage') != 'pending':
            return
        inviter_id = int(g.get('inviter_id'))
        invitee_id = int(g.get('invitee_id'))
        chooser_id = random.choice([inviter_id, invitee_id])
        g['chooser_id'] = chooser_id
        g['stage'] = 'choose'

    chooser_name = g.get('inviter_name') if chooser_id == inviter_id else g.get('invitee_name')
    chooser_name = str(chooser_name or 'Игрок')

    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(
        types.InlineKeyboardButton("🪙 Орёл", callback_data=f"coin_pick|{gid}|o"),
        types.InlineKeyboardButton("🪙 Решка", callback_data=f"coin_pick|{gid}|r")
    )

    text = (
        f"🪙 <b>Выбор стороны</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"Выбирает: <a href='tg://user?id={chooser_id}'>{safe_html(chooser_name)}</a>\n"
        f"<i>Нажми кнопку ниже:</i>"
    )

    sent = bot.send_message(chat_id_int, text, parse_mode="HTML", reply_markup=mk)
    try:
        with _coin_lock:
            if gid in coin_games:
                coin_games[gid].setdefault('cleanup_msg_ids', []).append(int(sent.message_id))
    except Exception:
        pass


def _is_admin_or_owner(chat_id: int, user_id: int) -> bool:
    # владелец бота (ADMIN_IDS) или админ/создатель чата
    try:
        if int(user_id) in ADMIN_IDS:
            return True
    except Exception:
        pass
    try:
        member = bot.get_chat_member(chat_id, user_id)
        status = getattr(member, 'status', '') or ''
        return status in ['administrator', 'creator']
    except Exception:
        return False


@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!monoff')
def coin_monoff_cmd(message):
    # отмена монетки только в текущем чате
    if message.chat.type == 'private':
        return
    if not _is_admin_or_owner(message.chat.id, message.from_user.id):
        bot.reply_to(message, "❌ Команда доступна только админам этого чата.")
        return

    chat_id = str(message.chat.id)
    with _coin_lock:
        gid = coin_games_by_chat.get(chat_id)
        g = coin_games.get(gid) if gid else None

    if not gid or not g:
        bot.reply_to(message, "✅ В этом чате нет активной игры 🪙.")
        return

    # Убираем кнопки и удаляем сообщения игры (если получится)
    try:
        if g.get('msg_id'):
            _coin_edit_remove_kb(message.chat.id, int(g['msg_id']))
    except Exception:
        pass

    cleanup_ids = list(g.get('cleanup_msg_ids') or [])
    for mid in cleanup_ids:
        try:
            bot.delete_message(message.chat.id, int(mid))
        except Exception:
            pass

    with _coin_lock:
        _coin_end_game(chat_id, gid)

    bot.send_message(message.chat.id, "🪙 Игра отменена командой <code>!monoff</code>.", parse_mode="HTML")


def _coin_finish_flip(chat_id_int: int, gid: str):
    # показ результата через 5 секунд
    def _finish():
        with _coin_lock:
            g = coin_games.get(gid)
            if not g:
                return
            if g.get('stage') != 'flip':
                return
            amount = int(g.get('amount', 0))
            choice = g.get('choice')  # 'орел'|'решка'
            inviter_id = int(g['inviter_id'])
            invitee_id = int(g['invitee_id'])
            cleanup_ids = list(g.get('cleanup_msg_ids') or [])

        def _name_for(uid: int) -> str:
            if uid == inviter_id:
                return str(g.get('inviter_name') or 'Игрок')
            if uid == invitee_id:
                return str(g.get('invitee_name') or 'Игрок')
            return 'Игрок'

        result = random.choice(['орел', 'решка'])

        if choice == result:
            win_id = int(g['chooser_id'])
            lose_id = invitee_id if win_id == inviter_id else inviter_id
        else:
            lose_id = int(g['chooser_id'])
            win_id = invitee_id if lose_id == inviter_id else inviter_id

        # начисления: оба уже внесли по amount (при принятии)
        data = load_data()
        w = get_user(data, win_id, _name_for(win_id))
        w['balance'] = int(w.get('balance', 0)) + (amount * 2)
        save_data(data)

        # история
        try:
            save_history(win_id, "Орел/Решка победа", f"+{amount*2}")
            save_history(lose_id, "Орел/Решка поражение", f"-{amount}")
        except Exception:
            pass

        win_name = safe_html(_name_for(win_id))
        lose_name = safe_html(_name_for(lose_id))

        # Итоговое сообщение (его НЕ удаляем)
        result_msg = bot.send_message(
            chat_id_int,
            f"🪙 <b>Результат:</b> <b>{'🪙 Орёл' if result=='орел' else '🪙 Решка'}</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"🏆 Победитель: <a href='tg://user?id={win_id}'>{win_name}</a>\n"
            f"➕ Выигрыш: <b>{format_balance(amount*2)}</b> 💰\n"
            f"😔 Проиграл: <a href='tg://user?id={lose_id}'>{lose_name}</a>",
            parse_mode="HTML"
        )

        # Чистим все сообщения игры (оставляем только итог)
        for mid in cleanup_ids:
            try:
                if int(mid) == int(result_msg.message_id):
                    continue
                bot.delete_message(chat_id_int, int(mid))
            except Exception:
                pass

        with _coin_lock:
            _coin_end_game(str(chat_id_int), gid)

    threading.Timer(5, _finish).start()


BOT_USERNAME = None
try:
    BOT_USERNAME = bot.get_me().username
except Exception:
    BOT_USERNAME = None

BOT_ID = None
try:
    BOT_ID = bot.get_me().id
except Exception:
    BOT_ID = None

DATA_FILE = 'casino_users.json'
BACKUP_FILE = DATA_FILE + '.bak'
_DATA_LOCK = threading.Lock()
results_log = {}  # Теперь это словарь: {chat_id: [список результатов]}
# --- STATE: хранение по чатам + блокировки ---
import threading
from html import escape as _html_escape

bets = []  # legacy (не использовать напрямую)
bets_by_chat = {}  # chat_id(str) -> list[bet]
spinning_by_chat = set()  # set[str(chat_id)]
spinning = False  # legacy: не использовать (используй spinning_by_chat)
_chat_locks = {}  # chat_id(str) -> threading.Lock

# Последняя "квитанция ставок" по (chat_id, user_id)
last_user_bets = {}  # key: f"{chat_id}:{user_id}" -> list[bet]

# --- анти-флуд для "пов" (повторить): 1 раз после игры, сбрасывается после "отмена" ---
repeat_used = {}  # key: f"{chat_id}:{user_id}" -> bool

# --- ограбления (пер-чату + общий топ) ---
ROBBERY_FILE = 'robbery_stats.json'

ROBBERY_CD_SECONDS = 2 * 60 * 60  # 2 часа
ROBBERY_CD_FILE = 'robbery_cooldowns.json'

# --- покупка функции ограбления ("Ворская жизнь") ---
ROBBERY_ACCESS_FILE = 'robbery_access.json'  # user_id -> true

# --- доступ к платной команде !бот ищи ---
BOTSEARCH_ACCESS_FILE = 'botsearch_access.json'  # user_id -> true

def _load_robbery_access():
    try:
        if os.path.exists(ROBBERY_ACCESS_FILE) and os.path.getsize(ROBBERY_ACCESS_FILE) > 0:
            with open(ROBBERY_ACCESS_FILE, 'r', encoding='utf-8') as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}

def _save_robbery_access(d):
    tmp = ROBBERY_ACCESS_FILE + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=4)
        import shutil as _sh
        _sh.move(tmp, ROBBERY_ACCESS_FILE)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

def has_robbery_access(user_id: str) -> bool:
    d = _load_robbery_access()
    return str(user_id) in d and bool(d[str(user_id)])

def grant_robbery_access(user_id: str):
    d = _load_robbery_access()
    d[str(user_id)] = True
    _save_robbery_access(d)

def revoke_robbery_access(user_id: str):
    d = _load_robbery_access()
    d.pop(str(user_id), None)
    _save_robbery_access(d)

def _load_botsearch_access():
    try:
        if os.path.exists(BOTSEARCH_ACCESS_FILE) and os.path.getsize(BOTSEARCH_ACCESS_FILE) > 0:
            with open(BOTSEARCH_ACCESS_FILE, 'r', encoding='utf-8') as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}

def _save_botsearch_access(d):
    tmp = BOTSEARCH_ACCESS_FILE + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=4)
        import shutil as _sh
        _sh.move(tmp, BOTSEARCH_ACCESS_FILE)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

def has_botsearch_access(user_id: str) -> bool:
    d = _load_botsearch_access()
    return str(user_id) in d and bool(d[str(user_id)])

def grant_botsearch_access(user_id: str):
    d = _load_botsearch_access()
    d[str(user_id)] = True
    _save_botsearch_access(d)

def revoke_botsearch_access(user_id: str):
    d = _load_botsearch_access()
    d.pop(str(user_id), None)
    _save_botsearch_access(d)


# --- доступ к платной функции 🏦 Банк (личный сейф) ---
BANK_ACCESS_FILE = 'bank_access.json'  # user_id -> true

def _load_bank_access():
    try:
        if os.path.exists(BANK_ACCESS_FILE) and os.path.getsize(BANK_ACCESS_FILE) > 0:
            with open(BANK_ACCESS_FILE, 'r', encoding='utf-8') as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}

def _save_bank_access(d):
    tmp = BANK_ACCESS_FILE + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=4)
        import shutil as _sh
        _sh.move(tmp, BANK_ACCESS_FILE)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

def has_bank_access(user_id: str) -> bool:
    d = _load_bank_access()
    return str(user_id) in d and bool(d[str(user_id)])

def grant_bank_access(user_id: str):
    d = _load_bank_access()
    d[str(user_id)] = True
    _save_bank_access(d)

def revoke_bank_access(user_id: str):
    d = _load_bank_access()
    d.pop(str(user_id), None)
    _save_bank_access(d)

def _deep_link_url(start_payload: str) -> str:
    if BOT_USERNAME:
        return f"https://t.me/{BOT_USERNAME}?start={start_payload}"
    return ""

SHOP_PRICE_STARS = 15
SHOP_OWNER_USERNAME = "tonvio"  # куда писать для покупки
SHOP_DONATE_INFO = "@tonvio"

def _owner_buy_url(item_title: str, user_id: int, user_name: str) -> str:
    """Ссылка на владельца с заранее заполненным текстом."""
    import urllib.parse as _up
    txt = f"Хочу купить: {item_title} (цена: {SHOP_PRICE_STARS}⭐️). Мой ID: {user_id}. Имя: {user_name}"
    return f"https://t.me/{SHOP_OWNER_USERNAME}?text=" + _up.quote(txt)

def shop_message_text(user_id: int) -> str:
    return (
        f"🛒 <b>Магазин</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"💫 <b>Цена любого товара:</b> <b>{SHOP_PRICE_STARS}⭐️</b>\n"
        f"Оплата: пишете владельцу в ЛС (донат идёт на аккаунт владельца).\n"
        f"━━━━━━━━━━━━━━\n"
        f"1) 🔓 <b>Ворская жизнь</b> — доступ к команде <code>!ограбить</code>\n"
        f"2) 🚫 <b>Снять лимит переводов</b> — снимает лимит 1 000 000 🪙/день\n"
        f"3) 🔎 <b>!бот ищи</b> — поиск игрока (монеты + где состоит)\n"
        f"4) 🏦 <b>Банк</b> — личный сейф (кнопка 🏦 Банк в ЛС)\n"
        f"━━━━━━━━━━━━━━\n"
        f"👇 Нажми кнопку нужного товара и напиши мне в ЛС."
    )

def shop_message_markup(user_id: int) -> types.InlineKeyboardMarkup:
    uid = int(user_id)
    buyer_name = ""
    try:
        buyer_name = safe_html(getattr(bot.get_chat(uid), 'first_name', ''))
    except Exception:
        buyer_name = ""

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton("🔓 Купить «Ворская жизнь» (!ограбить)", url=_owner_buy_url("Ворская жизнь (!ограбить)", uid, buyer_name)))
    markup.add(types.InlineKeyboardButton("🚫 Купить «Снять лимит переводов»", url=_owner_buy_url("Снять лимит переводов", uid, buyer_name)))
    markup.add(types.InlineKeyboardButton("🔎 Купить «!бот ищи»", url=_owner_buy_url("Команда !бот ищи", uid, buyer_name)))
    markup.add(types.InlineKeyboardButton("🏦 Купить «Банк (личный сейф)»", url=_owner_buy_url("Банк (личный сейф)", uid, buyer_name)))
    return markup


def show_shop(message):
    """Показывает магазин:
    - в ЛС: полный магазин с кнопками
    - в чате: кнопка, которая открывает магазин в ЛС (без спама в группе)
    """
    try:
        if message.chat.type != 'private':
            url = _deep_link_url("shop")
            mk = types.InlineKeyboardMarkup()
            if url:
                mk.add(types.InlineKeyboardButton("🛒 Открыть магазин в ЛС", url=url))
            bot.reply_to(message, "🛒 Магазин открывается в ЛС бота.", reply_markup=mk if url else None)
            return

        bot.send_message(
            message.chat.id,
            shop_message_text(message.from_user.id),
            reply_markup=shop_message_markup(message.from_user.id),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except Exception:
        bot.send_message(message.chat.id, "🛒 Магазин временно недоступен.")

# совместимость со старым вызовом (если где-то осталось)
def send_shop(message):
    return show_shop(message)

def load_robbery_cd():
    if os.path.exists(ROBBERY_CD_FILE):
        try:
            with open(ROBBERY_CD_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_robbery_cd(d):
    with open(ROBBERY_CD_FILE, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=4)

def _load_robbery():
    try:
        if os.path.exists(ROBBERY_FILE) and os.path.getsize(ROBBERY_FILE) > 0:
            with open(ROBBERY_FILE, 'r', encoding='utf-8') as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}

def _save_robbery(d):
    tmp = ROBBERY_FILE + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=4)
        import shutil as _sh
        _sh.move(tmp, ROBBERY_FILE)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

def _robbery_add(chat_id: str, robber_id: str, delta_amount: int):
    d = _load_robbery()
    cid = str(chat_id)
    uid = str(robber_id)
    if cid not in d:
        d[cid] = {}
    if uid not in d[cid]:
        d[cid][uid] = {'count': 0, 'stolen': 0}
    d[cid][uid]['count'] = int(d[cid][uid].get('count', 0)) + 1
    d[cid][uid]['stolen'] = int(d[cid][uid].get('stolen', 0)) + int(delta_amount)
    _save_robbery(d)

def _robbery_top_chat(chat_id: str, limit: int = 10):
    d = _load_robbery()
    cid = str(chat_id)
    items = []
    for uid, info in d.get(cid, {}).items():
        try:
            items.append((uid, int(info.get('count', 0)), int(info.get('stolen', 0))))
        except Exception:
            pass
    items.sort(key=lambda x: (x[2], x[1]), reverse=True)  # stolen, then count
    return items[:limit]

def _robbery_top_global(limit: int = 10):
    d = _load_robbery()
    agg = {}
    for cid, m in d.items():
        if not isinstance(m, dict):
            continue
        for uid, info in m.items():
            try:
                c = int(info.get('count', 0))
                s = int(info.get('stolen', 0))
            except Exception:
                continue
            if uid not in agg:
                agg[uid] = [0, 0]
            agg[uid][0] += c
            agg[uid][1] += s
    items = [(uid, v[0], v[1]) for uid, v in agg.items()]
    items.sort(key=lambda x: (x[2], x[1]), reverse=True)
    return items[:limit]


def _lock_for_chat(chat_id) -> threading.Lock:
    cid = str(chat_id)
    if cid not in _chat_locks:
        _chat_locks[cid] = threading.Lock()
    return _chat_locks[cid]

def _get_chat_bets(chat_id):
    cid = str(chat_id)
    if cid not in bets_by_chat:
        bets_by_chat[cid] = []
    return bets_by_chat[cid]


def _merge_add_bet(chat_id, user_id: int, username: str, amount: int, bet_value: str):
    """Добавляет ставку, но если уже есть такая же (тот же игрок + та же цель) — суммирует."""
    try:
        cid = str(chat_id)
        uid = int(user_id)
        val = str(bet_value)
        amt = int(amount)
        if amt <= 0:
            return
        chat_bets = _get_chat_bets(cid)
        for b in chat_bets:
            try:
                if int(b.get('user_id', 0)) == uid and str(b.get('bet_value')) == val:
                    b['amount'] = int(b.get('amount', 0)) + amt
                    b['username'] = username
                    return
            except Exception:
                continue
        chat_bets.append({
            'user_id': uid,
            'username': username,
            'amount': amt,
            'bet_value': val,
            'chat_id': cid,
        })
    except Exception:
        pass


def has_bets(chat_id) -> bool:
    return len(_get_chat_bets(chat_id)) > 0

def safe_html(text: str) -> str:
    return _html_escape(str(text), quote=False)




# =========================
# 💍 СВАДЬБА / БРАКИ / HP / ОТЧИВКИ
# =========================

MARRIAGES_FILE = 'marriages.json'
DIVORCE_PENDING_FILE = 'divorce_pending.json'

# Регистраторы (кто может делать !поженить). По умолчанию = админы бота.
REGISTRAR_IDS = list(ADMIN_IDS) if 'ADMIN_IDS' in globals() else []

_BADGE_RULES = [
    (50.0, "❄"),
    (20.0, "❃"),
    (10.0, "✸"),
    (5.0,  "✿"),
]

def _hp_units_to_float(units: int) -> float:
    # 1 unit = 0.01 HP
    try:
        return int(units) / 100.0
    except Exception:
        return 0.0

def _marriage_pair_key(u1: int, u2: int) -> str:
    a, b = sorted([int(u1), int(u2)])
    return f"{a}_{b}"

def _load_json_file(path_: str, default):
    try:
        if os.path.exists(path_) and os.path.getsize(path_) > 0:
            with open(path_, 'r', encoding='utf-8') as f:
                d = json.load(f)
                return d if isinstance(d, type(default)) else default
    except Exception:
        pass
    return default

def _save_json_atomic(path_: str, data_obj):
    tmp = path_ + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data_obj, f, ensure_ascii=False, indent=4)
        import shutil as _sh
        _sh.move(tmp, path_)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

def _load_marriages():
    return _load_json_file(MARRIAGES_FILE, {"pairs": {}, "user_to_pair": {}})

def _save_marriages(d):
    if "pairs" not in d: d["pairs"] = {}
    if "user_to_pair" not in d: d["user_to_pair"] = {}
    _save_json_atomic(MARRIAGES_FILE, d)

def _load_divorce_pending():
    return _load_json_file(DIVORCE_PENDING_FILE, {})

def _save_divorce_pending(d):
    _save_json_atomic(DIVORCE_PENDING_FILE, d)

def _badge_for_hp_units(hp_units: int) -> str:
    hp = _hp_units_to_float(hp_units)
    for thr, emoji in _BADGE_RULES:
        if hp >= thr:
            return emoji
    return ""

def badge_for_user(user_id: int) -> str:
    try:
        m = _load_marriages()
        pair_key = m.get("user_to_pair", {}).get(str(user_id))
        if not pair_key:
            return ""
        pair = m.get("pairs", {}).get(pair_key)
        if not pair:
            return ""
        return _badge_for_hp_units(int(pair.get("hp_units", 0)))
    except Exception:
        return ""

def name_with_badge_plain(user_id: int, name: str) -> str:
    b = badge_for_user(int(user_id))
    if b:
        return f"{b} {name}"
    return str(name)

def name_with_badge_html(user_id: int, name: str) -> str:
    return safe_html(name_with_badge_plain(user_id, name))

def link_user_html(user_id: int, name: str) -> str:
    return f"<a href='tg://user?id={int(user_id)}'>{name_with_badge_html(user_id, name)}</a>"

def _format_duration(seconds: int) -> str:
    try:
        seconds = max(0, int(seconds))
    except Exception:
        seconds = 0
    mins = seconds // 60
    if mins <= 0:
        return "0 мин"
    days = mins // (60*24)
    mins_rem = mins % (60*24)
    hours = mins_rem // 60
    minutes = mins_rem % 60
    parts = []
    if days > 0:
        parts.append(f"{days} д")
    if hours > 0:
        parts.append(f"{hours} ч")
    if minutes > 0 and days == 0:
        parts.append(f"{minutes} мин")
    return " ".join(parts) if parts else "0 мин"

def _resolve_username_to_user(username: str):
    u = (username or "").strip()
    if not u:
        return None
    if not u.startswith("@"):
        u = "@" + u
    try:
        chat = bot.get_chat(u)
        return chat
    except Exception:
        return None



def _resolve_username_in_chat(chat_id: int, username: str):
    """Ищем пользователя по @username ТОЛЬКО среди тех, кого бот видел в этом чате.
    Это убирает зависимость от bot.get_chat('@user'), которая часто падает.
    Возвращает объект с полями id, first_name, username или None.
    """
    try:
        u = (username or '').strip()
        if not u:
            return None
        if u.startswith('@'):
            u = u[1:]
        u = u.lower()

        cid = str(chat_id)
        users = chat_participants.get(cid, {}) or {}
        for uid, info in users.items():
            try:
                if isinstance(info, dict):
                    un = (info.get('username') or '').strip()
                    nm = (info.get('name') or 'Игрок')
                else:
                    un = ''
                    nm = str(info or 'Игрок')
                if un and un.lower() == u:
                    obj = type('ChatUser', (), {})()
                    obj.id = int(uid)
                    obj.first_name = nm
                    obj.username = un
                    obj.is_bot = False
                    return obj
            except Exception:
                continue
    except Exception:
        pass
    return None

def _extract_mentions_from_message(message):
    """Достаёт упоминания из entities:
    - text_mention (нажатое упоминание, даёт user.id)
    - mention (@username)
    Возвращает (users_from_text_mention, usernames_from_mentions)
    """
    users = []
    usernames = []
    try:
        ents = getattr(message, 'entities', None) or []
        txt = message.text or ''
        for e in ents:
            try:
                t = getattr(e, 'type', '')
                if t == 'text_mention' and getattr(e, 'user', None):
                    users.append(e.user)
                elif t == 'mention':
                    s = txt[e.offset:e.offset + e.length]
                    if s:
                        usernames.append(s)
            except Exception:
                continue
    except Exception:
        pass
    # плюс слова, которые начинаются на @ (на всякий)
    try:
        for w in (message.text or '').split():
            if w.startswith('@') and len(w) > 1:
                usernames.append(w)
    except Exception:
        pass
    # уникализация
    seen=set()
    out=[]
    for u in usernames:
        k=u.lower()
        if k not in seen:
            seen.add(k); out.append(u)
    return users, out

def _is_registrar(user_id: int) -> bool:
    try:
        return int(user_id) in set(int(x) for x in REGISTRAR_IDS)
    except Exception:
        return False

def _get_my_marriage(user_id: int):
    m = _load_marriages()
    pair_key = m.get("user_to_pair", {}).get(str(user_id))
    if not pair_key:
        return None, None
    pair = m.get("pairs", {}).get(pair_key)
    return pair_key, pair

def _render_my_marriage(user_id: int) -> str:
    pair_key, pair = _get_my_marriage(user_id)
    if not pair:
        return "💔 У вас нет брака."
    u1 = int(pair.get("u1"))
    u2 = int(pair.get("u2"))
    hp_units = int(pair.get("hp_units", 0))
    hp = _hp_units_to_float(hp_units)
    since = int(pair.get("since_ts", 0))
    now = int(time.time())
    dur = _format_duration(now - since)

    n1 = pair.get("u1_name") or "Игрок"
    n2 = pair.get("u2_name") or "Игрок"

    text = (
        f"💍 <b>МОЙ БРАК</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"👩‍❤️‍👨 Пара: {link_user_html(u1, n1)} + {link_user_html(u2, n2)}\n"
        f"⏱ Вместе: <b>{dur}</b>\n"
        f"⚜️ HP: <b>{hp:.2f}</b>\n"
        f"━━━━━━━━━━━━━━"
    )
    return text

def _get_bot_username_cached() -> str:
    global _BOT_USERNAME_CACHE
    try:
        return _BOT_USERNAME_CACHE
    except Exception:
        _BOT_USERNAME_CACHE = None
    if _BOT_USERNAME_CACHE:
        return _BOT_USERNAME_CACHE
    try:
        me = bot.get_me()
        _BOT_USERNAME_CACHE = getattr(me, "username", None)
    except Exception:
        _BOT_USERNAME_CACHE = None
    return _BOT_USERNAME_CACHE or ""

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower().startswith('!поженить'))
def cmd_marry(message):
    # свадьба только в чате
    if message.chat.type == 'private':
        return
    if not _bot_cd_ok(message):
        return
    if not _is_registrar(message.from_user.id):
        bot.reply_to(message, "❌ Только регистратор может проводить свадьбу.")
        return
    # 1) собираем кандидатов (можно: reply, text_mention, @username, или числовой id)
    candidates = []

    # вариант A: reply = один из супругов
    if message.reply_to_message and getattr(message.reply_to_message, 'from_user', None):
        ru = message.reply_to_message.from_user
        if not getattr(ru, 'is_bot', False):
            candidates.append({
                "id": int(ru.id),
                "name": getattr(ru, "first_name", None) or getattr(ru, "username", None) or "Игрок",
                "is_bot": bool(getattr(ru, "is_bot", False))
            })

    # вариант B: text_mention / mention из entities
    users_from_text_mention, usernames = _extract_mentions_from_message(message)
    for u in users_from_text_mention:
        try:
            if u and not getattr(u, 'is_bot', False):
                candidates.append({
                    "id": int(u.id),
                    "name": getattr(u, "first_name", None) or getattr(u, "username", None) or "Игрок",
                    "is_bot": bool(getattr(u, "is_bot", False))
                })
        except Exception:
            continue

    # вариант C: @username — ищем глобально (по username_map/users data), а не только среди тех, кого бот видел в чате
    for un in usernames:
        r = resolve_user_token(un, chat_id=message.chat.id)
        if r:
            r["is_bot"] = False
            candidates.append(r)

    # вариант D: из текста команды: !поженить @a @b  или  !поженить 123 456  или  !поженить @b (если один через reply)
    parts = (message.text or '').strip().split()
    if len(parts) >= 2:
        for tok in parts[1:]:
            tok = (tok or "").strip()
            if not tok:
                continue
            if tok.startswith('@') or tok.isdigit():
                r = resolve_user_token(tok, chat_id=message.chat.id)
                if r:
                    r["is_bot"] = False
                    candidates.append(r)

    # чистим дубли по id
    uniq = []
    seen_ids = set()
    for c in candidates:
        try:
            uid = int(c.get("id"))
        except Exception:
            continue
        if uid in seen_ids:
            continue
        seen_ids.add(uid)
        uniq.append(c)
    candidates = uniq

    # ожидаем ровно 2 человека
    if len(candidates) < 2:
        bot.reply_to(
            message,
            "❌ Не смог найти двух игроков.\\n"
            "Используй: <code>!поженить @user1 @user2</code> или <code>!поженить ID1 ID2</code>.",
            parse_mode="HTML"
        )
        return

    # берём первых двух
    u1 = candidates[0]
    u2 = candidates[1]

    # защита от самосвадьбы / ботов
    if int(u1.get('id')) == int(u2.get('id')):
        bot.reply_to(message, "❌ Нельзя поженить одного и того же пользователя 🙂")
        return
    if bool(u1.get('is_bot')) or bool(u2.get('is_bot')):
        bot.reply_to(message, "🤖 Ботов женить нельзя 🙂")
        return

    uid1 = int(u1.get('id'))
    uid2 = int(u2.get('id'))

    # проверка: уже в браке?
    m = _load_marriages()
    utp = m.get("user_to_pair", {}) or {}
    if str(uid1) in utp or str(uid2) in utp:
        bot.reply_to(message, "💔 Кто-то из них уже в браке.")
        return

    pair_key = _marriage_pair_key(uid1, uid2)

    # имена: всегда берём актуальные first_name из Telegram-объекта
    n1 = (u1.get('name') or 'Игрок')
    n2 = (u2.get('name') or 'Игрок')

    pair = {
        "u1": min(uid1, uid2),
        "u2": max(uid1, uid2),
        "u1_name": n1 if min(uid1, uid2) == uid1 else n2,
        "u2_name": n2 if max(uid1, uid2) == uid2 else n1,
        "since_ts": int(time.time()),
        "hp_units": 0
    }

    m.setdefault("pairs", {})[pair_key] = pair
    m.setdefault("user_to_pair", {})[str(uid1)] = pair_key
    m.setdefault("user_to_pair", {})[str(uid2)] = pair_key
    _save_marriages(m)

    registrar_link = link_user_html(message.from_user.id, message.from_user.first_name)
    text = (
        "💞 <b>СВАДЬБА ОФОРМЛЕНА!</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"Регистратор: {registrar_link} ✅\n"
        f"Пара: {link_user_html(pair['u1'], pair['u1_name'])} + {link_user_html(pair['u2'], pair['u2_name'])}\n"
        "━━━━━━━━━━━━━━\n"
        "ℹ️ Посмотреть: <code>!мой брак</code> / <code>!брак</code> / <code>!мойб</code>"
    )
    bot.send_message(message.chat.id, text, parse_mode="HTML")
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in ['!браки', 'браки'])
def cmd_marriage_top(message):
    if not _bot_cd_ok(message):
        return
    m = _load_marriages()
    pairs = list(m.get("pairs", {}).items())
    if not pairs:
        bot.reply_to(message, "💔 Пока нет браков.")
        return
    pairs.sort(key=lambda kv: (int(kv[1].get("hp_units", 0)), -int(kv[1].get("since_ts", 0))), reverse=True)
    lines = []
    for i, (k, p) in enumerate(pairs[:10], 1):
        hp = _hp_units_to_float(int(p.get("hp_units", 0)))
        lines.append(f"{i}. {name_with_badge_html(p.get('u1'), p.get('u1_name','Игрок'))} + {name_with_badge_html(p.get('u2'), p.get('u2_name','Игрок'))} — ⚜️ <b>{hp:.2f}</b>")
    bot.send_message(message.chat.id, "💞 <b>ТОП БРАКОВ (⚜️ HP)</b>\n━━━━━━━━━━━━━━\n" + "\n".join(lines), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in ['!мой брак', '!мойбрак', '!мойб', '!брак'])
def cmd_my_marriage(message):
    if not _bot_cd_ok(message):
        return
    if message.chat.type == 'private':
        bot.send_message(message.chat.id, _render_my_marriage(message.from_user.id), parse_mode="HTML")
        return

    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(
        types.InlineKeyboardButton("💬 В чате", callback_data=f"mar_show_chat:{message.from_user.id}"),
        types.InlineKeyboardButton("📩 В ЛС", url=f"https://t.me/{_get_bot_username_cached()}?start=my_marriage")
    )
    bot.send_message(message.chat.id, "Открыть <b>мой брак</b> в чате или в ЛС?", parse_mode="HTML", reply_markup=mk)

@bot.callback_query_handler(func=lambda call: isinstance(call.data, str) and call.data.startswith("mar_show_chat:"))
def cb_mar_show_chat(call):
    try:
        uid = int(call.data.split(":", 1)[1])
    except Exception:
        uid = None
    if uid is None or int(call.from_user.id) != uid:
        bot.answer_callback_query(call.id, "Это не для вас 🙂", show_alert=True)
        return
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    bot.send_message(call.message.chat.id, _render_my_marriage(uid), parse_mode="HTML")
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

@bot.message_handler(func=lambda m: (m.text or '').strip().startswith('/start my_marriage'))
def start_my_marriage(message):
    if message.chat.type != 'private':
        return
    bot.send_message(message.chat.id, _render_my_marriage(message.from_user.id), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower().startswith('!браккоин'))
def cmd_marriage_coin(message):
    if not _bot_cd_ok(message):
        return
    parts = (message.text or "").strip().split()
    if len(parts) != 2:
        bot.reply_to(message, "❗ Используй так: <code>!браккоин 100000</code> (кратно 100 000)", parse_mode="HTML")
        return
    amount = parse_human_amount(parts[1])
    if amount <= 0:
        bot.reply_to(message, "❌ Сумма неверная.")
        return
    if amount < 100_000 or amount % 100_000 != 0:
        bot.reply_to(message, "❌ Минимум 100 000 и только кратно 100 000.")
        return

    pair_key, pair = _get_my_marriage(message.from_user.id)
    if not pair:
        bot.reply_to(message, "💔 У вас нет брака.")
        return

    data = load_data()
    u = get_user(data, message.from_user.id, message.from_user.first_name)
    if int(u.get("balance", 0)) < amount:
        bot.reply_to(message, "❌ Недостаточно 🪙.")
        return

    u["balance"] = int(u.get("balance", 0)) - amount
    save_data(data)

    m = _load_marriages()
    p = m.get("pairs", {}).get(pair_key)
    if not p:
        bot.reply_to(message, "💔 Брак не найден (возможно уже расторгнут).")
        return

    add_units = amount // 100_000  # 100k => 1 unit => 0.01 HP
    before_units = int(p.get("hp_units", 0))
    after_units = before_units + int(add_units)
    p["hp_units"] = after_units
    if int(p.get("u1")) == int(message.from_user.id):
        p["u1_name"] = message.from_user.first_name
    elif int(p.get("u2")) == int(message.from_user.id):
        p["u2_name"] = message.from_user.first_name
    m["pairs"][pair_key] = p
    _save_marriages(m)

    before_badge = _badge_for_hp_units(before_units)
    after_badge = _badge_for_hp_units(after_units)

    hp = _hp_units_to_float(after_units)
    msg = f"✅ {link_user_html(message.from_user.id, message.from_user.first_name)} пополнил(а) брак на <b>{format_balance(amount)}</b> 🪙\n⚜️ HP пары: <b>{hp:.2f}</b>"
    if after_badge and after_badge != before_badge:
        msg += f"\n🏅 Новая отчивка пары: <b>{after_badge}</b>"
    bot.send_message(message.chat.id, msg, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in ['!развод', 'развод'])
def cmd_divorce(message):
    if message.chat.type == 'private':
        return
    if not _bot_cd_ok(message):
        return

    # ✅ Админ-функция: ответь на сообщение игрока и напиши !развод
    # Разводит пару мгновенно, без подтверждения (чтобы чистить фармеров/ботов).
    if message.reply_to_message and getattr(message.reply_to_message, 'from_user', None):
        if _is_admin_or_owner(message.chat.id, message.from_user.id):
            target = message.reply_to_message.from_user
            try:
                if getattr(target, 'is_bot', False):
                    bot.reply_to(message, "🤖 У бота нет брака 🙂")
                    return
            except Exception:
                pass

            pair_key_t, pair_t = _get_my_marriage(int(target.id))
            if not pair_t:
                bot.reply_to(message, "💔 У этого игрока нет брака.")
                return

            # удаляем брак
            m = _load_marriages()
            pair_real = m.get("pairs", {}).get(pair_key_t)
            if pair_real:
                u1 = str(pair_real.get("u1"))
                u2 = str(pair_real.get("u2"))
                try:
                    m.get("user_to_pair", {}).pop(u1, None)
                    m.get("user_to_pair", {}).pop(u2, None)
                except Exception:
                    pass
                m.get("pairs", {}).pop(pair_key_t, None)
                _save_marriages(m)

            # чистим pending разводы по этой паре (если были)
            try:
                pend = _load_divorce_pending()
                dead = []
                for k, v in (pend or {}).items():
                    try:
                        if str(v.get("pair_key")) == str(pair_key_t):
                            dead.append(k)
                    except Exception:
                        continue
                for k in dead:
                    pend.pop(k, None)
                _save_divorce_pending(pend)
            except Exception:
                pass

            txt = (
                "⚖️ <b>РАСТОРЖЕНИЕ БРАКА</b>\n"
                "━━━━━━━━━━━━━━\n"
                f"Основание: решение администратора\n"
                f"Администратор: {link_user_html(message.from_user.id, message.from_user.first_name)}\n"
                f"Пара: {link_user_html(int(pair_t.get('u1')), pair_t.get('u1_name') or 'Игрок')}  +  "
                f"{link_user_html(int(pair_t.get('u2')), pair_t.get('u2_name') or 'Игрок')}\n"
                "━━━━━━━━━━━━━━\n"
                "Примечание: действие выполнено без подтверждения (анти-фарм/анти-бот)."
            )
            bot.send_message(message.chat.id, txt, parse_mode="HTML")
            return

    pair_key, pair = _get_my_marriage(message.from_user.id)
    if not pair:
        bot.reply_to(message, "💔 У вас нет брака.")
        return

    fee = 100_000
    data = load_data()
    u = get_user(data, message.from_user.id, message.from_user.first_name)
    if int(u.get("balance", 0)) < fee:
        bot.reply_to(message, "❌ Для развода нужно <b>100 000</b> 🪙.", parse_mode="HTML")
        return
    u["balance"] = int(u.get("balance", 0)) - fee
    save_data(data)

    u1 = int(pair.get("u1"))
    u2 = int(pair.get("u2"))
    partner_id = u2 if int(message.from_user.id) == u1 else u1
    partner_name = pair.get("u2_name") if partner_id == u2 else pair.get("u1_name")
    partner_link = link_user_html(partner_id, partner_name or "Игрок")

    pend = _load_divorce_pending()
    pend_key = f"{pair_key}:{message.chat.id}"
    pend[pend_key] = {
        "pair_key": pair_key,
        "chat_id": message.chat.id,
        "initiator_id": message.from_user.id,
        "partner_id": partner_id,
        "fee": fee,
        "ts": int(time.time())
    }
    _save_divorce_pending(pend)

    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(
        types.InlineKeyboardButton("✅ ДА", callback_data=f"div_yes:{pend_key}"),
        types.InlineKeyboardButton("❌ НЕТ", callback_data=f"div_no:{pend_key}")
    )
    bot.send_message(
        message.chat.id,
        f"💔 {partner_link}, вы хотите <b>развестись</b>?\n"
        f"━━━━━━━━━━━━━━\n"
        f"Заявку подал(а): {link_user_html(message.from_user.id, message.from_user.first_name)}\n"
        f"Списано за подачу: <b>{format_balance(fee)}</b> 🪙",
        parse_mode="HTML",
        reply_markup=mk
    )

@bot.callback_query_handler(func=lambda call: isinstance(call.data, str) and (call.data.startswith("div_yes:") or call.data.startswith("div_no:")))
def cb_divorce(call):
    action, pend_key = call.data.split(":", 1)
    pend = _load_divorce_pending()
    info = pend.get(pend_key)
    if not info:
        bot.answer_callback_query(call.id, "Заявка уже неактуальна.", show_alert=True)
        return

    if int(time.time()) - int(info.get("ts", 0)) > 600:
        try:
            data = load_data()
            initiator_id = str(info.get("initiator_id"))
            u = get_user(data, initiator_id)
            u["balance"] = int(u.get("balance", 0)) + int(info.get("fee", 0))
            save_data(data)
        except Exception:
            pass
        pend.pop(pend_key, None)
        _save_divorce_pending(pend)
        bot.answer_callback_query(call.id, "⏳ Время вышло. Заявка отменена.", show_alert=True)
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        return

    if int(call.from_user.id) != int(info.get("partner_id")):
        bot.answer_callback_query(call.id, "Это решение должен(на) принять второй(ая) половинка 🙂", show_alert=True)
        return

    if action == "div_no":
        try:
            data = load_data()
            initiator_id = str(info.get("initiator_id"))
            u = get_user(data, initiator_id)
            u["balance"] = int(u.get("balance", 0)) + int(info.get("fee", 0))
            save_data(data)
        except Exception:
            pass
        pend.pop(pend_key, None)
        _save_divorce_pending(pend)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            try:
                bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
            except Exception:
                pass
        bot.send_message(call.message.chat.id, "❌ Развод отменён.")
        bot.answer_callback_query(call.id)
        return

    pair_key_real = info.get("pair_key")
    m = _load_marriages()
    pair = m.get("pairs", {}).get(pair_key_real)
    if pair:
        u1 = str(pair.get("u1"))
        u2 = str(pair.get("u2"))
        m.get("user_to_pair", {}).pop(u1, None)
        m.get("user_to_pair", {}).pop(u2, None)
        m.get("pairs", {}).pop(pair_key_real, None)
        _save_marriages(m)

    pend.pop(pend_key, None)
    _save_divorce_pending(pend)

    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass

    bot.send_message(call.message.chat.id, "✅ 💔 Развод оформлен.")
    bot.answer_callback_query(call.id)

# =========================
# 🎰 РУЛЕТКА: меню (инлайн) + безопасное удаление (КАК ПРЕЖДЕ)
# =========================

def roulette_menu_text() -> str:
    return """🎲 <b>Минирулетка</b>
Угадайте число из:
<code>0💚</code>
<code>1🔴 2⚫️ 3🔴 4⚫️ 5🔴 6⚫️</code>
<code>7🔴 8⚫️ 9🔴 10⚫️ 11🔴 12⚫️</code>

<i>Ставки можно текстом:</i>
<code>10 красное</code> | <code>5 12</code>"""

def roulette_menu_markup() -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=4)

    # Диапазоны: ставит 1к на КАЖДОЕ число диапазона
    markup.add(
        types.InlineKeyboardButton("1-3", callback_data="range_1_3"),
        types.InlineKeyboardButton("4-6", callback_data="range_4_6"),
        types.InlineKeyboardButton("7-9", callback_data="range_7_9"),
        types.InlineKeyboardButton("10-12", callback_data="range_10_12"),
    )

    # Цвета: 1к на цвет
    markup.add(
        types.InlineKeyboardButton("1к на 🔴", callback_data="bet_1000_k"),
        types.InlineKeyboardButton("1к на ⚫️", callback_data="bet_1000_ch"),
        types.InlineKeyboardButton("1к на 💚", callback_data="bet_1000_z"),
    )

    # Управление
    markup.add(
        types.InlineKeyboardButton("Повторить", callback_data="repeat"),
        types.InlineKeyboardButton("Удвоить", callback_data="double_all"),
        types.InlineKeyboardButton("Крутить", callback_data="spin"),
    )
    return markup

# --- хранение последнего меню рулетки по чатам, чтобы удалять старое ---
roulette_last_menu_msg = {}  # chat_id(str) -> message_id(int)

def _delete_roulette_menu(chat_id: int):
    cid = str(chat_id)
    mid = roulette_last_menu_msg.get(cid)
    if not mid:
        return
    try:
        bot.delete_message(chat_id, mid)
    except Exception:
        pass
    roulette_last_menu_msg.pop(cid, None)

def send_roulette_menu(chat_id: int):
    # удаляем предыдущее меню (если было), чтобы не мусорить чат
    _delete_roulette_menu(chat_id)
    try:
        m = bot.send_message(chat_id, roulette_menu_text(), reply_markup=roulette_menu_markup(), parse_mode="HTML")
        roulette_last_menu_msg[str(chat_id)] = int(m.message_id)
        return m
    except Exception:
        return None

def send_links_pretty(chat_id: int):
    text = (
        "‼️ <b>Новости и обновления бота</b>\n"
        "━━━━━━━━━━━━━━\n"
        "💰 Канал: <b>@utxa_news</b>\n"
        "💳 Донат / покупка функций: <b>@tonvio</b>\n\n"
        "🎮 <b>Игровые чаты:</b>\n\n"
        "1️⃣ 🇷🇺 <b>Russia</b> — @utxa_russia\n"
        "2️⃣ 🇰🇬 <b>Kyrgyzstan</b> — @utxa_chat\n"
        "3️⃣ 🇺🇿 <b>Uzbekistan</b> — @utxa_uzb\n"
        "4️⃣ 🇰🇿 <b>Kazakhstan</b> — @utxa_kz\n"
    )

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("💰 Канал", url="https://t.me/utxa_news"),
        types.InlineKeyboardButton("💳 Донат", url="https://t.me/nutht"),
    )
    kb.add(
        types.InlineKeyboardButton("🇰🇬 KG", url="https://t.me/utxa_chat"),
        types.InlineKeyboardButton("🇷🇺 RU", url="https://t.me/utxa_russia"),
    )
    kb.add(
        types.InlineKeyboardButton("🇺🇿 UZ", url="https://t.me/utxa_uzb"),
        types.InlineKeyboardButton("🇰🇿 KZ", url="https://t.me/utxa_kz"),
    )

    bot.send_message(
        chat_id,
        text,
        parse_mode="HTML",
        reply_markup=kb,
        disable_web_page_preview=True
    )


colors = {
    'к': [1, 3, 5, 7, 9, 11],
    'ч': [2, 4, 6, 8, 10, 12],
    'з': [0]
}
HISTORY_FILE = 'users_history.json'


def load_history():
    """Читает users_history.json максимально безопасно.
    Если файл битый/пустой — возвращаем {} (и не падаем).
    """
    try:
        if not os.path.exists(HISTORY_FILE) or os.path.getsize(HISTORY_FILE) == 0:
            return {}
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception as e:
        # если файл сломан — переименуем, чтобы бот снова мог писать историю
        try:
            bad = HISTORY_FILE + f".bad_{int(time.time())}"
            shutil.move(HISTORY_FILE, bad)
            print(f"⚠️ users_history.json был поврежден. Переименован в {bad}")
        except Exception:
            pass
        return {}


def _save_history_file(history):
    """Атомарная запись истории (через .tmp), чтобы не ломалось при вылете."""
    tmp = HISTORY_FILE + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=4)
        shutil.move(tmp, HISTORY_FILE)
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        print(f"Ошибка записи истории: {e}")


def add_event(user_id, text):
    history = load_history()
    uid = str(user_id)
    if uid not in history:
        history[uid] = []

    # Получаем текущее время в формате [HH:MM:SS]
    current_time = time.strftime("[%H:%M:%S]")
    history[uid].append(f"{current_time} {text}")

    # Храним только последние 20 записей для каждого
    if len(history[uid]) > 20:
        history[uid] = history[uid][-20:]

    _save_history_file(history)


def load_data():
    """Загрузка балансов с защитой от потери данных.
    Если основной файл пуст/битый — пытаемся восстановить из .bak.
    """
    with _DATA_LOCK:
        # если файла нет — пустая база
        if not os.path.exists(DATA_FILE):
            return {}

        def _try_load(path_):
            if not os.path.exists(path_):
                return None
            if os.path.getsize(path_) == 0:
                return None
            try:
                with open(path_, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                return d if isinstance(d, dict) else None
            except Exception:
                return None

        data = _try_load(DATA_FILE)
        if data is not None:
            return data

        # основной файл пуст/поврежден — пробуем бэкап
        backup = _try_load(BACKUP_FILE)
        if backup is not None:
            print("⚠️ Восстановление балансов из бэкапа .bak (основной файл был пуст/битый).")
            # попытка восстановить основной
            try:
                tmp_file = DATA_FILE + '.restore.tmp'
                with open(tmp_file, 'w', encoding='utf-8') as f:
                    json.dump(backup, f, ensure_ascii=False, indent=4)
                shutil.move(tmp_file, DATA_FILE)
            except Exception:
                pass
            return backup

        # ничего не удалось
        print("ОШИБКА: Файл балансов пуст/поврежден и бэкап не найден. Балансы НЕ будут перезаписаны автоматически.")
        return {}

LOG_FILE = 'results_log.json'


def save_log_to_file():
    global results_log
    # Используем временный файл .tmp, чтобы если бот вылетит, основной лог не стерся
    tmp_file = LOG_FILE + '.tmp'
    try:
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(results_log, f, ensure_ascii=False, indent=4)
        # Если записалось успешно, заменяем старый файл новым
        import shutil
        shutil.move(tmp_file, LOG_FILE)
    except Exception as e:
        print(f"Ошибка при сохранении лога: {e}")


def load_log_from_file():
    global results_log
    if os.path.exists(LOG_FILE):
        if os.path.getsize(LOG_FILE) == 0:
            results_log = {}  # Файл пуст
            return

        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                # Проверяем, что загрузился именно словарь (для раздельных логов)
                if isinstance(data, dict):
                    results_log = data
                else:
                    results_log = {}  # Если там старый формат (список), сбрасываем
            except Exception as e:
                print(f"Ошибка загрузки лога: {e}")
                results_log = {}
    else:
        results_log = {}

# Вызовите загрузку сразу, чтобы при старте бот прочитал файл
load_log_from_file()

@bot.message_handler(commands=['unlimit'])
def cmd_unlimit(message):
    if message.from_user.id not in ADMIN_IDS: return
    try:
        target_id = message.text.split()[1]
        data = load_data()
        if 'whitelist' not in data: data['whitelist'] = []
        if target_id not in data['whitelist']:
            data['whitelist'].append(str(target_id))
            save_data(data)
        bot.reply_to(message, f"✅ Лимит для <code>{target_id}</code> снят!", parse_mode="HTML")
    except:
        bot.reply_to(message, "Пример: <code>/unlimit 1234567</code>", parse_mode="HTML")

@bot.message_handler(commands=['limit'])
def cmd_limit(message):
    if message.from_user.id not in ADMIN_IDS: return
    try:
        target_id = message.text.split()[1]
        data = load_data()
        if 'whitelist' in data and str(target_id) in data['whitelist']:
            data['whitelist'].remove(str(target_id))
            save_data(data)
        bot.reply_to(message, f"⚠️ Лимит для <code>{target_id}</code> возвращен.")
    except:
        bot.reply_to(message, "Пример: <code>/limit 1234567</code>", parse_mode="HTML")



@bot.message_handler(commands=['vor'])
def cmd_vor(message):
    """Админ-команда: выдать доступ к !ограбить.
Использование:
- /vor 123456789
- ответом на сообщение: /vor
"""
    if message.from_user.id not in ADMIN_IDS:
        return

    target_id = None
    parts = (message.text or "").split()
    if len(parts) >= 2 and parts[1].isdigit():
        target_id = parts[1]
    elif message.reply_to_message:
        try:
            target_id = str(message.reply_to_message.from_user.id)
        except Exception:
            target_id = None

    if not target_id:
        bot.reply_to(message, "Пример: <code>/vor 123456789</code> (или ответь на сообщение и напиши /vor)", parse_mode="HTML")
        return

    grant_robbery_access(str(target_id))
    bot.reply_to(message, f"✅ Доступ к <code>!ограбить</code> выдан: <code>{target_id}</code>", parse_mode="HTML")


@bot.message_handler(commands=['unvor'])
def cmd_unvor(message):
    """Админ-команда: забрать доступ к !ограбить."""
    if message.from_user.id not in ADMIN_IDS:
        return

    target_id = None
    parts = (message.text or "").split()
    if len(parts) >= 2 and parts[1].isdigit():
        target_id = parts[1]
    elif message.reply_to_message:
        try:
            target_id = str(message.reply_to_message.from_user.id)
        except Exception:
            target_id = None

    if not target_id:
        bot.reply_to(message, "Пример: <code>/unvor 123456789</code> (или ответь на сообщение и напиши /unvor)", parse_mode="HTML")
        return

    revoke_robbery_access(str(target_id))
    bot.reply_to(message, f"✅ Доступ к <code>!ограбить</code> убран: <code>{target_id}</code>", parse_mode="HTML")

@bot.message_handler(commands=['botsearch'])
def cmd_botsearch(message):
    """Админ: выдать доступ к платной команде !бот ищи.
Использование:
- /botsearch 123
- ответом на сообщение: /botsearch
"""
    if message.from_user.id not in ADMIN_IDS:
        return

    target_id = None
    parts = (message.text or '').split()
    if len(parts) >= 2 and parts[1].isdigit():
        target_id = parts[1]
    elif message.reply_to_message:
        try:
            target_id = str(message.reply_to_message.from_user.id)
        except Exception:
            target_id = None

    if not target_id:
        bot.reply_to(message, "Пример: <code>/botsearch 123456789</code> (или ответь и напиши /botsearch)", parse_mode="HTML")
        return

    grant_botsearch_access(str(target_id))
    bot.reply_to(message, f"✅ Доступ к <code>!бот ищи</code> выдан: <code>{target_id}</code>", parse_mode="HTML")


@bot.message_handler(commands=['unbotsearch'])
def cmd_unbotsearch(message):
    """Админ: забрать доступ к !бот ищи."""
    if message.from_user.id not in ADMIN_IDS:
        return

    target_id = None
    parts = (message.text or '').split()
    if len(parts) >= 2 and parts[1].isdigit():
        target_id = parts[1]
    elif message.reply_to_message:
        try:
            target_id = str(message.reply_to_message.from_user.id)
        except Exception:
            target_id = None

    if not target_id:
        bot.reply_to(message, "Пример: <code>/unbotsearch 123456789</code> (или ответь и напиши /unbotsearch)", parse_mode="HTML")
        return

    revoke_botsearch_access(str(target_id))
    bot.reply_to(message, f"✅ Доступ к <code>!бот ищи</code> убран: <code>{target_id}</code>", parse_mode="HTML")

def get_user(data, user_id, first_name="Игрок"):
    user_id = str(user_id)
    if user_id not in data:
        data[user_id] = {
            'balance': 100000,
            'last_bonus': 0,
            'first_name': first_name,
            'won': 0, 'lost': 0, 'max_bet': 0, 'max_win': 0,
            'bank': 0,
            'bank_last_action': 0
        }
    else:
        # Если в базе "Игрок", а телеграм прислал имя — обновляем
        if first_name and first_name != "Игрок":
            data[user_id]['first_name'] = first_name
        data[user_id].setdefault('bank', 0)
    data[user_id].setdefault('bank_last_action', 0)
    # referral / anti-spam stats
    data[user_id].setdefault('ref_by', None)          # кто пригласил (uid)
    data[user_id].setdefault('ref_pending', False)    # ждём 3 ставки
    data[user_id].setdefault('ref_rewarded', False)   # награда уже выдана
    data[user_id].setdefault('ref_count', 0)          # сколько людей привёл
    data[user_id].setdefault('roulette_bets_count', 0)
    return data[user_id]



# --- 1. ГЛАВНОЕ МЕНЮ (НИЖНЕЕ) ---
def main_menu_keyboard(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(types.KeyboardButton("Профиль"), types.KeyboardButton("🔗 Ссылки"))
    # Банк — платная функция: показываем кнопку только купившим (или админу)
    if (user_id in ADMIN_IDS) or has_bank_access(str(user_id)):
        markup.add(types.KeyboardButton("🛒 Магазин"), types.KeyboardButton("🏦 Банк"))
    else:
        markup.add(types.KeyboardButton("🛒 Магазин"))
    # ✅ Вернули кнопку помощи
    markup.add(types.KeyboardButton("❓ Помощь"), types.KeyboardButton("👥 Рефералы"))
    if user_id in ADMIN_IDS:
        markup.add(types.KeyboardButton("🛠 Админ-панель"))
    return markup


# =========================
# 👥 РЕФЕРАЛЫ
# - награда: 1 000 000 🪙 за каждого НОВОГО пользователя по ссылке
# =========================
REF_REWARD = 1_000_000
_bot_username_cache = None

# =========================
# 🎯 РУЛЕТКА: ВАЛИДАЦИЯ ЦЕЛЕЙ СТАВКИ + РЕФЕРАЛКА (анти-спам)
# =========================
VALID_NUMBERS = set(str(i) for i in range(0, 13))
VALID_COLORS = {'к', 'ч', 'з'}  # красн/черн/зел (з также используется для зеро-логики через '0')

def normalize_bet_target(t: str) -> str:
    t = (t or "").strip().lower()
    if t in ['красное', 'крас', 'к', 'k', 'red', 'r']:
        return 'к'
    if t in ['черное', 'черн', 'ч', 'ch', 'black', 'b']:
        return 'ч'
    if t in ['зеленое', 'зел', 'з', 'z', 'green', 'g']:
        return 'з'
    # зеро словами -> 0
    if t in ['зеро', 'zero', '0']:
        return '0'
    return t

def is_valid_bet_value(v: str) -> bool:
    v = (v or "").strip().lower()
    v = normalize_bet_target(v)
    if v in VALID_COLORS:
        return True
    if v.isdigit():
        return v in VALID_NUMBERS
    if '-' in v:
        try:
            a, b = v.split('-', 1)
            a = int(a); b = int(b)
            return 0 <= min(a, b) <= 12 and 0 <= max(a, b) <= 12
        except Exception:
            return False
    return False

def _maybe_finalize_ref_reward(data: dict, user_id: int):
    """Выдаём награду рефереру после 3 ставок у приглашённого."""
    try:
        u = get_user(data, user_id)
        if not u.get('ref_pending'):
            return
        ref_by = u.get('ref_by')
        if not ref_by:
            u['ref_pending'] = False
            return
        if bool(u.get('ref_rewarded')):
            u['ref_pending'] = False
            return
        if int(u.get('roulette_bets_count', 0)) < 3:
            return

        ref_user = get_user(data, str(ref_by), "Игрок")
        ref_user['balance'] = int(ref_user.get('balance', 0)) + int(REF_REWARD)
        ref_user['ref_count'] = int(ref_user.get('ref_count', 0)) + 1

        u['ref_pending'] = False
        u['ref_rewarded'] = True

        try:
            bot.send_message(int(ref_by), f"🎉 По твоей ссылке игрок сделал 3 ставки — начислено <b>{format_balance(REF_REWARD)}</b> 🪙!", parse_mode="HTML")
        except Exception:
            pass
    except Exception:
        pass


def _bot_username() -> str:
    global _bot_username_cache
    if _bot_username_cache:
        return _bot_username_cache
    try:
        me = bot.get_me()
        _bot_username_cache = getattr(me, "username", "") or ""
    except Exception:
        _bot_username_cache = ""
    return _bot_username_cache

def _ref_link(user_id: int) -> str:
    u = _bot_username()
    if u:
        return f"https://t.me/{u}?start=ref_{int(user_id)}"
    return f"t.me/<bot>?start=ref_{int(user_id)}"

def _is_new_user_in_db(data: dict, user_id: int) -> bool:
    try:
        return str(user_id) not in (data or {})
    except Exception:
        return True

def _apply_referral_if_needed(message):
    """Обрабатываем /start ref_<id>.
    Награда НЕ выдаётся сразу: ждём пока реферал сделает 3 ставки в рулетке.
    """
    try:
        parts = (message.text or "").split(maxsplit=1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        if not payload.startswith("ref_"):
            return

        ref_id_str = payload.replace("ref_", "", 1).strip()
        if not ref_id_str.isdigit():
            return
        ref_id = int(ref_id_str)
        new_uid = int(message.from_user.id)
        if ref_id <= 0 or ref_id == new_uid:
            return

        data = load_data()
        # создаём/обновляем запись пользователя
        u = get_user(data, new_uid, getattr(message.from_user, 'first_name', 'Игрок'))

        # реф уже был привязан? — ничего не делаем
        if u.get('ref_by') or u.get('ref_pending') or u.get('ref_rewarded'):
            save_data(data)
            return

        # не даём накрутку, если реферера нет в базе — всё равно разрешим, но создадим
        get_user(data, str(ref_id), 'Игрок')

        u['ref_by'] = int(ref_id)
        u['ref_pending'] = True
        u['ref_rewarded'] = False
        u['roulette_bets_count'] = int(u.get('roulette_bets_count', 0))

        save_data(data)

        # уведомления
        try:
            bot.send_message(message.chat.id, "✅ Реферальная ссылка принята! Сделай 3 ставки в рулетке, и награда будет начислена рефереру.")
        except Exception:
            pass
        try:
            bot.send_message(ref_id, f"👤 Новый реферал перешёл по твоей ссылке: {link_user_html(new_uid, getattr(message.from_user,'first_name','Игрок'))}\n\nℹ️ Награда ({format_balance(REF_REWARD)} 🪙) придёт после 3 ставок у реферала.", parse_mode="HTML")
        except Exception:
            pass
    except Exception:
        pass
        ref_id_str = payload.replace("ref_", "", 1).strip()
        if not ref_id_str.isdigit():
            return
        ref_id = int(ref_id_str)
        if ref_id <= 0:
            return

        new_user_id = int(message.from_user.id)
        if ref_id == new_user_id:
            return

        data = load_data()

        # "новый" = нет в базе вообще
        if not _is_new_user_in_db(data, new_user_id):
            return

        # создаём нового пользователя в базе
        nu = get_user(data, str(new_user_id), getattr(message.from_user, "first_name", "Игрок"))
        nu['referred_by'] = int(ref_id)

        # начисляем рефереру
        ref_u = get_user(data, str(ref_id), "Игрок")
        ref_u['balance'] = int(ref_u.get('balance', 0)) + int(REF_REWARD)
        ref_u['ref_count'] = int(ref_u.get('ref_count', 0)) + 1

        save_data(data)

        # уведомим реферера
        try:
            name = safe_html(getattr(message.from_user, "first_name", "Игрок"))
            bot.send_message(
                ref_id,
                f"🎉 Новый реферал: <a href='tg://user?id={new_user_id}'>{name}</a>\n"
                f"➕ Награда: <b>{format_balance(REF_REWARD)}</b> 🪙",
                parse_mode="HTML"
            )
        except Exception:
            pass
    except Exception:
        pass

def send_referrals_selector(message):
    """Кнопка меню '👥 Рефералы'. В группе — редирект в ЛС."""
    try:
        if message.chat.type != 'private':
            u = _bot_username()
            if not u:
                bot.reply_to(message, "👥 Рефералы доступны в ЛС. Напиши боту /start")
                return
            url = f"https://t.me/{u}?start=refmenu"
            mk = types.InlineKeyboardMarkup()
            mk.add(types.InlineKeyboardButton("👥 Открыть рефералы в ЛС", url=url))
            bot.reply_to(message, "👥 Рефералы доступны в ЛС. Нажми кнопку 👇", reply_markup=mk)
            return

        mk = types.InlineKeyboardMarkup()
        mk.add(
            types.InlineKeyboardButton("👥 Рефералы", callback_data="ref_my"),
            types.InlineKeyboardButton("🏆 Топ рефералы", callback_data="ref_top"),
        )
        bot.send_message(message.chat.id, "👥 <b>Реферальная система</b>", reply_markup=mk, parse_mode="HTML")
    except Exception:
        pass

@bot.callback_query_handler(func=lambda call: getattr(call, "data", "") in ["ref_my", "ref_top"])
def _ref_callbacks(call):
    if not _cb_is_fresh(call):
        return
    try:
        uid = int(call.from_user.id)
        data = load_data()
        u = get_user(data, str(uid), getattr(call.from_user, "first_name", "Игрок"))

        if call.data == "ref_my":
            link = _ref_link(uid)
            cnt = int(u.get('ref_count', 0))
            text = (
                "👥 <b>Ваши рефералы</b>\n"
                "━━━━━━━━━━━━━━\n"
                f"👤 Приглашено: <b>{cnt}</b>\n\n"
                f"🔗 Ваша ссылка:\n<code>{link}</code>\n\n"
                f"💰 Награда за каждого: <b>{format_balance(REF_REWARD)}</b> 🪙"
            )
            bot.send_message(call.message.chat.id, text, parse_mode="HTML", disable_web_page_preview=True)

        else:
            items = []
            for k, v in (data or {}).items():
                try:
                    if not str(k).isdigit() or not isinstance(v, dict):
                        continue
                    c = int(v.get('ref_count', 0))
                    if c > 0:
                        items.append((int(k), v.get('first_name', 'Игрок'), c))
                except Exception:
                    continue
            items.sort(key=lambda x: x[2], reverse=True)
            top = items[:10]

            if not top:
                bot.send_message(call.message.chat.id, "🏆 Пока нет рефералов в топе.")
            else:
                lines = ["🏆 <b>Топ рефералы</b>", "━━━━━━━━━━━━━━"]
                for i, (rid, name, c) in enumerate(top, 1):
                    lines.append(f"{i}. <a href='tg://user?id={rid}'>{safe_html(name)}</a> — <b>{c}</b>")
                bot.send_message(call.message.chat.id, "\n".join(lines), parse_mode="HTML")

        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
    except Exception:
        try:
            bot.answer_callback_query(call.id, "Ошибка", show_alert=False)
        except Exception:
            pass


# =========================
# 🏦 БАНК (только в ЛС)
# - деньги в банке НЕ участвуют в ограблениях и топах
# - пополнение/вывод с КД 60 сек, чтобы не спамили
# =========================
BANK_CD_SECONDS = 60
_BANK_PENDING = {}  # user_id(str) -> 'deposit' | 'withdraw'

def bank_keyboard() -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(types.KeyboardButton("💸 Вложить"), types.KeyboardButton("💳 Вывести"))
    kb.add(types.KeyboardButton("⬅️ Назад"))
    return kb

def _go_main_menu(chat_id: int, user_id: int):
    bot.send_message(chat_id, "🏠 Главное меню", reply_markup=main_menu_keyboard(user_id))

def show_bank(message):
    # Банк работает ТОЛЬКО в ЛС
    if message.chat.type != 'private':
        # даём кнопку перехода в ЛС
        if BOT_USERNAME:
            url = _deep_link_url("bank")
            mk = types.InlineKeyboardMarkup()
            if url:
                mk.add(types.InlineKeyboardButton("🏦 Открыть банк в ЛС", url=url))
            bot.reply_to(message, "ℹ️ Банк работает только в ЛС бота.", reply_markup=mk)
        else:
            bot.reply_to(message, "ℹ️ Банк работает только в ЛС бота. Открой бота и нажми: 🏦 Банк")
        return

    # Проверка покупки банка (кроме админов)
    requester_id = str(message.from_user.id)
    if (int(requester_id) not in ADMIN_IDS) and (not has_bank_access(requester_id)):
        if BOT_USERNAME:
            url = _deep_link_url("shop")
            mk = types.InlineKeyboardMarkup()
            if url:
                mk.add(types.InlineKeyboardButton("🛒 Открыть магазин", url=url))
            bot.send_message(message.chat.id, f"🔒 <b>Банк</b> — платная функция (цена: {SHOP_PRICE_STARS}⭐️).\nКупи в магазине и напиши владельцу в ЛС.", parse_mode="HTML", reply_markup=mk)
        else:
            bot.send_message(message.chat.id, f"🔒 Банк — платная функция (цена: {SHOP_PRICE_STARS}⭐️). Открой бота и нажми 🛒 Магазин.")
        return


    data = load_data()
    u = get_user(data, message.from_user.id, message.from_user.first_name)
    bank_amount = int(u.get('bank', 0))
    text = (
        "🏦 <b>Банк</b> (личный)\n"
        "━━━━━━━━━━━━━━\n"
        f"💰 В банке: <b>{format_balance(bank_amount)}</b> 🪙\n"
        "━━━━━━━━━━━━━━\n"
        "💡 Деньги в банке не видны другим и не попадают под ограбления/топы.\n"
        f"⏳ КД на операции: <b>{BANK_CD_SECONDS} сек</b>."
    )
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=bank_keyboard())

@bot.message_handler(commands=['bank'])
def bank_cmd(message):
    """Команда /bank:
- обычным игрокам: открыть банк (если куплен)
- админу: /bank USER_ID (или ответом) — выдать доступ к банку
"""
    # Админ-выдача доступа
    if message.from_user.id in ADMIN_IDS:
        target_id = None
        parts = (message.text or '').split()
        if len(parts) >= 2 and parts[1].isdigit():
            target_id = parts[1]
        elif message.reply_to_message:
            try:
                target_id = str(message.reply_to_message.from_user.id)
            except Exception:
                target_id = None

        if target_id:
            grant_bank_access(str(target_id))
            bot.reply_to(message, f"✅ Доступ к 🏦 Банку выдан: <code>{target_id}</code>", parse_mode="HTML")
            return

    # Иначе — просто открыть банк
    show_bank(message)

@bot.message_handler(commands=['unbank'])
def unbank_cmd(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    target_id = None
    parts = (message.text or '').split()
    if len(parts) >= 2 and parts[1].isdigit():
        target_id = parts[1]
    elif message.reply_to_message:
        try:
            target_id = str(message.reply_to_message.from_user.id)
        except Exception:
            target_id = None
    if not target_id:
        bot.reply_to(message, "Пример: <code>/unbank USER_ID</code> или ответь на сообщение и напиши <code>/unbank</code>", parse_mode="HTML")
        return
    revoke_bank_access(str(target_id))
    bot.reply_to(message, f"🗑 Доступ к 🏦 Банку снят: <code>{target_id}</code>", parse_mode="HTML")


def _bank_cd_left(u: dict) -> int:
    last_ts = float(u.get('bank_last_action', 0) or 0)
    left = int(BANK_CD_SECONDS - (time.time() - last_ts))
    return left if left > 0 else 0

@bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text in ["🏦 Банк"])
def bank_button_handler(message):
    show_bank(message)

@bot.message_handler(func=lambda m: m.chat.type == 'private' and m.text in ["💸 Вложить", "💳 Вывести", "⬅️ Назад"])
def bank_actions_handler(message):
    uid = str(message.from_user.id)

    if message.text == "⬅️ Назад":
        _BANK_PENDING.pop(uid, None)
        _go_main_menu(message.chat.id, message.from_user.id)
        return

    data = load_data()
    u = get_user(data, uid, message.from_user.first_name)
    left = _bank_cd_left(u)
    if left > 0:
        bot.reply_to(message, f"⏳ Подожди <b>{left} сек</b> перед следующей операцией банка.", parse_mode="HTML")
        return

    if message.text == "💸 Вложить":
        _BANK_PENDING[uid] = 'deposit'
        bot.send_message(message.chat.id, "💸 Введи сумму для <b>вклада</b> (числом):", parse_mode="HTML", reply_markup=bank_keyboard())
    elif message.text == "💳 Вывести":
        _BANK_PENDING[uid] = 'withdraw'
        bot.send_message(message.chat.id, "💳 Введи сумму для <b>вывода</b> (числом):", parse_mode="HTML", reply_markup=bank_keyboard())

@bot.message_handler(func=lambda m: m.chat.type == 'private' and str(m.from_user.id) in _BANK_PENDING and m.text and re.fullmatch(r"\d+", m.text.strip() or "") is not None)
def bank_amount_input_handler(message):
    uid = str(message.from_user.id)
    action = _BANK_PENDING.get(uid)

    try:
        amount = int((message.text or "").strip())
    except Exception:
        amount = 0

    if amount <= 0:
        bot.reply_to(message, "❌ Сумма должна быть больше 0.")
        return

    data = load_data()
    u = get_user(data, uid, message.from_user.first_name)

    left = _bank_cd_left(u)
    if left > 0:
        bot.reply_to(message, f"⏳ Подожди <b>{left} сек</b> перед следующей операцией банка.", parse_mode="HTML")
        return

    if action == 'deposit':
        if int(u.get('balance', 0)) < amount:
            bot.reply_to(message, "❌ Недостаточно 🪙 на балансе.")
            return
        u['balance'] = int(u.get('balance', 0)) - amount
        u['bank'] = int(u.get('bank', 0)) + amount
        u['bank_last_action'] = time.time()
        save_data(data)
        _BANK_PENDING.pop(uid, None)
        bot.send_message(message.chat.id, f"✅ Вклад выполнен: <b>{format_balance(amount)}</b> 🪙", parse_mode="HTML")
        show_bank(message)
        return

    if action == 'withdraw':
        if int(u.get('bank', 0)) < amount:
            bot.reply_to(message, "❌ Недостаточно 🪙 в банке.")
            return
        u['bank'] = int(u.get('bank', 0)) - amount
        u['balance'] = int(u.get('balance', 0)) + amount
        u['bank_last_action'] = time.time()
        save_data(data)
        _BANK_PENDING.pop(uid, None)
        bot.send_message(message.chat.id, f"✅ Вывод выполнен: <b>{format_balance(amount)}</b> 🪙", parse_mode="HTML")
        show_bank(message)
        return

    _BANK_PENDING.pop(uid, None)
    bot.reply_to(message, "❌ Операция отменена. Открой банк заново: 🏦 Банк")


# --- 2. КОМАНДА СТАРТ ---
@bot.message_handler(commands=['start'])
def start(message):
    # Deep-link: /start bonus -> сразу выдать бонус (только в ЛС)
    try:
        parts = (message.text or "").split(maxsplit=1)
        # Рефералка: /start ref_<id>
        _apply_referral_if_needed(message)

        if message.chat.type == 'private' and len(parts) > 1 and parts[1].strip().lower() == 'bonus':
            bonus_cmd(message)
            return

        if message.chat.type == 'private' and len(parts) > 1 and parts[1].strip().lower() == 'bank':
            show_bank(message)
            return

        if message.chat.type == 'private' and len(parts) > 1 and parts[1].strip().lower() == 'shop':
            bot.send_message(
                message.chat.id,
                shop_message_text(message.from_user.id),
                reply_markup=shop_message_markup(message.from_user.id),
                parse_mode="HTML"
            )
            return
    except Exception:
        pass

    # В группах /start НЕ показывает меню как в ЛС
    if message.chat.type in ['group', 'supergroup']:
        try:
            bot.send_message(message.chat.id, roulette_menu_text(), parse_mode='HTML')
        except Exception:
            try:
                bot.send_message(message.chat.id, roulette_menu_text(), parse_mode='HTML')
            except Exception:
                pass
        return

    welcome_text = (
        "💎 <b>Добро пожаловать в UTXA!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "✨ <i>Твоя атмосфера. Твой вайб. Твоя игра!</i>\n\n"
        "🎰 Погрузись в мир азарта. Мы создали это для твоего комфорта!\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📢 <b>Канал:</b> @utxa_news\n"
        "💬 <b>Чат:</b> @utxa_chat"
    )

    bot.send_message(
        message.chat.id,
        welcome_text,
        reply_markup=main_menu_keyboard(message.from_user.id),
        parse_mode="HTML"
    )


# =========================
# ❓ ПОМОЩЬ
# - нижняя кнопка "❓ Помощь"
# - инлайн выбор: игрок / админ
# - после нажатия меню удаляется (без спама)
# =========================
HELP_TEXT_PLAYER = (
    "<b>Мини игры UTXA</b>\n\n"
    "Рулетка, Банда, Розыгрыш\n"
    "---------------------------------------\n"
    "<b>Команды для рулетки:</b>\n\n"
    "Профиль - просмотр профиля\n"
    "Б - просмотр баланса\n"
    "Ссылки - официальные чаты\n"
    "100 к - ставка на красное\n"
    "100 ч - ставка на чёрное\n"
    "100 1 - ставка на цифру от 0 до 12\n"
    "100 0 - ставка на зеро\n"
    "100 1-3 - ставка через тире от 0 до 12\n"
    "+20000 - передача монет в ответ на сообщение, другому игроку\n\n"
    "Повторить - (повторяет вашу предыдущую ставку)\n"
    "Удвоить - (удваивает вашу ставку)\n"
    "Отмена - (отменяет вашу ставку)\n"
    "Го - (крутит рулетку)\n\n"
    "<b>Иксы при выигрыше в рулетку:</b>\n\n"
    "На цвет - х2\n"
    "На цифру - х12\n"
    "На зеро - х12 (возврат 50% для остальных ставок)\n"
    "---------------------------------------\n"
    "<b>Команда для банды:</b>\n\n"
    "Банда 100\n\n"
    "<b>Иксы при выигрыше в банду:</b>\n\n"
    "x3, x4, x5, x8, x12, x16\n"
    "---------------------------------------\n"
    "<b>Команда для розыгрыша:</b>\n\n"
    "!роз 25000\n\n"
    "Розыгрыш можно сделать до 25000 монет в котором могут участвовать от 2 до 150 участников"
    "\n---------------------------------------\n"
    "<b>Бонус / Ферма / Раздача:</b>\n\n"
    "🎁 Бонус: <code>бонус</code> / <code>/bonus</code> / <code>!бонус</code>\n"
    "— В чате: бот даст кнопку и отправит в ЛС\n"
    "— В ЛС: проверит подписку @utxa_news и выдаст бонус\n\n"
    "⛏ Криптофермер: <code>криптофермер</code> или <code>!криптофермер</code> (копит до 200 000 🪙 за 30 минут)\n"
    "🔥 Раздача активным: <code>!fire СУММА</code> (делит сумму между активными из последних 100 сообщений, кроме автора)\n"
    "\n---------------------------------------\n"
    "<b>💍 Свадьба / Браки:</b>\n\n"
    "<code>!поженить @юзер1 @юзер2</code> — только регистратор\n"
    "<code>!браки</code> — топ браков по ⚜️ HP (время не видно)\n"
    "<code>!мой брак</code> / <code>!брак</code> / <code>!мойб</code> — показать свой брак\n"
    "<code>!браккоин 100000</code> — сжечь 🪙 и поднять HP (100 000 = 0.01 HP, 1 000 000 = 0.1 HP, 10 000 000 = 1 HP)\n"
    "<code>!развод</code> — развод (спишет 100 000 🪙 и попросит подтверждение у второй половинки)\n\n"
    "🏅 Отчивки пары (значок перед ником во всех играх/топах):\n"
    "5 HP = ✿  | 10 HP = ✸  | 20 HP = ❃  | 50 HP = ❄\n\n"
    "Прочитайте внимательно!\n"
    "https://telegra.ph/UTXA-02-13-2\n"

)


# =========================
# ❓ HELP: разделяем помощь для игрока по разделам (чтобы не спамить текстом)
# =========================

HELP_PLAYER_SECTIONS = {
    "roulette": (
        "🎰 <b>Рулетка</b>\n"
        "━━━━━━━━━━━━━━\n"
        "• <code>Профиль</code> — профиль\n"
        "• <code>Б</code> — баланс\n"
        "• <code>Ссылки</code> — официальные чаты\n\n"
        "<b>Ставки:</b>\n"
        "• <code>100 к</code> — на красное\n"
        "• <code>100 ч</code> — на чёрное\n"
        "• <code>100 з</code> — на зелёное (0)\n"
        "• <code>100 7</code> — на цифру 0–12\n"
        "• <code>100 1-3</code> — на диапазон\n\n"
        "<b>Управление:</b>\n"
        "• <code>Повторить</code> — повтор ставки\n"
        "• <code>Удвоить</code> — удвоить\n"
        "• <code>Отмена</code> — отменить\n"
        "• <code>Го</code> — крутить\n\n"
        "💸 Передача: <code>+20000</code> (ответом на сообщение)"
    ),
    "coin": (
        "🪙 <b>Орёл / Решка</b>\n"
        "━━━━━━━━━━━━━━\n"
        "• Ответь на сообщение игрока и напиши: <code>!монетка 50000</code>\n"
        "  или коротко: <code>!мон 50000</code>\n"
        "• Отменить игру админом: <code>!monoff</code>"
    ),
    "marriage": (
        "💍 <b>Браки</b>\n"
        "━━━━━━━━━━━━━━\n"
        "• <code>!браки</code> — топ браков\n"
        "• <code>!мой брак</code> / <code>!брак</code> / <code>!мойб</code> — мой брак\n"
        "• <code>!браккоин 1000</code> — повысить HP (курс зависит от настроек)\n"
        "• <code>!развод</code> — подать на развод (подтверждает второй человек)"
    ),
    "bonus": (
        "🎁 <b>Бонусы</b>\n"
        "━━━━━━━━━━━━━━\n"
        "• <code>бонус</code> / <code>/bonus</code> / <code>!бонус</code> — получить бонус\n"
        "• Если команда в чате — бот перенаправит в ЛС (как и раньше)."
    ),
    "giveaway": (
        "🎁 <b>Розыгрыши</b>\n"
        "━━━━━━━━━━━━━━\n"
        "• <code>!роз 25000</code> — создать розыгрыш\n"
        "• <code>!ботроз</code> — участвовать (если активен)\n"
        "• Приз распределяется по правилам розыгрыша."
    ),
}

def help_player_sections_markup() -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(
        types.InlineKeyboardButton("🎰 Рулетка", callback_data="help_p:roulette"),
        types.InlineKeyboardButton("🪙 Орёл/Решка", callback_data="help_p:coin"),
        types.InlineKeyboardButton("💍 Браки", callback_data="help_p:marriage"),
        types.InlineKeyboardButton("🎁 Бонусы", callback_data="help_p:bonus"),
        types.InlineKeyboardButton("🎁 Розыгрыши", callback_data="help_p:giveaway"),
        types.InlineKeyboardButton("❌ Закрыть", callback_data="help_p:close"),
    )
    return mk

def help_player_back_markup() -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(
        types.InlineKeyboardButton("⬅️ Назад", callback_data="help_p:back"),
        types.InlineKeyboardButton("❌ Закрыть", callback_data="help_p:close"),
    )
    return mk



HELP_TEXT_ADMIN = """<b>🛠 Помощь админа (UTXA)</b>
━━━━━━━━━━━━━━
<b>🚫 Модерация (ответом на сообщение):</b>
• <code>!бан</code> — бан игрока
• <code>!мут 10</code> — мут на N секунд
• <code>!размут</code> — снять мут
• <code>!бот стоп</code> — запретить игроку писать

<b>💍 Браки / анти-фарм:</b>
• <code>!развод</code> — <b>админ-развод</b> (ответом на игрока, без подтверждения)
• <code>!поженить</code> — свадьба (только регистратор/владелец; можно @ или ответом)

<b>🎮 Управление играми:</b>
• <code>!ron</code> / <code>!roff</code> — вкл/выкл рулетку
• <code>!gon</code> / <code>!goff</code> — вкл/выкл «Банда»
• <code>!monoff</code> — отменить активную «Монетку» в чате

<b>📊 Чат-статистика:</b>
• <code>!стата</code> — топ сообщений за 24 часа (в этом чате)

<b>👑 Владелец (только ADMIN_IDS):</b>
• <code>/give USER_ID СУММА</code> — выдать 🪙
• <code>/take USER_ID СУММА</code> — забрать 🪙
• <b>🛠 Админ-панель</b> → «📢 Рассылка» (рассылка всем в ЛС)

<b>🧰 Полезное:</b>
• <code>!бот ищи</code> — прошлые ники/чаты игрока
• <code>/tagall</code> — тегнуть всех

<i>Подсказка:</i> большинство админ-команд работает только <b>в группе</b> и часто требует <b>ответа</b> на сообщение игрока."""

def send_help_selector(chat_id: int):
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(
        types.InlineKeyboardButton("Для игрока", callback_data="help_player"),
        types.InlineKeyboardButton("Для админа", callback_data="help_admin"),
    )
    bot.send_message(chat_id, "❓ <b>Помощь кому?</b>", parse_mode="HTML", reply_markup=mk)


@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in ['развлечения', '!разв', '!развлечения', 'разв', 'игры', 'игра'])
def развлечения_cmd(message):
    text = (
        "🎉 <b>Развлечения</b>\n"
        "━━━━━━━━━━━━━━\n"
        "🎲 Рулетка — меню: <code>/start</code> (в чате)\n"
        "🎰 Банда (мини-слоты): <code>Банда 1000</code> или <code>!банда 5к</code>\n"
        "🪙 Орёл/Решка (дуэль): ответь на игрока → <code>!монетка 50000</code>\n"
        "🏴‍☠️ Ограбление: ответь на игрока → <code>!ограбить</code>\n"
        "🎁 Бонус: <code>бонус</code> (в ЛС)\n"
        "📊 Стата чата (24ч): <code>!стата</code>\n"
        "🏆 Топ: <code>топ</code>\n"
    )

    # В группах сразу показываем рулетку-меню (без нижних кнопок)
    try:
        if message.chat.type in ['group', 'supergroup']:
            bot.send_message(message.chat.id, text, parse_mode="HTML")
        else:
            bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=main_menu_keyboard(message.from_user.id))
    except Exception:
        bot.send_message(message.chat.id, "🎉 Развлечения: /start (в чате), Банда 1000, !монетка 50000, !ограбить")








# --- 3. СТАРЫЙ ДИЗАЙН ПРОФИЛЯ ---

def show_profile(obj):
    # Определяем, откуда взяты данные (из сообщения или кнопки)
    user = obj.from_user
    chat_id = obj.chat.id if hasattr(obj, 'chat') else obj.message.chat.id

    data = load_data()
    user_data = get_user(data, user.id)

    # Брак в профиле
    pair_key, pair = _get_my_marriage(user.id)
    if pair:
        hp = _hp_units_to_float(int(pair.get("hp_units", 0)))
        dur = _format_duration(int(time.time()) - int(pair.get("since_ts", 0)))
        marriage_block = (
            f"\n\n💍 <b>Брак</b>\n"
            f"👩‍❤️‍👨 {link_user_html(pair.get('u1'), pair.get('u1_name','Игрок'))} + "
            f"{link_user_html(pair.get('u2'), pair.get('u2_name','Игрок'))}\n"
            f"⏱ Вместе: <b>{dur}</b>\n"
            f"⚜️ HP: <b>{hp:.2f}</b>"
        )
    else:
        marriage_block = "\n\n💍 <b>Брак</b>: <b>нет</b>"

    text = (
        f"👤 <b>Профиль</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"👤 Ник: <a href='tg://user?id={user.id}'>{name_with_badge_html(user.id, user.first_name)}</a>\n\n"
        f"💰 Баланс: <b>{user_data.get('balance', 0)}</b> 🪙\n"
        f"🏆 Выиграно: {user_data.get('won', 0)}\n"
        f"💸 Проиграно: {user_data.get('lost', 0)}\n"
        f"📈 Макс. ставка: {user_data.get('max_bet', 0)}\n"
        f"🔥 Макс. выигрыш: {user_data.get('max_win', 0)}"
        f"{marriage_block}\n"
        f"━━━━━━━━━━━━━━"
    )
    bot.send_message(chat_id, text, parse_mode='HTML')



# --- 4. ОБРАБОТКА НИЖНИХ КНОПОК ---

@bot.message_handler(func=lambda message: message.text and message.text.strip().lower() == 'топ')
def show_top(message):
    # Игнорируем команду, если она была написана пока бот был выключен
    try:
        if hasattr(message, 'date') and int(getattr(message, 'date', 0) or 0) < int(BOT_START_UNIX):
            return
    except Exception:
        pass

    """ТОП по балансу:
    - В группах: ТОП только среди тех, кого бот видел в этом чате
      (берём из chat_participants; если он пуст — пробуем восстановить из message_stats_24h.json)
    - В ЛС: общий ТОП по всей базе
    """
    try:
        data = load_data()

        # Гарантируем запись пользователя в базе (чтобы топ не был пустым)
        try:
            if message and getattr(message, 'from_user', None):
                get_user(data, message.from_user.id, message.from_user.first_name)
                save_data(data)
        except Exception:
            pass


        # Всегда отмечаем автора команды в участниках чата (на случай, если middleware не сработал)
        if message.chat.type in ['group', 'supergroup']:
            try:
                track_user_in_chat(message.chat.id, message.from_user.id, message.from_user.first_name, getattr(message.from_user, 'username', '') or '', getattr(message.chat, 'title', '') or '')
                remember_username(message.from_user)
            except Exception:
                pass

        candidates = []

        if message.chat.type != 'private':
            cid = str(message.chat.id)

            # 1) Основной источник — chat_participants
            seen_users = set((chat_participants.get(cid) or {}).keys())

            # 2) Если пусто — пробуем восстановить из статистики сообщений (если она ведётся)
            if not seen_users:
                try:
                    d = _load_message_stats()
                    if isinstance(d, dict) and cid in d and isinstance(d[cid], dict):
                        seen_users = set(d[cid].keys())
                except Exception:
                    pass

            # 3) Если всё равно пусто — не говорим "пуст", а показываем общий ТОП (чтобы команда работала)
            if not seen_users:
                for uid, info in data.items():
                    if isinstance(info, dict):
                        candidates.append((str(uid), info))
            else:
                for uid, info in data.items():
                    if not isinstance(info, dict):
                        continue
                    if str(uid) in seen_users:
                        candidates.append((str(uid), info))
        else:
            for uid, info in data.items():
                if isinstance(info, dict):
                    candidates.append((str(uid), info))

        if not candidates:
            bot.send_message(message.chat.id, "🏆 Топ пока пуст!")
            return

        top_users_raw = sorted(candidates, key=lambda x: int(x[1].get('balance', 0)), reverse=True)

        lines = []
        rank = 0
        for (uid, info) in top_users_raw:
            balance = int(info.get('balance', 0))
            if balance <= 0:
                continue
            rank += 1
            if rank > 20:
                break
            name = info.get('first_name') or info.get('username') or f"Юзер {uid}"
            lines.append(f"{rank}. {safe_html(name)} — <b>{format_balance(balance)}</b> 💰")

        if not lines:
            bot.send_message(message.chat.id, "🏆 Топ пока пуст!")
            return

        header = "🏆 <b>ТОП 20</b>"
        if message.chat.type != 'private':
            header += " (этот чат)"

        response_text = f"{header}\n━━━━━━━━━━━━━━\n" + "\n".join(lines)
        bot.send_message(message.chat.id, response_text, parse_mode="HTML")

    except Exception as e:
        print(f"Ошибка в топ: {e}")
        bot.reply_to(message, "❌ Не удалось вывести топ.")


@bot.message_handler(func=lambda message: message.text and message.text.lower() == 'удвоить')
def double_bet_text(message):
    # Удваивает ВСЕ активные ставки пользователя в этом чате
    if message.chat.type == 'private':
        return
    double_bets_all(message)


@bot.message_handler(func=lambda message: message.text.lower() == 'б')

def balance_with_bonus(message):
    data = load_data()
    user_id = message.from_user.id
    user = get_user(data, user_id)

    name = message.from_user.first_name

    # 1. Считаем сумму всех активных ставок пользователя
    on_bet = sum(int(bet['amount']) for bet in _get_chat_bets(message.chat.id) if str(bet['user_id']) == str(user_id))

    # 2. Форматируем основной баланс
    balance = f"{user['balance']:,}".replace(",", " ")

    # 3. Формируем строку баланса (если есть ставка, добавляем +сумма)
    if on_bet > 0:
        on_bet_str = f"{on_bet:,}".replace(",", " ")
        balance_display = f"{balance} <b>+{on_bet_str}</b>"
    else:
        balance_display = balance

    # Твой оригинальный дизайн
    text = (
        f" {name}\n"
        f"✅💵 = {balance_display}"
    )

    # Если баланс ноль, добавляем текст про бонус
    if user['balance'] == 0:
        text += (
            f"\n\n"
            f"Баланс ноль?\n"
            f"Пропиши /bonus или !бонус"
        )
    # Внутри функции баланса добавь это перед save_data(data):
    user['first_name'] = message.from_user.first_name
    
    # Используем parse_mode="HTML", чтобы жирный шрифт (+) работал
    bot.send_message(message.chat.id, text, parse_mode="HTML")


@bot.message_handler(func=lambda message: message.text and message.text.lower() == 'го')
def start_spin(message):
    # НЕ блокируем бота sleep'ом: используем Timer
    chat_id = str(message.chat.id)

    if message.chat.type == 'private':
        return

    # удаляем старое меню рулетки (если есть)
    _delete_roulette_menu(message.chat.id)

    lock = _lock_for_chat(chat_id)
    with lock:
        if chat_id in spinning_by_chat:
            bot.reply_to(message, "⚠️ <b>Рулетка уже крутится в этом чате!</b>\nПодождите завершения.", parse_mode="HTML")
            return

        current_chat_bets = list(_get_chat_bets(chat_id))
        if not current_chat_bets:
            bot.reply_to(message, "❌ <b>Ошибка: ставок нету!</b>\nСначала сделайте ставку.", parse_mode="HTML")
            return

        spinning_by_chat.add(chat_id)

    # Сообщение о том, что игрок крутит рулетку
    spin_message = bot.send_message(
        message.chat.id,
        f"<a href='tg://user?id={message.from_user.id}'>{name_with_badge_html(message.from_user.id, message.from_user.first_name)}</a> 🎲 крутит рулетку...",
        parse_mode="HTML"
    )
    gif_message = bot.send_animation(message.chat.id, "https://i.gifer.com/77rN.gif")

    # Задержка зависит от кол-ва ставок (без блокировки потока)
    num_bets = len(current_chat_bets)
    delay = random.choice([10, 12, 15]) if num_bets > 2 else random.choice([5, 7, 10])

    def _finish():
        # чистим временные сообщения
        try:
            bot.delete_message(message.chat.id, spin_message.message_id)
            bot.delete_message(message.chat.id, gif_message.message_id)
        except Exception:
            pass

        # запускаем результат
        spin_roulette(message)

    threading.Timer(delay, _finish).start()

def spin_from_menu(call):
    """Запуск рулетки из меню (инлайн-кнопка 'Крутить'):
    - с задержкой (как 'го')
    - показывает кто крутит
    - удаляет старое меню, чтобы не засорять чат
    """
    try:
        chat_id = str(call.message.chat.id)
    except Exception:
        return

    # удаляем меню, с которого нажали (и старое меню если оно запомнено)
    try:
        _delete_roulette_menu(int(chat_id))
    except Exception:
        pass

    if call.message.chat.type == 'private':
        return

    lock = _lock_for_chat(chat_id)
    with lock:
        if chat_id in spinning_by_chat:
            try:
                bot.answer_callback_query(call.id, "⏳ Рулетка уже крутится…", show_alert=False)
            except Exception:
                pass
            return

        current_chat_bets = list(_get_chat_bets(chat_id))
        if not current_chat_bets:
            try:
                bot.answer_callback_query(call.id, "❌ Ставок нет!", show_alert=False)
            except Exception:
                pass
            return

        spinning_by_chat.add(chat_id)


    # показываем, кто крутит (как просили)
    try:
        name = getattr(call.from_user, 'first_name', 'Игрок')
        user_link = f"<a href='tg://user?id={call.from_user.id}'>{safe_html(name)}</a>"
        bot.send_message(call.message.chat.id, f"{user_link} 🎲 крутит рулетку...", parse_mode="HTML")
    except Exception:
        pass

    # Только гифка (без текста)
    try:
        gif_message = bot.send_animation(call.message.chat.id, "https://i.gifer.com/77rN.gif")
    except Exception:
        gif_message = None

    num_bets = len(current_chat_bets)
    delay = random.choice([10, 12, 15]) if num_bets > 2 else random.choice([5, 7, 10])

    def _finish():
        try:
            if gif_message:
                bot.delete_message(call.message.chat.id, gif_message.message_id)
        except Exception:
            pass
        spin_roulette(call)

    threading.Timer(delay, _finish).start()


def get_user(data, user_id, first_name="Игрок"):
    user_id = str(user_id)
    if user_id not in data:
        data[user_id] = {
            'balance': 100000,
            'last_bonus': 0,
            'first_name': first_name,
            'won': 0, 'lost': 0, 'max_bet': 0, 'max_win': 0,
            'bank': 0,
            'bank_last_action': 0
        }
    else:
        # НЕ перетираем имя на "Игрок"
        if first_name and first_name != "Игрок":
            data[user_id]['first_name'] = first_name
        data[user_id].setdefault('bank', 0)
    data[user_id].setdefault('bank_last_action', 0)
    return data[user_id]


@bot.message_handler(func=lambda message: message.text.lower() in ['лог', '!лог'])
def show_log_unified(message):
    global results_log

    # Получаем ID текущего чата (группы или ЛС)
    chat_id = str(message.chat.id)

    # Проверяем, есть ли лог именно для этого чата в словаре
    # Если results_log это еще список [], метод .get не сработает,
    # поэтому убедись, что в начале кода results_log = {}
    if isinstance(results_log, dict):
        current_log = results_log.get(chat_id, [])
    else:
        current_log = []

    if not current_log:
        bot.reply_to(message, "История игр в этом чате пока пуста!")
        return

    # Логика количества
    cmd = message.text.lower()
    if cmd == '!лог':
        count = 20
    else:
        count = 10

    # Берем последние результаты конкретно этого чата
    log_slice = current_log[-count:]
    log_text = "\n".join(log_slice)

    # Вывод в твоем стиле
    bot.send_message(message.chat.id, f"История игр:\n{log_text}")


@bot.message_handler(func=lambda m: m.text and (m.text.startswith('+') or m.text.split()[0] in ['/pay', '/transfer']))
def transfer_money(message):
    data = load_data()
    sender_id = str(message.from_user.id)

    if not message.reply_to_message:
        bot.reply_to(message, "❌ <b>Нужно ответить на сообщение игрока!</b>", parse_mode="HTML")
        return

    try:
        # Извлекаем сумму
        text = message.text.replace('+', '').replace('/pay', '').replace('/transfer', '').strip()
        amount = int(text.split()[0])
        if amount <= 0: return

        # Проверка привилегий (Админ или Белый список)
        whitelist = data.get('whitelist', [])
        is_privileged = (int(sender_id) in ADMIN_IDS) or (sender_id in whitelist)

        sender = get_user(data, sender_id, message.from_user.first_name)

        if not is_privileged:
            from datetime import datetime
            today = datetime.now().strftime('%Y-%m-%d')

            # Сброс лимита если новый день
            if sender.get('last_transfer_date') != today:
                sender['last_transfer_date'] = today
                sender['daily_transfer_sum'] = 0

            if sender.get('daily_transfer_sum', 0) + amount > 1000000:
                left = 1000000 - sender.get('daily_transfer_sum', 0)
                bot.reply_to(message,
                             f"❌ <b>Лимит!</b> Осталось сегодня: <b>{format_balance(max(0, left))}</b> 🪙",
                             parse_mode="HTML")
                return

        receiver_id = str(message.reply_to_message.from_user.id)

        # Запрет перевода боту (часто люди ошибаются и отвечают боту)
        try:
            if getattr(message.reply_to_message.from_user, 'is_bot', False) or (BOT_ID is not None and int(receiver_id) == int(BOT_ID)):
                bot.reply_to(message, "⚠️ Эй, не туда переводишь! Переводы боту запрещены 🙂\nОтветь на сообщение игрока, которому хочешь перевести.", parse_mode="HTML")
                return
        except Exception:
            pass

        if sender_id == receiver_id:
            bot.reply_to(message, "🤔 Нельзя передать самому себе.")
            return

        if sender['balance'] < amount:
            bot.reply_to(message, "❌ Недостаточно средств.")
            return

        # Перевод
        receiver = get_user(data, receiver_id, message.reply_to_message.from_user.first_name)
        sender['balance'] -= amount
        receiver['balance'] += amount

        if not is_privileged:
            sender['daily_transfer_sum'] = sender.get('daily_transfer_sum', 0) + amount

        save_data(data)

        # История переводов
        try:
            receiver_name = message.reply_to_message.from_user.first_name
            sender_name = message.from_user.first_name
            save_history(sender_id, f"перевод игроку {receiver_name}", f"-{amount}")
            save_history(receiver_id, f"перевод от игрока {sender_name}", f"+{amount}")
        except Exception:
            pass

        sender_link = f"<a href='tg://user?id={sender_id}'>{name_with_badge_html(message.from_user.id, message.from_user.first_name)}</a>"
        receiver_link = f"<a href='tg://user?id={receiver_id}'>{message.reply_to_message.from_user.first_name}</a>"

        design_text = (
            f"💸 {sender_link} ➔ {receiver_link}\n"
            f"💰 <b>{format_balance(amount)}</b> 🪙"
        )

        bot.send_message(message.chat.id, design_text, parse_mode="HTML")

    except Exception as e:
        print(f"Ошибка в pay: {e}")


@bot.message_handler(regexp=r'^(\d+)\s+(\d+|к|ч|з|\d+-\d+)$')
def text_bet_handler(message):
    # Игнорируем старые апдейты (когда бот был оффлайн)
    try:
        if hasattr(message, 'date') and int(getattr(message, 'date', 0) or 0) < int(BOT_START_UNIX):
            return
    except Exception:
        pass
    parts = message.text.lower().split()
    if len(parts) != 2:
        return
    amount, value = parts
    amount = int(amount)
    if amount <= 0:
        return
    call = {'from_user': message.from_user, 'message': message, 'data': f"{amount}_{value}"}
    place_bet(call)

@bot.callback_query_handler(func=lambda call: not (call.data and call.data.startswith('gw_')))
def handle_callback_query(call):
    # Защита от системных кнопок розыгрыша
    try:
        if call.data and str(call.data).startswith('gw_'):
            return
    except Exception:
        pass
    # Игнорируем старые callback-и (когда бот был оффлайн)
    if not _cb_is_fresh(call):
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        return

# БОНУС
    if call.data == 'check_bonus':
        cb_check_bonus(call)
        return
    if call.data == 'repeat':
        repeat_bet(call)
    elif call.data == 'double':
        double_bet(call)
    elif call.data == 'spin':
        spin_from_menu(call)
    elif call.data == 'profile':
        show_profile(call)
    elif call.data == 'links':
        show_links(call)
    elif call.data == 'admin' and call.from_user.id in ADMIN_IDS:
        show_admin_panel(call)
    elif call.data == 'admin_stats' and call.from_user.id in ADMIN_IDS:
        data = load_data()

        # В базе могут быть служебные ключи (например: whitelist), поэтому считаем только пользователей.
        users = []
        for k, v in (data or {}).items():
            try:
                if str(k).isdigit() and isinstance(v, dict):
                    users.append(v)
            except Exception:
                continue

        total_users = len(users)
        total_balance = 0
        for u in users:
            try:
                b = u.get('balance', 0)
                # баланс может быть числом или строкой вида "1 000 000"
                if isinstance(b, str):
                    b2 = b.replace(' ', '').replace(',', '').strip()
                    b = int(b2) if b2.lstrip('-').isdigit() else 0
                total_balance += int(b)
            except Exception:
                pass

        bot.send_message(
            call.from_user.id,
            "📊 <b>Статистика проекта</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"👥 Пользователей: <b>{total_users}</b>\n"
            f"💰 Общий баланс: <b>{format_balance(total_balance)}</b> 🪙\n"
            "━━━━━━━━━━━━━━\n"
            "🗂 Чат-статистика (24ч): <code>!стата</code>\n"
            "📢 Рассылка: через <b>Админ-панель</b> → «Рассылка»",
            parse_mode="HTML"
        )
    elif call.data == 'admin_give' and call.from_user.id in ADMIN_IDS:
        bot.send_message(call.from_user.id, "Напишите в формате:\n<code>/give USER_ID СУММА</code>", parse_mode='HTML')
    elif call.data == 'admin_take' and call.from_user.id in ADMIN_IDS: # Добавил кнопку в логику
        bot.send_message(call.from_user.id, "Напишите в формате:\n<code>/take USER_ID СУММА</code>", parse_mode='HTML')

    elif call.data == 'admin_broadcast' and call.from_user.id in ADMIN_IDS:
        _BROADCAST_STATE[str(call.from_user.id)] = {'mode': None}
        bot.send_message(call.from_user.id, "📢 <b>Рассылка</b>\nВыбери тип рассылки:", parse_mode="HTML", reply_markup=_broadcast_type_markup())
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        return

    elif call.data in ['bc_type_text', 'bc_type_photo', 'bc_type_fwd'] and call.from_user.id in ADMIN_IDS:
        mode = 'text' if call.data == 'bc_type_text' else ('photo' if call.data == 'bc_type_photo' else 'forward')
        _BROADCAST_STATE[str(call.from_user.id)] = {'mode': mode}
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        if mode == 'text':
            bot.send_message(call.from_user.id, "✍️ Отправь <b>текст</b> для рассылки одним сообщением.", parse_mode="HTML")
        elif mode == 'photo':
            bot.send_message(call.from_user.id, "🖼 Отправь <b>фото</b> (можно с подписью) для рассылки.", parse_mode="HTML")
        else:
            bot.send_message(call.from_user.id, "↪️ Перешли или отправь <b>любое сообщение</b>, которое нужно разослать.", parse_mode="HTML")
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        return

    elif call.data == 'bc_cancel' and call.from_user.id in ADMIN_IDS:
        _BROADCAST_STATE.pop(str(call.from_user.id), None)
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        bot.send_message(call.from_user.id, "❌ Рассылка отменена.")
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        return


    elif call.data == 'help_player':
        # удаляем сообщение выбора (чтобы не мусорить) и показываем разделы
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        bot.send_message(
            call.message.chat.id,
            "❓ <b>Помощь игроку:</b> выбери раздел 👇",
            parse_mode="HTML",
            reply_markup=help_player_sections_markup()
        )
        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        return

    elif isinstance(call.data, str) and call.data.startswith('help_p:'):
        sec = call.data.split(':', 1)[1].strip()

        # ❌ Закрыть
        if sec == 'close':
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                try:
                    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
                except Exception:
                    pass
            try:
                bot.answer_callback_query(call.id)
            except Exception:
                pass
            return

        # ⬅️ Назад к разделам
        if sec == 'back':
            try:
                bot.edit_message_text(
                    "❓ <b>Помощь игроку:</b> выбери раздел 👇",
                    call.message.chat.id,
                    call.message.message_id,
                    parse_mode="HTML",
                    reply_markup=help_player_sections_markup()
                )
            except Exception:
                try:
                    bot.send_message(
                        call.message.chat.id,
                        "❓ <b>Помощь игроку:</b> выбери раздел 👇",
                        parse_mode="HTML",
                        reply_markup=help_player_sections_markup()
                    )
                except Exception:
                    pass
            try:
                bot.answer_callback_query(call.id)
            except Exception:
                pass
            return

        # 📚 Конкретный раздел
        section_text = HELP_PLAYER_SECTIONS.get(sec) or "❗ Раздел не найден."
        try:
            bot.edit_message_text(
                section_text,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML",
                reply_markup=help_player_back_markup()
            )
        except Exception:
            # если не получилось редактировать — отправим отдельным сообщением
            try:
                bot.send_message(call.message.chat.id, section_text, parse_mode="HTML", reply_markup=help_player_back_markup())
            except Exception:
                pass

        try:
            bot.answer_callback_query(call.id)
        except Exception:
            pass
        return

    elif call.data == 'help_admin':
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        bot.send_message(call.message.chat.id, HELP_TEXT_ADMIN, parse_mode='HTML')
    elif call.data.startswith('range_'):
        # range_1_3 -> ставим 1к на 1 2 3
        try:
            _, a, b = call.data.split('_', 2)
            a = int(a); b = int(b)
            targets = list(range(min(a, b), max(a, b) + 1))
            _place_multi_bets_from_callback(call, targets=targets, amount_each=1000)
        except Exception:
            bot.answer_callback_query(call.id, "Ошибка диапазона", show_alert=False)
    elif call.data.startswith('bet_'):
        # bet_1000_к
        try:
            _, amount_s, target = call.data.split('_', 2)
            amount = int(amount_s)
            _place_multi_bets_from_callback(call, targets=[target], amount_each=amount)
        except Exception:
            bot.answer_callback_query(call.id, "Ошибка ставки", show_alert=False)
    elif call.data == 'double_all':
        double_bets_all(call)

    # --- 🪙 Орёл/Решка ---
    elif call.data.startswith('coin_acc|'):
        gid = call.data.split('|', 1)[1]
        with _coin_lock:
            g = coin_games.get(gid)
        if not g:
            try:
                bot.answer_callback_query(call.id, "Игра уже неактуальна", show_alert=False)
            except Exception:
                pass
            return

        # Принять может только приглашённый
        if int(call.from_user.id) != int(g.get('invitee_id', 0)):
            try:
                bot.answer_callback_query(call.id, "Это приглашение не для вас", show_alert=False)
            except Exception:
                pass
            return

        # Убираем кнопки приглашения
        try:
            _coin_edit_remove_kb(call.message.chat.id, call.message.message_id)
        except Exception:
            pass

        # Списываем ставки (приглашающий может быть уже с резервом)
        amount = int(g.get('amount', 0))
        inviter_reserved = bool(g.get('inviter_reserved'))
        data = load_data()
        u1 = get_user(data, g['inviter_id'], g.get('inviter_name', 'Игрок'))
        u2 = get_user(data, g['invitee_id'], g.get('invitee_name', 'Игрок'))

        if inviter_reserved:
            # приглашающий уже внес ставку при создании
            if int(u2.get('balance', 0)) < amount:
                bot.send_message(call.message.chat.id, "❌ Игра отменена: у приглашённого не хватает 💰.")
                # вернём резерв приглашающему
                try:
                    u1['balance'] = int(u1.get('balance', 0)) + amount
                    save_data(data)
                except Exception:
                    pass
                with _coin_lock:
                    _coin_end_game(str(call.message.chat.id), gid)
                return
            u2['balance'] = int(u2.get('balance', 0)) - amount
            save_data(data)
        else:
            # старый режим (если резерв не был сделан)
            if int(u1.get('balance', 0)) < amount or int(u2.get('balance', 0)) < amount:
                bot.send_message(call.message.chat.id, "❌ Игра отменена: у одного из игроков не хватает 💰.")
                with _coin_lock:
                    _coin_end_game(str(call.message.chat.id), gid)
                return
            u1['balance'] = int(u1.get('balance', 0)) - amount
            u2['balance'] = int(u2.get('balance', 0)) - amount
            save_data(data)

        try:
            bot.answer_callback_query(call.id, "Принято!", show_alert=False)
        except Exception:
            pass

        _coin_start_choose(call.message.chat.id, gid)

    elif call.data.startswith('coin_dec|'):
        gid = call.data.split('|', 1)[1]
        with _coin_lock:
            g = coin_games.get(gid)
        if not g:
            return

        # Отказаться может только приглашённый
        if int(call.from_user.id) != int(g.get('invitee_id', 0)):
            try:
                bot.answer_callback_query(call.id, "Это приглашение не для вас", show_alert=False)
            except Exception:
                pass
            return

        # Убираем кнопки приглашения
        try:
            _coin_edit_remove_kb(call.message.chat.id, call.message.message_id)
        except Exception:
            pass

        # Возвращаем резерв приглашающему (если был)
        try:
            if g.get('inviter_reserved'):
                amount = int(g.get('amount', 0))
                data = load_data()
                u1 = get_user(data, int(g.get('inviter_id', 0)), g.get('inviter_name', 'Игрок'))
                u1['balance'] = int(u1.get('balance', 0)) + amount
                save_data(data)
        except Exception:
            pass

        # Удаляем сообщения игры (включая вызов)
        try:
            chat_id_int = int(g.get('chat_id'))
            for mid in list(g.get('cleanup_msg_ids') or []):
                try:
                    bot.delete_message(chat_id_int, int(mid))
                except Exception:
                    pass
        except Exception:
            pass

        bot.send_message(call.message.chat.id, "🪙 Игра отменена (отказ).")
        with _coin_lock:
            _coin_end_game(str(call.message.chat.id), gid)

    elif call.data.startswith('coin_pick|'):
        # coin_pick|gid|o
        parts = call.data.split('|')
        if len(parts) < 3:
            return
        gid = parts[1]
        pick = parts[2]
        with _coin_lock:
            g = coin_games.get(gid)
        if not g:
            return

        chooser_id = int(g.get('chooser_id') or 0)
        if int(call.from_user.id) != chooser_id:
            try:
                bot.answer_callback_query(call.id, "Вы не выбираете сторону", show_alert=False)
            except Exception:
                pass
            return

        choice = 'орел' if pick == 'o' else 'решка'
        with _coin_lock:
            if gid not in coin_games or coin_games[gid].get('stage') != 'choose':
                return
            coin_games[gid]['choice'] = choice
            coin_games[gid]['stage'] = 'flip'

        # Убираем кнопки выбора
        try:
            _coin_edit_remove_kb(call.message.chat.id, call.message.message_id)
        except Exception:
            pass

        # Второму автоматически назначаем противоположную сторону (для текста)
        other_id = g['invitee_id'] if chooser_id == int(g['inviter_id']) else g['inviter_id']
        other_choice = 'решка' if choice == 'орел' else 'орел'

        try:
            bot.answer_callback_query(call.id, "Ок!", show_alert=False)
        except Exception:
            pass

        sent2_text = (
            f"🪙 Выбор сделан!\n"
            f"━━━━━━━━━━━━━━\n"
            f"<a href='tg://user?id={chooser_id}'>{safe_html(call.from_user.first_name)}</a> выбрал(а): "
            f"<b>{'🪙 Орёл' if choice=='орел' else '🪙 Решка'}</b>\n"
            f"<a href='tg://user?id={other_id}'>"
            f"{safe_html(g.get('invitee_name') if other_id == g.get('invitee_id') else g.get('inviter_name'))}"
            f"</a> получает: <b>{'🪙 Орёл' if other_choice=='орел' else '🪙 Решка'}</b>\n\n"
            f"Бросаем монету... 🪙"
        )
        sent2 = bot.send_message(
            call.message.chat.id,
            sent2_text,
            parse_mode="HTML"
        )
        try:
            with _coin_lock:
                if gid in coin_games:
                    coin_games[gid].setdefault('cleanup_msg_ids', []).append(int(sent2.message_id))
        except Exception:
            pass

        _coin_finish_flip(call.message.chat.id, gid)
    else:
        # Если это не системная кнопка, значит это ставка
        place_bet(call)

def bonus_check_markup():
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("✅ Проверить подписку", callback_data='check_bonus'),
        types.InlineKeyboardButton("🔗 Подписаться", url='https://t.me/utxa_news')
    )
    return markup

def is_subscribed(user_id):
    try:
        member = bot.get_chat_member(CHANNEL, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return False



# =========================
# 🎁 БОНУС (подписка на канал + cooldown)
# Триггеры: бонус /bonus !бонус
# - В чате: перенаправляет в ЛС (кнопка)
# - В ЛС: проверка подписки + выдача
# =========================
BONUS_FILE = 'bonus_claims.json'
_bonus_lock = threading.Lock()

def _load_bonus_claims() -> dict:
    try:
        if os.path.exists(BONUS_FILE) and os.path.getsize(BONUS_FILE) > 0:
            with open(BONUS_FILE, 'r', encoding='utf-8') as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}

def _save_bonus_claims(d: dict):
    tmp = BONUS_FILE + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=4)
        import shutil as _sh
        _sh.move(tmp, BONUS_FILE)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

# Настройки бонуса (можешь менять)
BONUS_AMOUNT = 10000
BONUS_COOLDOWN_SECONDS = 24 * 60 * 60  # 1 раз в 24 часа

def _bonus_can_claim(user_id: int):
    now = int(time.time())
    uid = str(user_id)
    with _bonus_lock:
        d = _load_bonus_claims()
        last = int(d.get(uid, 0) or 0)
        left = BONUS_COOLDOWN_SECONDS - (now - last)
        if left > 0:
            return False, left
        return True, 0

def _bonus_mark_claim(user_id: int):
    now = int(time.time())
    uid = str(user_id)
    with _bonus_lock:
        d = _load_bonus_claims()
        d[uid] = now
        _save_bonus_claims(d)

def _bonus_redirect_markup() -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup()
    url = _deep_link_url("bonus")
    if url:
        mk.add(types.InlineKeyboardButton("🎁 Получить бонус в ЛС", url=url))
    return mk

# Бонус: запоминаем сообщение с кнопкой в чате, чтобы удалить его после получения бонуса в ЛС
_bonus_prompt_msg = {}  # user_id(int) -> (chat_id(int), message_id(int))

def bonus_cmd(message):
    """Основная логика бонуса (выполнять только в ЛС)."""
    try:
        if message.chat.type != 'private':
            sent = bot.reply_to(message, "🎁 Бонус выдаётся в ЛС. Нажми кнопку 👇", reply_markup=_bonus_redirect_markup())
            try:
                _bonus_prompt_msg[int(message.from_user.id)] = (int(message.chat.id), int(sent.message_id))
            except Exception:
                pass
            return

        user_id = int(message.from_user.id)

        # cooldown
        ok, left = _bonus_can_claim(user_id)
        if not ok:
            hh = left // 3600
            mm = (left % 3600) // 60
            ss = left % 60
            bot.reply_to(message, f"⏳ Бонус уже получен. Попробуй через {hh}ч {mm}м {ss}с.")
            return

        # подписка
        if not is_subscribed(user_id):
            bot.send_message(
                message.chat.id,
                "📢 Чтобы получить бонус, подпишись на канал @utxa_news и нажми «✅ Проверить подписку».",
                reply_markup=bonus_check_markup()
            )
            return

        # выдача
        data = load_data()
        user = get_user(data, user_id, getattr(message.from_user, 'first_name', 'Игрок'))
        user['balance'] = int(user.get('balance', 0)) + int(BONUS_AMOUNT)
        save_history(user_id, "Бонус", f"+{BONUS_AMOUNT}")
        save_data(data)

        _bonus_mark_claim(user_id)

        # удаляем сообщение с кнопкой в чате (если было)
        try:
            info = _bonus_prompt_msg.pop(int(user_id), None)
            if info:
                bot.delete_message(int(info[0]), int(info[1]))
        except Exception:
            pass

        bot.reply_to(
                    message,
                    "🎁 <b>Бонус получен!</b>\n"
                    "━━━━━━━━━━━━━━\n"
                    f"➕ Начислено: <b>{format_balance(BONUS_AMOUNT)}</b> 🪙\n"
                    f"💰 Баланс: <b>{format_balance(int(user.get('balance', 0)))}</b> 🪙",
                    parse_mode="HTML"
                )
    except Exception:
        bot.send_message(message.chat.id, "❌ Ошибка при выдаче бонуса. Попробуй ещё раз позже.")

@bot.callback_query_handler(func=lambda call: call.data == 'check_bonus')
def cb_check_bonus(call):
    try:
        uid = int(call.from_user.id)
        if is_subscribed(uid):
            try:
                bot.answer_callback_query(call.id, "✅ Подписка подтверждена!", show_alert=False)
            except Exception:
                pass
            # выдаём бонус (в ЛС)
            # call.message в ЛС, но на всякий
            bonus_cmd(call.message)
        else:
            bot.answer_callback_query(call.id, "❌ Подписка не найдена. Подпишись и попробуй снова.", show_alert=True)
    except Exception:
        try:
            bot.answer_callback_query(call.id, "❌ Ошибка проверки.", show_alert=True)
        except Exception:
            pass

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in ['бонус', '/bonus', '!бонус'])
def bonus_anywhere(message):
    # В чате -> перенаправляем в ЛС, в ЛС -> выдаём
    if message.chat.type != 'private':
        bot.reply_to(message, "🎁 Бонус выдаётся в ЛС. Нажми кнопку 👇", reply_markup=_bonus_redirect_markup())
        return
    bonus_cmd(message)

def place_bet(call):
    """Принимает ставку. call может быть CallbackQuery или словарём как раньше."""
    # Защита: не принимаем системные callback'и розыгрыша как ставки
    data = load_data()

    # Унификация входа
    if isinstance(call, dict):
        user_obj = call.get('from_user')
        msg = call.get('message')
        raw = call.get('data', '')
    else:
        user_obj = call.from_user
        msg = call.message
        raw = getattr(call, 'data', '')
    try:
        if isinstance(raw, str) and raw.startswith('gw_'):
            return
    except Exception:
        pass

    chat_id = str(msg.chat.id)
    user = get_user(data, user_obj.id, getattr(user_obj, "first_name", "Игрок"))

    # Парсим формат amount_value или просто value
    amount = None
    value = None
    if isinstance(raw, str) and "_" in raw:
        a, v = raw.split("_", 1)
        if a.isdigit():
            amount = int(a)
            value = v.strip()
    if amount is None:
        # fallback: минималка 5
        amount = 5
        value = str(raw).strip()

    # Только int
    try:
        amount = int(amount)
    except Exception:
        bot.send_message(msg.chat.id, "❌ Ставка должна быть целым числом.")
        return
    if amount <= 0:
        return

    # Нормализуем цель
    value = value.lower()
    if value in ['красное', 'крас', 'к']:
        value = 'к'
    elif value in ['черное', 'черн', 'ч']:
        value = 'ч'
    elif value in ['зеленое', 'зел', 'з']:
        value = 'з'

    # ✅ Валидация: только 0..12, к/ч/з, или диапазон 0-12
    value = normalize_bet_target(value)
    if not is_valid_bet_value(value):
        bot.send_message(msg.chat.id, "❌ Можно ставить только на числа 0–12 или на к/ч/з.")
        return

    lock = _lock_for_chat(chat_id)
    with lock:
        if chat_id in spinning_by_chat:
            bot.send_message(msg.chat.id, "⏳ Подождите, рулетка крутится!")
            return

        if user['balance'] < amount:
            bot.send_message(msg.chat.id, "Недостаточно 🪙 для ставки!")
            return

        # списание
        user['balance'] -= amount
        save_data(data)

        track_user_in_chat(chat_id, user_obj.id, getattr(user_obj, "first_name", "Игрок"), getattr(user_obj, "username", "") or "", getattr(msg.chat, 'title', '') or '')

        bet = {
            'user_id': user_obj.id,
            'username': getattr(user_obj, "first_name", "Игрок"),
            'bet_value': value,
            'amount': amount,
            'chat_id': chat_id,
        }
        _get_chat_bets(chat_id).append(bet)

        # счётчик ставок (для рефералки анти-спам)
        user['roulette_bets_count'] = int(user.get('roulette_bets_count', 0)) + 1
        _maybe_finalize_ref_reward(data, int(user_obj.id))
        save_data(data)

    # ВАЖНО: last_user_bets мы НЕ трогаем на этапе ставок.
    # Оно перезаписывается ПОСЛЕ каждого спина (квитанция прошлой игры).

    bet_text = (
        f"<a href='tg://user?id={user_obj.id}'>{name_with_badge_html(user_obj.id, getattr(user_obj,'first_name','Игрок'))}</a> "
        f"<b>{format_balance(amount)}</b> на {value_to_text(value)}"
    )
    bot.send_message(msg.chat.id, bet_text, parse_mode="HTML")


def _place_multi_bets_from_callback(call, targets, amount_each: int = 1000):
    """Ставит несколько ставок из инлайн-кнопки. targets: список (числа 0-12 или 'к','ч','з')."""
    msg = call.message
    chat_id = str(msg.chat.id)
    user_id = str(call.from_user.id)
    user_name = getattr(call.from_user, "first_name", "Игрок")

    data = load_data()
    user = get_user(data, user_id, user_name)

    # Нормализация целей
    norm_targets = []
    for t in targets:
        t = str(t).lower()
        if t in ['красное', 'крас', 'к', 'k']:
            t = 'к'
        elif t in ['черное', 'черн', 'ч', 'ch']:
            t = 'ч'
        elif t in ['зеленое', 'зел', 'з', 'z']:
            t = 'з'
        norm_targets.append(t)

    # ✅ Валидация целей
    clean_targets = []
    for t in norm_targets:
        t = normalize_bet_target(t)
        if not is_valid_bet_value(t):
            try:
                bot.answer_callback_query(call.id, "❌ Только 0–12 или к/ч/з", show_alert=False)
            except Exception:
                pass
            return
        clean_targets.append(t)
    norm_targets = clean_targets
    total_needed = int(amount_each) * len(norm_targets)
    lock = _lock_for_chat(chat_id)
    with lock:
        if chat_id in spinning_by_chat:
            bot.answer_callback_query(call.id, "Рулетка крутится…", show_alert=False)
            return
        if user.get('balance', 0) < total_needed:
            bot.answer_callback_query(call.id, "Недостаточно 🪙!", show_alert=False)
            return

        user['balance'] -= total_needed
        save_data(data)

        for t in norm_targets:
            _get_chat_bets(chat_id).append({
                'user_id': int(user_id),
                'username': user_name,
                'bet_value': t,
                'amount': int(amount_each),
                'chat_id': chat_id
            })


    # счётчик ставок (для рефералки анти-спам)
    try:
        data = load_data()
        u = get_user(data, user_id, user_name)
        u['roulette_bets_count'] = int(u.get('roulette_bets_count', 0)) + len(norm_targets)
        _maybe_finalize_ref_reward(data, int(user_id))
        save_data(data)
    except Exception:
        pass
    # Короткое подтверждение (без спама — ответ на callback)
    try:
        bot.answer_callback_query(call.id, f"✅ Принято: {format_balance(total_needed)} 🪙", show_alert=False)
    except Exception:
        pass


def double_bets_all(obj):
    """Удваивает ВСЕ текущие ставки пользователя в этом чате (и по тексту, и по кнопке)."""
    # obj может быть message/callback/dict
    if isinstance(obj, dict) and 'message' in obj:
        msg = obj['message']
        chat_id = str(msg.chat.id)
        from_user = obj.get('from_user')
    elif hasattr(obj, 'message') and hasattr(obj, 'from_user'):
        msg = obj.message
        chat_id = str(msg.chat.id)
        from_user = obj.from_user
    else:
        msg = obj
        chat_id = str(msg.chat.id)
        from_user = msg.from_user

    user_id = str(from_user.id)
    user_name = getattr(from_user, "first_name", "Игрок")

    data = load_data()
    user = get_user(data, user_id, user_name)

    lock = _lock_for_chat(chat_id)
    with lock:
        if chat_id in spinning_by_chat:
            bot.send_message(msg.chat.id, "⏳ Подождите, рулетка крутится!")
            return

        chat_bets = _get_chat_bets(chat_id)
        my_bets = [b for b in chat_bets if str(b.get('user_id')) == user_id]
        if not my_bets:
            bot.send_message(msg.chat.id, "❌ У вас нет активных ставок в этом чате.")
            return

        add_total = sum(int(b['amount']) for b in my_bets)
        if user.get('balance', 0) < add_total:
            bot.send_message(msg.chat.id, f"❌ Недостаточно монет! Нужно: <b>{format_balance(add_total)}</b> 🪙", parse_mode="HTML")
            return

        user['balance'] -= add_total
        save_data(data)

        # Удваиваем суммы ставок БЕЗ создания новых строк ставок (без флуда)
        for b in my_bets:
            b['amount'] = int(b['amount']) * 2

    # Показ обновленных ставок (каждая стала x2)
    lines = []
    for b in my_bets:
        lines.append(f"• <b>{format_balance(int(b['amount']))}</b> на <b>{safe_html(value_to_text(b['bet_value']))}</b>")
    bot.send_message(msg.chat.id, "➕ <b>Ставки удвоены:</b>\n" + "\n".join(lines), parse_mode="HTML")


def double_bets_all_from_callback(call):
    double_bets_all(call)

@bot.message_handler(func=lambda message: message.text and message.text.lower() == 'пов')
def repeat_bet_fixed(message):
    if message.chat.type == 'private':
        return

    # игнорируем сообщения бота/людей вида 'Ставка: ...'
    try:
        if message.text and message.text.strip().lower().startswith('ставка:'):
            return
    except Exception:
        pass

    chat_id = str(message.chat.id)
    user_id = str(message.from_user.id)
    key = f"{chat_id}:{user_id}"

    # анти-флуд: "пов" можно только 1 раз после игры (сброс после "отмена")
    if repeat_used.get(key, False):
        bot.reply_to(message, "⚠️ Повтор уже использован. Если отменишь ставки — снова можно 1 раз.")
        return

    data = load_data()
    user = get_user(data, user_id, message.from_user.first_name)

    # Берём только квитанцию последней игры в ЭТОМ чате
    receipt = last_user_bets.get(key, [])
    if not receipt:
        bot.reply_to(message, "❌ Нет ставок для повтора (предыдущая игра в этом чате не найдена).")
        return

    total_amount = sum(int(b['amount']) for b in receipt)
    if user['balance'] < total_amount:
        bot.reply_to(message, f"❌ Недостаточно монет! Нужно: {format_balance(total_amount)} 🪙")
        return

    lock = _lock_for_chat(chat_id)
    with lock:
        if chat_id in spinning_by_chat:
            bot.reply_to(message, "⏳ Подождите, рулетка крутится!")
            return

        user['balance'] -= total_amount
        save_data(data)

        lines = []
        for b in receipt:
            _merge_add_bet(chat_id, int(user_id), message.from_user.first_name, int(b['amount']), b['bet_value'])
            lines.append(f"• <b>{format_balance(int(b['amount']))}</b> на <b>{safe_html(value_to_text(b['bet_value']))}</b>")

    repeat_used[key] = True
    bot.reply_to(message, "🔄 <b>Ставки повторены:</b>\n" + "\n".join(lines), parse_mode="HTML")

# Функция для перевода значения ставки в человекочитаемый формат
def value_to_text(value):
    if value == 'к':
        return "красное"
    elif value == 'ч':
        return "черное"
    elif value == 'з':
        return "зеленое"
    elif '-' in value:
        return value  # Диапазоны выводятся как есть
    else:
        return value  # Просто числа или другие значения


def repeat_bet(call):
    """Callback repeat: повтор последней квитанции в текущем чате (ТОЛЬКО для инлайн-кнопки)."""
    msg = call['message'] if isinstance(call, dict) else call.message
    chat_id = str(msg.chat.id)
    user_id_int = int(call['from_user'].id if isinstance(call, dict) else call.from_user.id)
    user_id = str(user_id_int)
    key = f"{chat_id}:{user_id}"

    # анти-флуд: повтор можно только 1 раз, пока ставки не отменят/не прокрутят заново
    if repeat_used.get(key, False):
        try:
            bot.answer_callback_query(call.id, "⚠️ Повтор уже использован.", show_alert=False)
        except Exception:
            pass
        return

    data = load_data()
    user = get_user(data, user_id, (call['from_user'].first_name if isinstance(call, dict) else call.from_user.first_name))

    receipt = last_user_bets.get(key, [])
    if not receipt:
        bot.send_message(msg.chat.id, "❌ Нет предыдущих ставок в этом чате!")
        return

    total_amount = sum(int(b.get('amount', 0)) for b in receipt)
    if int(user.get('balance', 0)) < total_amount:
        bot.send_message(msg.chat.id, "❌ Недостаточно монет!")
        return

    lock = _lock_for_chat(chat_id)
    with lock:
        if chat_id in spinning_by_chat:
            bot.send_message(msg.chat.id, "⏳ Подождите, рулетка крутится!")
            return

        # списываем суммарно
        user['balance'] = int(user.get('balance', 0)) - total_amount
        save_data(data)

        # переносим ставки в текущие ставки чата
        for b in receipt:
            _get_chat_bets(chat_id).append({
                'user_id': int(user_id),
                'username': user.get('first_name', 'Игрок'),
                'bet_value': b.get('bet_value'),
                'amount': int(b.get('amount', 0)),
                'chat_id': chat_id
            })

    repeat_used[key] = True

    # красивое сообщение (как просил)
    name = call['from_user'].first_name if isinstance(call, dict) else call.from_user.first_name
    user_link = f"<a href='tg://user?id={user_id_int}'>{safe_html(name)}</a>"
    bot.send_message(
        msg.chat.id,
        f"🔄 {user_link} повторил ставки. Итого: <b>{format_balance(total_amount)}</b> 🪙",
        parse_mode="HTML"
    )


def double_bet(call):
    """Callback double: удвоить ВСЕ текущие ставки пользователя в этом чате."""
    double_bets_all(call)





TOPDAY_FILE = 'topday_profit.json'

def _load_topday_profit():
    try:
        if os.path.exists(TOPDAY_FILE) and os.path.getsize(TOPDAY_FILE) > 0:
            with open(TOPDAY_FILE, 'r', encoding='utf-8') as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}

def _save_topday_profit(d):
    tmp = TOPDAY_FILE + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=4)
        import shutil as _sh
        _sh.move(tmp, TOPDAY_FILE)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

def _today_key():
    # используем локальную дату сервера; важно лишь, что ключ хранится и не "сбрасывается таймером"
    from datetime import datetime
    return datetime.now().strftime('%Y-%m-%d')

def update_topday_profit(chat_id: str, user_id: str, delta: int):
    try:
        d = _load_topday_profit()
        day = _today_key()
        cid = str(chat_id)
        uid = str(user_id)
        if cid not in d:
            d[cid] = {}
        if day not in d[cid]:
            d[cid][day] = {}
        d[cid][day][uid] = int(d[cid][day].get(uid, 0)) + int(delta)
        _save_topday_profit(d)
    except Exception:
        pass

def get_topday_profit(chat_id: str):
    d = _load_topday_profit()
    day = _today_key()
    cid = str(chat_id)
    return d.get(cid, {}).get(day, {})


def spin_roulette(obj):
    """Крутит рулетку и рассчитывает результаты ТОЛЬКО для текущего чата."""
    # obj может быть message/callback/dict
    if isinstance(obj, dict) and 'message' in obj:
        msg = obj['message']
        chat_id = str(msg.chat.id)
        user_obj = obj.get('from_user')
    elif hasattr(obj, 'message') and hasattr(obj, 'from_user'):
        msg = obj.message
        chat_id = str(msg.chat.id)
        user_obj = obj.from_user
    else:
        msg = obj
        chat_id = str(obj.chat.id)
        user_obj = obj.from_user if hasattr(obj, 'from_user') else None

    lock = _lock_for_chat(chat_id)
    with lock:
        # Генерируем результат
        result = random.randint(0, 12)
        if result in colors['к']:
            color, color_key = '🔴', 'к'
        elif result in colors['ч']:
            color, color_key = '⚫', 'ч'
        else:
            color, color_key = '🟢', 'з'

        # Лог по чатам
        if chat_id not in results_log:
            results_log[chat_id] = []
        res_display = f"{result}{color}"
        results_log[chat_id].append(res_display)
        if len(results_log[chat_id]) > 50:
            results_log[chat_id] = results_log[chat_id][-50:]
        save_log_to_file()

        data = load_data()
        chat_bets = list(_get_chat_bets(chat_id))

        # Перезаписываем "квитанции" ставок (последняя игра) по (chat, user)
        users_in_chat = set(b['user_id'] for b in chat_bets)
        for uid in users_in_chat:
            key = f"{chat_id}:{uid}"
            last_user_bets[key] = [b.copy() for b in chat_bets if b['user_id'] == uid]
            repeat_used[key] = False  # после игры можно "пов" ровно 1 раз

        bet_lines = []
        win_lines = []
        refund_lines = []


        per_user_summary = {}  # user_id -> {win, loss, has_win}
        # Правило зеро:
        # - ставки на 0 -> x12
        # - все остальные ставки -> возврат 50% (rounded down)
        for bet in chat_bets:
            user_id = str(bet['user_id'])
            user = get_user(data, user_id, bet.get('username', 'Игрок'))
            amount = int(bet['amount'])
            bet_val = str(bet['bet_value'])

            # для отображения (без HTML-инъекций)
            user_name_plain = name_with_badge_html(bet.get('user_id'), bet.get('username', 'Игрок'))
            bet_lines.append(f"• {user_name_plain} <b>{format_balance(amount)}</b> на {safe_html(value_to_text(bet_val))}")


            if user_id not in per_user_summary:
                per_user_summary[user_id] = {'win': 0, 'loss': 0, 'has_win': False}
            win = False
            winnings = 0

            if result == 0:
                # ЗЕРО:
                # - ставка на 0 выигрывает x12
                # - все остальные получают возврат 50%, остальное = проигрыш
                if (bet_val == 'з') or (bet_val.isdigit() and int(bet_val) == 0):
                    win = True
                    winnings = amount * 12
                    per_user_summary[user_id]['has_win'] = True
                    per_user_summary[user_id]['win'] += int(winnings)
                    loss_amount = 0
                else:
                    refund = amount // 2
                    if refund > 0:
                        user['balance'] += refund
                        refund_lines.append(f"• {user_name_plain} возврат <b>{format_balance(refund)}</b> (зеро)")
                    loss_amount = amount - refund
                    user['lost'] = user.get('lost', 0) + loss_amount
                    per_user_summary[user_id]['loss'] += int(loss_amount)
            else:
                loss_amount = amount

                if bet_val == color_key:
                    win = True
                    winnings = amount * (12 if bet_val == 'з' else 2)
                    per_user_summary[user_id]['has_win'] = True
                    per_user_summary[user_id]['win'] += int(winnings)
                elif '-' in bet_val:
                    try:
                        a, b = map(int, bet_val.split('-'))
                        rng_min, rng_max = min(a, b), max(a, b)
                        # только валидные диапазоны в рулетке 0..12
                        if rng_min < 0 or rng_max > 12:
                            raise ValueError("range out of bounds")

                        if rng_min <= result <= rng_max:
                            win = True
                            rng_size = (rng_max - rng_min) + 1

                            # ЛОГИКА ДИАПАЗОНОВ:
                            # чем меньше чисел — тем выше кэф, но не выше x5
                            # на 0-12 (13 чисел) кэф < 1, чтобы игра уходила в минус (пример: 1000 -> 920)
                            mult = min(5.0, (12.0 / float(rng_size)) * 0.995)  # лёгкая маржа
                            mult = round(mult, 2)
                            winnings = int(amount * mult)
                            per_user_summary[user_id]['has_win'] = True
                            per_user_summary[user_id]['win'] += int(winnings)
                    except Exception:
                        pass
                elif bet_val.isdigit() and int(bet_val) == result:
                    win = True
                    winnings = amount * 12
                    per_user_summary[user_id]['has_win'] = True
                    per_user_summary[user_id]['win'] += int(winnings)

                if not win:
                    user['lost'] = user.get('lost', 0) + loss_amount
                    per_user_summary[user_id]['loss'] += int(loss_amount)

            # Начисление выигрыша (ВАЖНО: работает и для зеро)
            if win:
                user['balance'] += winnings
                user['won'] = user.get('won', 0) + winnings
                user_link = f'<a href="tg://user?id={user_id}">{user_name_plain}</a>'
                win_lines.append(f"• {user_link} выиграл <b>{format_balance(winnings)}</b> на {safe_html(value_to_text(bet_val))}")
            # (история пишется ниже итогом)
            # --- ТОП ДНЯ: считаем общий ВЫИГРЫШ (сумма всех выплат за день) ---
            try:
                if win and int(winnings) > 0:
                    update_topday_profit(chat_id, user_id, int(winnings))
            except Exception:
                pass

            user['max_bet'] = max(int(user.get('max_bet', 0)), amount)
            user['max_win'] = max(int(user.get('max_win', 0)), winnings)

        # Сохраняем один раз
        
        # ===== Итог истории (1 запись за игру на игрока) =====
        # Правило:
        # - если игрок выиграл ХОТЬ ОДНУ ставку -> пишем +сумму всех выигрышей (проигрыши не считаем)
        # - если НЕ выиграл ни одной ставки -> пишем -сумму всех проигрышей (с учётом возвратов на зеро)
        for _uid, _sum in per_user_summary.items():
            try:
                if _sum.get('has_win') and int(_sum.get('win', 0)) > 0:
                    save_history(_uid, "итог рулетки", f"+{int(_sum.get('win', 0))}")
                else:
                    loss_total = int(_sum.get('loss', 0))
                    if loss_total > 0:
                        save_history(_uid, "итог рулетки", f"-{loss_total}")
            except Exception:
                pass

        save_data(data)

        
        # --- Красивый и "просторный" вывод результата ---
        header = f"🎲 <b>Рулетка:</b> {result}{color}"

        # Ставки (обычным текстом, без ссылок)
        # Ставки (суммируем дубли: 500 на 5 + 500 на 5 -> 1000 на 5)
        bet_block = []
        agg_bets = {}  # (user_id, name, bet_value) -> amount
        for bet in chat_bets:
            try:
                uid = int(bet.get('user_id', 0))
                nm = str(bet.get('username', 'Игрок'))
                val = str(bet.get('bet_value'))
                key = (uid, nm, val)
                agg_bets[key] = int(agg_bets.get(key, 0)) + int(bet.get('amount', 0))
            except Exception:
                continue

        for (uid, nm, val), amt_sum in agg_bets.items():
            nm_html = safe_html(nm or 'Игрок')
            tgt = safe_html(value_to_text(val))
            bet_block.append(f"{nm_html} <b>{format_balance(int(amt_sum))}</b> на {tgt}")

        # Победители (кликабельные имена)
        winners_block = []
        if win_lines:
            winners_block.append("━━━━━━━━━━━━━━")
            winners_block.append("<b>Победители:</b>")
            winners_block.extend(win_lines)

        # Возвраты (если выпал 0)
        refunds_block = []
        if refund_lines:
            refunds_block.append("━━━━━━━━━━━━━━")
            refunds_block.append("<b>Возвраты 50% (зеро):</b>")
            refunds_block.extend(refund_lines)

        text_parts = [header]
        if bet_block:
            text_parts.append("━━━━━━━━━━━━━━")
            text_parts.extend(bet_block)
        if winners_block:
            text_parts.extend(winners_block)
        if refunds_block:
            text_parts.extend(refunds_block)

        bot.send_message(int(chat_id), "\n".join(text_parts), parse_mode="HTML", disable_web_page_preview=True)

        # Сразу отправляем меню с кнопками (для новичков)
        try:
            send_roulette_menu(int(chat_id))
        except Exception:
            pass

        # Чистим ставки чата и снимаем spinning
        bets_by_chat[chat_id] = []
        spinning_by_chat.discard(chat_id)

def show_profile(obj):
    # Определяем, откуда взяты данные (из сообщения или кнопки)
    user = obj.from_user
    chat_id = obj.chat.id if hasattr(obj, 'chat') else obj.message.chat.id

    data = load_data()
    user_data = get_user(data, user.id)

    # Форматируем текст профиля как раньше
    text = (
        f"👤 <b>Профиль</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"👤 Ник: <a href='tg://user?id={user.id}'>{safe_html(user.first_name)}</a>\n\n"
        f"💰 Баланс: <b>{user_data.get('balance', 0)}</b> 🪙\n"
        f"🏆 Выиграно: {user_data.get('won', 0)}\n"
        f"💸 Проиграно: {user_data.get('lost', 0)}\n"
        f"📈 Макс. ставка: {user_data.get('max_bet', 0)}\n"
        f"🔥 Макс. выигрыш: {user_data.get('max_win', 0)}\n"
        f"━━━━━━━━━━━━━━"
    )
    bot.send_message(chat_id, text, parse_mode='HTML')


def show_links(call):
    try:
        send_links_pretty(call.from_user.id)
    except Exception:
        try:
            send_links_pretty(call.message.chat.id)
        except Exception:
            pass


@bot.message_handler(func=lambda message: message.text in ["Профиль", "🔗 Ссылки", "🏦 Банк", "🛒 Магазин", "❓ Помощь", "👥 Рефералы", "🛠 Админ-панель"])
def handle_menu_navigation(message):
    if message.text == "Профиль":
        if not _bot_cd_ok(message):
            return
        show_profile(message)

    elif message.text == "🔗 Ссылки":
        if not _bot_cd_ok(message):
            return
        send_links_pretty(message.chat.id)

    elif message.text == "🏦 Банк":
        if not _bot_cd_ok(message):
            return
        show_bank(message)

    elif message.text == "🛒 Магазин":
        if not _bot_cd_ok(message):
            return
        try:
            show_shop(message)
        except Exception:
            try:
                send_shop(message)
            except Exception:
                bot.send_message(message.chat.id, "🛒 Магазин временно недоступен.")

    elif message.text == "❓ Помощь":
        if not _bot_cd_ok(message):
            return
        send_help_selector(message.chat.id)

    elif message.text == "👥 Рефералы":
        if not _bot_cd_ok(message):
            return
        send_referrals_selector(message)

    elif message.text == "🛠 Админ-панель":
        if not _bot_cd_ok(message):
            return
        if message.from_user.id in ADMIN_IDS:
            # Создаем ИНЛАЙН кнопки для админки
            markup = types.InlineKeyboardMarkup()
            markup.add(
                types.InlineKeyboardButton("💰 Выдать монеты", callback_data="admin_give"),
                types.InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")
            )
            markup.add(
                types.InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")
            )
            bot.send_message(message.chat.id, "🛠 <b>Панель управления:</b>", reply_markup=markup, parse_mode="HTML")
            # nested @bot.callback_query_handler был удалён (опасно регистрировать хендлеры внутри функций)

@bot.message_handler(commands=['give'])
def give_money(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        _, uid, amount = message.text.split()
        uid = str(uid)
        amount = int(amount)
        data = load_data()
        user = get_user(data, uid)
        user['balance'] += amount
        save_data(data)
        bot.send_message(message.chat.id, f"Пользователю {uid} выдано {amount} монет.")
    except:
        bot.send_message(message.chat.id, "Неверный формат. Используй /give USER_ID СУММА")


import random
from datetime import datetime
# ========== ДОБАВЛЕННЫЕ ФУНКЦИИ ==========

# Показать ID пользователя
@bot.message_handler(commands=['id'])
def show_id(message):
    if message.chat.type != 'private':
        bot.reply_to(message, f"Ваш ID: <b>{message.from_user.id}</b>\nНик: {message.from_user.first_name}", parse_mode='HTML')

# Команда "Профиль"
# 1. Создаем ОДНУ универсальную функцию для вывода данных


# История пользователя
@bot.message_handler(func=lambda message: message.text.lower() == 'история')
def show_history(message):
    if message.chat.type == 'private': return
    user_id = str(message.from_user.id)
    history = get_user_history(user_id)
    if not history:
        bot.reply_to(message, "История пуста!")
        return
    # history уже хранится строками вида: [HH:MM:SS] событие: +/-сумма
    text = "\n".join(history[-10:])
    bot.send_message(message.chat.id, text)

# Обработка зеро — возврат 50%
def process_zero_bet(users_bets):
    results = []
    for user_id, bet in users_bets.items():
        refund = bet['amount'] // 2
        update_balance(user_id, refund)
        save_history(user_id, 'Возврат с зеро', f"+{refund}")
        results.append(f"<a href='tg://user?id={user_id}'>{bet['name']}</a> — возврат {refund}")
    return results

# Отмена ставки
@bot.message_handler(func=lambda message: message.text and message.text.lower() == 'отмена')
def cancel_bet(message):
    if message.chat.type == 'private':
        return

    # игнорируем сообщения бота/людей вида 'Ставка: ...'
    try:
        if message.text and message.text.strip().lower().startswith('ставка:'):
            return
    except Exception:
        pass

    chat_id = str(message.chat.id)
    user_id = message.from_user.id

    lock = _lock_for_chat(chat_id)
    with lock:
        if chat_id in spinning_by_chat:
            bot.reply_to(message, "⏳ Нельзя отменить, рулетка уже крутится!")
            return

        chat_bets = _get_chat_bets(chat_id)
        user_bets = [b for b in chat_bets if b['user_id'] == user_id]

        if not user_bets:
            bot.reply_to(message, "У вас нет активных ставок!")
            return

        total_return = sum(int(b['amount']) for b in user_bets)

        data = load_data()
        user = get_user(data, str(user_id), message.from_user.first_name)
        user['balance'] += total_return
        save_data(data)

        bets_by_chat[chat_id] = [b for b in chat_bets if b['user_id'] != user_id]
        # сбрасываем лимит на 'пов' после отмены
        try:
            repeat_used[f"{chat_id}:{user_id}"] = False
        except Exception:
            pass

    bot.reply_to(message, "Ставки отменены ✅", parse_mode="HTML")

# Рекорды дня
@bot.message_handler(commands=['рекорды'])
def show_day_records(message):
    if message.chat.type == 'private': return
    records = get_daily_records(str(message.chat.id))
    if not records:
        bot.reply_to(message, "Сегодня пока нет рекордов!")
        return
    text = "<b>Рекорды рулетки дня:</b>\n"
    for rec in records:
        text += f"{rec['name']} — выигрыш {rec['amount']}\n"
    bot.send_message(message.chat.id, text, parse_mode='HTML')


# Блокировка крутки без ставок
@bot.message_handler(func=lambda message: False)
def check_spin(message):
    if message.chat.type == 'private': return
    if not has_bets(str(message.chat.id)):
        bot.reply_to(message, "Нет ставок — рулетка не запускается!")
        return
    start_spin(message)

def get_user_data(user_id):
    data = load_data()
    user = get_user(data, user_id)
    return {
        'balance': user.get('balance', 0),
        'won': user.get('won', 0),
        'lost': user.get('lost', 0),
        'max_bet': user.get('max_bet', 0),
        'max_win': user.get('max_win', 0)
    }

def save_history(user_id, event, change):
    """Единая история (ПЕРСИСТЕНТНО в users_history.json).
    Формат строки: [HH:MM:SS] событие: +/-сумма
    """
    try:
        add_event(str(user_id), f"{event}: {change}")
    except Exception as e:
        print(f"Ошибка записи истории: {e}")

def get_user_history(user_id):
    try:
        h = load_history()
        return h.get(str(user_id), [])
    except Exception:
        return []
def remove_bet(user_id, chat_id):
    global bets
    before = len(bets)
    bets = [b for b in bets if not (str(b['user_id']) == str(user_id) and str(b.get('chat_id', chat_id)) == str(chat_id))]
    return len(bets) < before

@bot.message_handler(func=lambda message: message.text and message.text.lower() == 'ставки')
def show_user_bets(message):
    chat_id = str(message.chat.id)
    user_id = message.from_user.id
    user_bets = [b for b in _get_chat_bets(chat_id) if b['user_id'] == user_id]
    if not user_bets:
        bot.reply_to(message, "У вас нет активных ставок в этом чате.")
        return

    lines = []
    total = 0
    for bet in user_bets:
        total += int(bet['amount'])
        lines.append(f"• <b>{format_balance(int(bet['amount']))}</b> на <b>{safe_html(value_to_text(bet['bet_value']))}</b>")

    bot.send_message(message.chat.id, "🎟 <b>Ваши ставки:</b>\n" + "\n".join(lines) + f"\n\nИтого: <b>{format_balance(total)}</b> 🪙", parse_mode="HTML")

@bot.message_handler(commands=['take'])
def admin_take_money(message):
    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        # Формат: /take 12345678 5000
        args = message.text.split()
        if len(args) < 3:
            bot.reply_to(message, "❌ Формат: /take USER_ID СУММА")
            return

        target_id = str(args[1])
        amount = int(args[2])

        data = load_data()
        if target_id not in data:
            bot.reply_to(message, "❌ Пользователь не найден в базе.")
            return

        if data[target_id]['balance'] < amount:
            data[target_id]['balance'] = 0  # Забираем всё, если баланс меньше суммы
        else:
            data[target_id]['balance'] -= amount

        save_data(data)
        save_history(target_id, "Изъято админом", f"-{amount}")

        bot.reply_to(message, f"✅ У пользователя {target_id} успешно отобрано {amount} 🪙.")
        bot.send_message(target_id, f"⚠️ Администратор изъял у вас {amount} 🪙.")
    except Exception as e:
        bot.reply_to(message, f"Ошибка: {e}")


@bot.message_handler(func=lambda message: message.text and re.match(r'^(\d+(?:\.\d+)?)(%|[kкmм]+)?\s+(.+)$', message.text.lower()))
def text_bet_handler(message):
    """Текстовые ставки. Без float-ошибок: используем Decimal -> int."""
    if message.chat.type == 'private':
        return

    # игнорируем сообщения бота/людей вида 'Ставка: ...'
    try:
        if message.text and message.text.strip().lower().startswith('ставка:'):
            return
    except Exception:
        pass

    chat_id = str(message.chat.id)
    lock = _lock_for_chat(chat_id)
    with lock:
        if chat_id in spinning_by_chat:
            bot.reply_to(message, "⏳ Подождите, рулетка крутится!")
            return

    import decimal
    decimal.getcontext().prec = 28

    match = re.match(r'^(\d+(?:\.\d+)?)(%|[kкmм]+)?\s+(.+)$', message.text.lower())
    if not match:
        return

    amount_raw = match.group(1)
    modifier = match.group(2)
    target_raw = match.group(3).strip()

    # Фильтр "обычное общение"

    # Строгая проверка цели, чтобы "100000 чс" / "100000 абв" НЕ принималось.
    allowed_exact = {'к', 'k', 'ч', 'ch', 'з', 'z', '0', 'зеро',
                     'крас', 'красное', 'черн', 'черное', 'зел', 'зеленое'}
    first_word = target_raw.split()[0] if target_raw.split() else ""
    is_digit_target = first_word.isdigit() and 0 <= int(first_word) <= 12
    is_color_target = first_word in allowed_exact
    is_range_target = bool(re.fullmatch(r"\d{1,2}\s*[-–]\s*\d{1,2}", first_word))
    if is_range_target:
        try:
            a, b = [int(x.strip()) for x in re.split(r"[-–]", first_word)]
            is_range_target = (0 <= a <= 12 and 0 <= b <= 12)
        except Exception:
            is_range_target = False
    if not (is_digit_target or is_color_target or is_range_target):
        return

    data = load_data()
    user = get_user(data, message.from_user.id, message.from_user.first_name)
    current_balance = int(user.get('balance', 0))

    # Считаем сумму ставки (int)
    try:
        amount_val = decimal.Decimal(amount_raw)
    except Exception:
        return

    if modifier == '%':
        # процент от текущего баланса
        if amount_val > 100:
            amount_val = decimal.Decimal(100)
        amount = int((decimal.Decimal(current_balance) * amount_val / decimal.Decimal(100)).to_integral_value(rounding=decimal.ROUND_FLOOR))
    elif modifier:
        m = modifier.lower()
        base = int(amount_val.to_integral_value(rounding=decimal.ROUND_FLOOR))
        if m in ['к', 'k']:
            amount = base * 1000
        elif m in ['кк', 'kk', 'м', 'm']:
            amount = base * 1000000
        else:
            amount = base
    else:
        amount = int(amount_val.to_integral_value(rounding=decimal.ROUND_FLOOR))

    if amount <= 0:
        return

    # Разбираем цели и ограничиваем количество
    targets = target_raw.split()
    valid_targets = []
    for t in targets:
        if len(valid_targets) >= 10:  # анти-случайный "поставил на 100 целей"
            break

        t = t.strip()
        if not t:
            continue

        ok = False

        # digit 0-12
        if t.isdigit() and 0 <= int(t) <= 12:
            ok = True

        # colors
        elif t in allowed_exact:
            ok = True

        # range a-b (только цифры)
        elif re.fullmatch(r"\d{1,2}\s*[-–]\s*\d{1,2}", t):
            try:
                a, b = [int(x.strip()) for x in re.split(r"[-–]", t)]
                if 0 <= a <= 12 and 0 <= b <= 12:
                    ok = True
            except Exception:
                ok = False

        # если в сообщении есть лишние слова (например: "на добавил") — полностью игнорируем
        if not ok:
            return

        valid_targets.append(t)

    if not valid_targets:
        return

    total_needed = amount * len(valid_targets)
    if current_balance < total_needed:
        bot.reply_to(message, f"❌ Недостаточно баланса! Нужно: {format_balance(total_needed)} 🪙")
        return

    # Списание и запись ставок
    lock = _lock_for_chat(chat_id)
    with lock:
        user['balance'] -= total_needed
        save_data(data)

        # Суммируем цели в рамках этой команды (если игрок написал "5 5" — станет 1000 на 5)
        agg = {}  # bet_value -> сумма
        for t in valid_targets:
            val = t
            if t in ['красное', 'крас', 'к', 'k']:
                val = 'к'
            elif t in ['черное', 'черн', 'ч', 'ch']:
                val = 'ч'
            elif t in ['зеленое', 'зел', 'з', 'z']:
                val = 'з'
            agg[val] = int(agg.get(val, 0)) + int(amount)

        # Списание уже сделали выше, теперь записываем ставки (с суммированием в общем списке)
        for val, amt_sum in agg.items():
            _merge_add_bet(chat_id, message.from_user.id, message.from_user.first_name, int(amt_sum), str(val))

        # Короткое подтверждение
        lines = ["Ваши ставки приняты ✅", ""]
        for val, amt_sum in agg.items():
            lines.append(f"<b>{format_balance(int(amt_sum))}</b> на <b>{safe_html(value_to_text(str(val)))}</b>")

    bot.reply_to(message, "\n".join(lines), parse_mode="HTML")

# --- ОБНОВЛЕННАЯ СЕКЦИЯ РОЗЫГРЫША (СО СТИКЕРАМИ И ШАНСАМИ) ---
active_giveaway = {
    'chat_id': None,
    'amount': 0,
    'participants': {},  # user_id: count_stickers (теперь словарь)
    'is_active': False
}

# --- ПОЛНОСТЬЮ РАБОЧАЯ СЕКЦИЯ РОЗЫГРЫША ---
active_giveaway = {
    'chat_id': None,
    'amount': 0,
    'creator_id': None,
    'creator_name': "",
    'participants': {},  # user_id: count_stickers
    'is_active': False
}


@bot.message_handler(func=lambda m: m.text and (m.text.lower().startswith('!розстар')))
def start_giveaway(message):
    global active_giveaway
    user_id = message.from_user.id

    # Проверка на админа
    if user_id not in ADMIN_IDS:
        bot.reply_to(message, "❌ Только админ может начать розыгрыш!")
        return

    if active_giveaway['is_active']:
        bot.reply_to(message, "⚠️ Сначала завершите текущий розыгрыш командой <code>!итог</code>", parse_mode="HTML")
        return

    try:
        parts = message.text.lower().split()
        if len(parts) < 2:
            bot.reply_to(message, "Используйте: <code>!роз 500к</code>", parse_mode="HTML")
            return

        # Парсинг суммы (к, м, кк)
        amount_raw = parts[1]
        clean_amount = re.sub(r'[^0-9.]', '', amount_raw)
        amount = float(clean_amount)
        if 'кк' in amount_raw or 'kk' in amount_raw or 'м' in amount_raw or 'm' in amount_raw:
            amount *= 1000000
        elif 'к' in amount_raw or 'k' in amount_raw:
            amount *= 1000
        amount = int(amount)

        if amount <= 0: return

        # Списание баланса
        data = load_data()
        user_data = get_user(data, user_id)

        if user_data['balance'] < amount:
            bot.reply_to(message, f"❌ Недостаточно 🪙!\nБаланс: {format_balance(user_data['balance'])}")
            return

        user_data['balance'] -= amount
        save_data(data)

        # Активация
        active_giveaway.update({
            'is_active': True,
            'amount': amount,
            'creator_id': user_id,
            'creator_name': message.from_user.first_name,
            'chat_id': message.chat.id,
            'participants': {}
        })

        bot.send_message(message.chat.id,
                         f"🎉 <b>РОЗЫГРЫШ ОТ {message.from_user.first_name.upper()}!</b>\n"
                         f"━━━━━━━━━━━━━━\n"
                         f"💰 Приз: <b>{format_balance(amount)}</b> 🪙\n\n"
                         f"📝 Для участия пиши: <code>!ботроз</code>\n"
                         f"🔥 Больше сообщений = больше палочек!", parse_mode="HTML")

    except Exception as e:
        bot.reply_to(message, "❌ Ошибка парсинга суммы!")


import threading

# --- КОНФИГУРАЦИЯ ---
active_giveaway = {
    'chat_id': None,
    'message_id': None,
    'amount': 0,
    'creator_name': "",
    'creator_id': None,
    'participants': {},  # user_id: [список эмодзи]
    'is_active': False
}

EMOJI_POOL = ["💖", "💎", "🔥", "⭐", "🍀", "🚀", "🍭", "🍩", "🧁", "⚡"]


def update_giveaway_ui():
    """Функция для автоматического обновления сообщения розыгрыша"""
    global active_giveaway
    if not active_giveaway['is_active']:
        return

    # Добавляем по 1 рандомному эмодзи каждому участнику
    for uid in active_giveaway['participants']:
        active_giveaway['participants'][uid].append(random.choice(EMOJI_POOL))

    # Формируем текст
    lines = []
    # Сортируем участников по количеству эмодзи (лидеры сверху)
    sorted_participants = sorted(active_giveaway['participants'].items(), key=lambda x: len(x[1]), reverse=True)

    for i, (uid, emojis) in enumerate(sorted_participants[:10], 1):  # Топ-10 участников
        u_data = get_user(load_data(), uid)
        name = u_data.get('first_name', 'Игрок')
        emoji_str = "".join(emojis)
        lines.append(f"|{i}️⃣| {emoji_str} | {name}")

    text = (
            f"🎁 <b>РОЗЫГРЫШ НА {format_balance(active_giveaway['amount'])} 🪙!</b>\n"
            f"👤 От: {active_giveaway['creator_name']}\n"
            f"━━━━━━━━━━━━━━\n"
            f"📝 Пиши: <code>!ботроз</code>\n"
            f"⏳ Каждые 10 сек добавляется бонус!\n"
            f"━━━━━━━━━━━━━━\n"
            + ("\n".join(lines) if lines else "<i>Ожидание участников...</i>")
    )

    try:
        # Пытаемся редактировать, если не выходит — переотправляем
        bot.edit_message_text(text, active_giveaway['chat_id'], active_giveaway['message_id'], parse_mode="HTML")
    except:
        msg = bot.send_message(active_giveaway['chat_id'], text, parse_mode="HTML")
        active_giveaway['message_id'] = msg.message_id

    # Запускаем следующий цикл через 10 секунд
    if active_giveaway['is_active']:
        threading.Timer(10, update_giveaway_ui).start()


import threading

# --- КОНФИГУРАЦИЯ ---
active_giveaway = {
    'chat_id': None,
    'message_id': None,
    'amount': 0,
    'creator_name': "",
    'creator_id': None,
    'participants': {},  # user_id: [список эмодзи]
    'is_active': False,
    'is_running': False  # Статус: идет ли уже процесс начисления эмодзи
}

EMOJI_POOL = ["💖", "💎", "🔥", "⭐", "🍀", "🚀", "🍭", "🍩", "🧁", "⚡", "🌈", "🎈", "🧿", "🍒"]


def update_giveaway_ui():
    global active_giveaway
    if not active_giveaway['is_active'] or not active_giveaway['is_running']:
        return

    # Добавляем по 1 рандомному эмодзи каждому участнику
    for uid in active_giveaway['participants']:
        active_giveaway['participants'][uid].append(random.choice(EMOJI_POOL))

    # Формируем текст (Топ-4 участника)
    lines = []
    # Сортируем: у кого больше эмодзи — тот выше
    sorted_p = sorted(active_giveaway['participants'].items(), key=lambda x: len(x[1]), reverse=True)

    total_amount = active_giveaway['amount']
    total_all_emojis = sum(len(e) for e in active_giveaway['participants'].values())

    for i, (uid, emojis) in enumerate(sorted_p[:4], 1):
        u_data = get_user(load_data(), uid)
        name = u_data.get('first_name', 'Игрок')
        emoji_str = "".join(emojis)
        # Визуальный расчет доли
        share = int((len(emojis) / total_all_emojis) * total_amount) if total_all_emojis > 0 else 0
        lines.append(f"|{i}️⃣| {emoji_str} | {format_balance(share)} | {name}")

    text = (
            f"🎁 <b>Розыгрыш {format_balance(active_giveaway['amount'])} от {active_giveaway['creator_name']}</b>\n"
            f"<b>Результат:</b>\n"
            f"━━━━━━━━━━━━━━\n"
            + "\n".join(lines) +
            f"\n━━━━━━━━━━━━━━\n"
            f"⏳ Добавляю эмодзи... Кто соберет больше — заберет куш!"
    )

    try:
        if active_giveaway['message_id']:
            bot.edit_message_text(text, active_giveaway['chat_id'], active_giveaway['message_id'], parse_mode="HTML")
        else:
            msg = bot.send_message(active_giveaway['chat_id'], text, parse_mode="HTML")
            active_giveaway['message_id'] = msg.message_id
    except:
        pass

    # Цикл 10 секунд
    threading.Timer(10, update_giveaway_ui).start()


import threading

# --- КОНФИГУРАЦИЯ ---
active_giveaway = {
    'chat_id': None,
    'message_id': None,
    'amount': 0,
    'creator_name': "",
    'creator_id': None,
    'participants': {},
    'is_active': False,
    'is_running': False,
    'emojis_given': 0  # Счетчик выданных в процессе эмодзи
}

EMOJI_POOL = ["💎", "🔥", "⭐", "🍀", "🚀", "⚡", "🧿", "👑", "🍭", "🍒"]


def update_giveaway_ui():
    global active_giveaway
    if not active_giveaway['is_active'] or not active_giveaway['is_running']:
        return

    participants = active_giveaway['participants']

    # ПРОВЕРКА ЛИМИТА: если выдали 10 эмодзи - СТОП и ИТОГИ
    if active_giveaway['emojis_given'] >= 10:
        finish_giveaway_auto()
        return

    if participants:
        # Выбираем ОДНОГО случайного участника
        p_ids = list(participants.keys())
        lucky_uid = random.choice(p_ids)
        participants[lucky_uid].append(random.choice(EMOJI_POOL))
        active_giveaway['emojis_given'] += 1  # Увеличиваем счетчик

    # Формируем текст
    sorted_p = sorted(participants.items(), key=lambda x: len(x[1]), reverse=True)
    lines = []
    for i, (uid, emojis) in enumerate(sorted_p[:10], 1):
        data = load_data()
        u_name = get_user(data, uid).get('first_name', 'Игрок')
        emoji_str = "".join(emojis)
        lines.append(f"|{i}️⃣| {emoji_str} | {u_name}")

    # Сколько осталось до конца
    left = 10 - active_giveaway['emojis_given']

    text = (
            f"🎁 <b>Розыгрыш {format_balance(active_giveaway['amount'])} от {active_giveaway['creator_name']}</b>\n"
            f"<b>РАСПРЕДЕЛЕНИЕ ДОЛЕЙ...</b>\n"
            f"━━━━━━━━━━━━━━\n"
            + "\n".join(lines) +
            f"\n━━━━━━━━━━━━━━\n"
            f"🎯 Осталось раздать бонусов: <b>{left}</b>\n"
            f"🎲 Каждые 5 сек бонус падает одному из вас!"
    )

    try:
        bot.edit_message_text(text, active_giveaway['chat_id'], active_giveaway['message_id'], parse_mode="HTML")
    except:
        pass

    # Запускаем следующий цикл через 5 секунд
    threading.Timer(5, update_giveaway_ui).start()


def finish_giveaway_auto():
    """Функция автоматического завершения"""
    global active_giveaway
    participants = active_giveaway['participants']
    total_bank = active_giveaway['amount']
    total_emojis = sum(len(e) for e in participants.values())

    data = load_data()
    final_results = []
    sorted_p = sorted(participants.items(), key=lambda x: len(x[1]), reverse=True)

    for uid, emojis in sorted_p:
        count = len(emojis)
        share = int((count / total_emojis) * total_bank)
        bonus = count * 3000

        user_db = get_user(data, uid)
        user_db['balance'] += (share + bonus)
        final_results.append(
            f"👤 {user_db['first_name']}\n└ Доля: <b>{format_balance(share)}</b> | Бонус: <b>{format_balance(bonus)}</b>")

    save_data(data)
    active_giveaway['is_active'] = False
    active_giveaway['is_running'] = False

    res_text = (
            f"🏁 <b>ЛИМИТ ДОСТИГНУТ! ИТОГИ:</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"💰 Банк {format_balance(total_bank)} 🪙 разделен пропорционально:\n\n"
            + "\n\n".join(final_results) +
            f"\n━━━━━━━━━━━━━━\n"
            f"✅ Все монеты зачислены на баланс!"
    )
    bot.send_message(active_giveaway['chat_id'], res_text, parse_mode="HTML")


@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!розстар'))
def start_dynamic_giveaway(message):
    global active_giveaway
    if message.from_user.id not in ADMIN_IDS: return
    if active_giveaway['is_active']: return

    try:
        parts = message.text.lower().split()
        amount_raw = parts[1]
        clean_amount = re.sub(r'[^0-9.]', '', amount_raw)
        amount = int(float(clean_amount) * (1000 if 'к' in amount_raw else 1000000 if 'м' in amount_raw else 1))

        data = load_data()
        user = get_user(data, message.from_user.id)
        if user['balance'] < amount:
            bot.reply_to(message, "❌ Недостаточно средств!")
            return

        user['balance'] -= amount
        save_data(data)

        active_giveaway.update({
            'is_active': True, 'is_running': False, 'amount': amount,
            'creator_id': message.from_user.id, 'creator_name': message.from_user.first_name,
            'chat_id': message.chat.id, 'participants': {}, 'message_id': None, 'emojis_given': 0
        })

        bot.send_message(message.chat.id,
                         f"🎉 <b>РОЗЫГРЫШ НА {format_balance(amount)} 🪙!</b>\n"
                         f"📝 Пиши: <code>!ботроз</code>\n"
                         f"🚀 Шоу (10 бонусов по 5 сек) начнется при 4 участниках!", parse_mode="HTML")
    except:
        bot.reply_to(message, "Ошибка! Пример: !роз 1м")


@bot.message_handler(func=lambda m: m.text and m.text.lower() == '!ботрозстар')
def join_dynamic(message):
    global active_giveaway
    if not active_giveaway['is_active']: return
    uid = message.from_user.id
    if uid not in active_giveaway['participants']:
        active_giveaway['participants'][uid] = [random.choice(EMOJI_POOL)]
        bot.reply_to(message, f"✅ {message.from_user.first_name}, ты в деле!")

    if len(active_giveaway['participants']) >= 4 and not active_giveaway['is_running']:
        active_giveaway['is_running'] = True
        msg = bot.send_message(message.chat.id, "💎 Понеслась! Раздаю 10 случайных эмодзи...")
        active_giveaway['message_id'] = msg.message_id
        update_giveaway_ui()


@bot.message_handler(func=lambda m: m.text and m.text.lower() == '!итогстар')
def end_dynamic_manual(message):
    global active_giveaway
    if message.from_user.id not in ADMIN_IDS or not active_giveaway['is_active']: return

    # Если админ нажал !итог, а 4 человека нет - запускаем принудительно
    if not active_giveaway['is_running']:
        if len(active_giveaway['participants']) > 0:
            active_giveaway['is_running'] = True
            update_giveaway_ui()
        else:
            bot.reply_to(message, "❌ Нет участников!")
    else:
        # Если процесс идет, админ может закончить досрочно
        finish_giveaway_auto()

        # --- ИГРА БАНДАА (EMOJI EDITION) ---
        EMOJI_POOL = [
            "🚮", "🚰", "♿", "🚹", "🚺", "🚻", "🚼", "🚾", "🛂", "🛃", "🛄", "🛅", "🗣️", "👤", "👥", "🫂", "👣",
            "⚠️", "🚸", "⛔", "🚫", "🚳", "🚭", "🚯", "🚱", "🚷", "📵", "🔞", "☢️", "☣️", "🔙", "🔚", "🔛", "🔜", "🔝",
            "🛐", "⚛️", "🕉️", "✡️", "☸️", "☯️", "✝️", "☦️", "☪️", "☮️", "🕎", "🔯", "🪯", "♈", "♉", "♊", "♋",
            "♌", "♍", "♎", "♏", "♐", "♑", "♒", "♓", "⛎", "‼️", "⁉️", "❓", "❔", "❕", "❗", "⚕️", "♻️",
            "⚜️", "🔱", "📛", "🔰", "⭕", "✅", "☑️", "✔️", "❌", "❎", "🔴", "🟠", "🟡", "🟢", "🔵", "🟣", "🟤",
            "⚫", "⚪", "🟥", "🟧", "🟨", "🟩", "🟦", "🟪", "🟫", "⬛", "⬜", "💬", "💭", "👪"
        ]

        @bot.message_handler(func=lambda m: m.text and m.text.strip().lower().startswith(('банда', '!банда', '!band', 'band')))
        def game_band(message):
            """
            🎰 Банда — Мини-слоты
            Формат без мусора вида \\n\\n, один аккуратный результат.
            Пример: Банда 1000000 / !банда 5к / band 1m
            """
            try:
                parts = (message.text or "").strip().split()
                if len(parts) < 2:
                    bot.reply_to(message, "Введите ставку! Пример: <code>Банда 5к</code> или <code>!банда 1м</code>", parse_mode="HTML")
                    return

                amount_raw = parts[1].lower().replace("rb", "").replace("рб", "")
                amount_num = re.sub(r"[^0-9.]", "", amount_raw)
                if not amount_num:
                    bot.reply_to(message, "❌ Сумма нужна числом. Пример: <code>Банда 10000</code>", parse_mode="HTML")
                    return

                mult = 1
                if "к" in amount_raw or "k" in amount_raw:
                    mult = 1000
                elif "м" in amount_raw or "m" in amount_raw:
                    mult = 1000000

                amount = int(float(amount_num) * mult)
                if amount < 100:
                    bot.reply_to(message, "❌ Минимум 100 🪙", parse_mode="HTML")
                    return

                data = load_data()
                user = get_user(data, message.from_user.id, message.from_user.first_name)

                if int(user.get("balance", 0)) < amount:
                    bot.reply_to(message, "❌ Недостаточно средств!", parse_mode="HTML")
                    return

                # Списание ставки
                user["balance"] = int(user.get("balance", 0)) - amount

                # Слот-иконки (приятный набор, без мусора)
                SLOT_EMOJIS = ["⭐️", "🍋", "🍒", "🍇", "🔔", "💎", "7️⃣", "🍀", "🍉"]
                res = [random.choice(SLOT_EMOJIS) for _ in range(3)]

                # Множители
                multiplier = 0
                reason = ""

                if res[0] == res[1] == res[2]:
                    multiplier = 12
                    reason = "💎 ДЖЕКПОТ!"
                elif res[0] == res[1] or res[1] == res[2] or res[0] == res[2]:
                    multiplier = 3
                    reason = "✨ ПАРА!"

                if multiplier > 0:
                    payout = amount * multiplier  # выплата
                    user["balance"] = int(user.get("balance", 0)) + payout
                    save_data(data)

                    text = (
                        "🎰 <b>Банда — Мини-слоты</b>\n"
                        "━━━━━━━━━━━━━━\n"
                        f"Игрок: {name_with_badge_html(message.from_user.id, message.from_user.first_name)}\n"
                        f"Ставка: {format_balance(amount)} 🪙\n\n"
                        f"{res[0]}  {res[1]}  {res[2]}\n\n"
                        f"🏆 <b>ВЫИГРЫШ</b>\n"
                        f"Выигрыш: <b>{format_balance(payout)}</b> 🪙 (x{multiplier})\n"
                        f"{reason}"
                    )
                    bot.reply_to(message, text, parse_mode="HTML")
                    try:
                        save_history(message.from_user.id, "Банда победа", f"+{payout}")
                    except Exception:
                        pass
                else:
                    save_data(data)
                    text = (
                        "🎰 <b>Банда — Мини-слоты</b>\n"
                        "━━━━━━━━━━━━━━\n"
                        f"Игрок: {name_with_badge_html(message.from_user.id, message.from_user.first_name)}\n"
                        f"Ставка: {format_balance(amount)} 🪙\n\n"
                        f"{res[0]}  {res[1]}  {res[2]}\n\n"
                        "💀 <b>ПРОИГРЫШ</b>\n"
                        f"Потеря: {format_balance(amount)} 🪙"
                    )
                    bot.reply_to(message, text, parse_mode="HTML")
                    try:
                        save_history(message.from_user.id, "Банда поражение", f"-{amount}")
                    except Exception:
                        pass

            except Exception:
                bot.reply_to(message, "Ошибка! Пример: <code>Банда 1000</code> или <code>!банда 5к</code>", parse_mode="HTML")

# --- РП-КОМАНДЫ (ПНУТЬ, УБИТЬ И Т.Д.) ---

@bot.message_handler(func=lambda message: message.text and message.text.lower().split()[0] in
                                          ['пнуть', 'убить', 'ударить', 'обнять', 'поцеловать', 'изнасиловать'])
def rp_commands(message):
    text = message.text.lower().split()
    cmd = text[0]

    # 1. ОБРАБОТКА "ВСЕХ" (Только для админов)
    if len(text) > 1 and text[1] == 'всех':
        if message.from_user.id not in ADMIN_IDS:
            bot.reply_to(message, "❌ Эта мощь доступна только админам!")
            return

        users = get_chat_users(message.chat.id)
        if not users:
            bot.reply_to(message, "Я еще никого не запомнил в этом чате!")
            return

        # Убираем самого админа из списка "жертв"
        targets = [f'<a href="tg://user?id={u["id"]}">{u["name"]}</a>' for u in users if
                   u['id'] != message.from_user.id]

        if not targets:
            bot.reply_to(message, "Тут больше никого нет...")
            return

        actions_all = {
            'пнуть': '🦵 пнул всех участников:',
            'убить': '🔪 прикончил всех в этом чате:',
            'ударить': '👊 раздал лещей всем:',
            'обнять': '🫂 тепло обнял всех:',
            'поцеловать': '💋 расцеловал всех:',
            'изнасиловать': '🔞 жестко наказал всех:'
        }

        admin_link = f'<a href="tg://user?id={message.from_user.id}">{name_with_badge_html(message.from_user.id, message.from_user.first_name)}</a>'
        mentions = ", ".join(targets)
        bot.send_message(message.chat.id, f"{admin_link} {actions_all[cmd]}\n\n{mentions}", parse_mode="HTML")
        return

    # 2. ИНДИВИДУАЛЬНАЯ КОМАНДА (Через Reply)
    if not message.reply_to_message:
        bot.reply_to(message, f"Чтобы {cmd}, ответь на сообщение того, кого хочешь {cmd}!")
        return

    # Данные отправителя и цели
    user_who = f'<a href="tg://user?id={message.from_user.id}">{name_with_badge_html(message.from_user.id, message.from_user.first_name)}</a>'
    target_user = message.reply_to_message.from_user
    user_target = f'<a href="tg://user?id={target_user.id}">{target_user.first_name}</a>'

    # Словарь действий
    actions = {
        'пнуть': ('🦵', 'пнул'),
        'убить': ('🔪', 'убил'),
        'ударить': ('👊', 'ударил'),
        'обнять': ('🫂', 'обнял'),
        'поцеловать': ('💋', 'поцеловал'),
        'изнасиловать': ('🔞', 'изнасиловал')
    }

    emoji, action_text = actions[cmd]

    bot.send_message(
        message.chat.id,
        f"{emoji} | {user_who} {action_text} {user_target}",
        parse_mode="HTML"
    )


# ----------------------------------------


# ================== ДОП. ФУНКЦИИ (ВА-БАНК / КРИПТОФЕРМЕР / ТОП ДНЯ) ==================

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('ва-банк'))
def va_bank_cmd(message):
    if message.chat.type == 'private':
        return
    parts = message.text.lower().split()
    if len(parts) < 2:
        bot.reply_to(message, "Пример: <code>Ва-банк 5</code> или <code>Ва-банк к</code>", parse_mode="HTML")
        return

    target = parts[1].strip()
    # Нормализация цели
    if target in ['красное', 'крас', 'к']:
        target = 'к'
    elif target in ['черное', 'черн', 'ч']:
        target = 'ч'
    elif target in ['зеленое', 'зел', 'з', '0', 'зеро']:
        target = 'з' if target in ['зеленое', 'зел', 'з'] else '0'

    target = normalize_bet_target(target)
    if not is_valid_bet_value(target):
        bot.reply_to(message, "❌ Можно ставить только на 0–12 или к/ч/з.")
        return
    data = load_data()
    user = get_user(data, message.from_user.id, message.from_user.first_name)
    amount = int(user.get('balance', 0))
    if amount <= 0:
        bot.reply_to(message, "❌ Баланс ноль.")
        return

    # Ставим всей суммой (через place_bet)
    call = {'from_user': message.from_user, 'message': message, 'data': f"{amount}_{target}"}
    place_bet(call)


@bot.message_handler(func=lambda m: m.text and m.text.lower() in ['!топдня', 'топдня'])
def top_day_cmd(message):
    if message.chat.type == 'private':
        return
    chat_id = str(message.chat.id)
    stats = get_topday_profit(chat_id)
    if not stats:
        bot.reply_to(message, "Сегодня ещё нет результатов 😴")
        return

    # Сортируем по сумме выигрышей
    top = sorted(stats.items(), key=lambda x: int(x[1]), reverse=True)[:5]
    lines = []
    for i, (uid, won_sum) in enumerate(top, 1):
        try:
            uid_int = int(uid)
            name = bot.get_chat_member(message.chat.id, uid_int).user.first_name
        except Exception:
            name = f"Юзер {uid}"

        won_sum = int(won_sum)
        lines.append(f"{i}. {safe_html(name)} — <b>{format_balance(won_sum)}</b> 🪙")

    bot.send_message(message.chat.id, "🏆 <b>ТОП ДНЯ (выиграли):</b>\n━━━━━━━━━━━━━━\n" + "\n".join(lines), parse_mode="HTML")





@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in ['!стата', 'стата', '!статистика', 'статистика'])
def cmd_chat_stats_24h(message):
    """ТОП по сообщениям за последние 24 часа — раздельно по чатам."""
    # Игнорируем старые апдейты (когда бот был оффлайн)
    try:
        if hasattr(message, 'date') and int(getattr(message, 'date', 0) or 0) < int(BOT_START_UNIX):
            return
    except Exception:
        pass
    if message.chat.type == 'private':
        bot.reply_to(message, "ℹ️ Команда <code>!стата</code> работает в группах (статистика по чату).", parse_mode="HTML")
        return

    items = top_message_stats_24h(message.chat.id, limit=10)
    if not items:
        bot.reply_to(message, "📊 Пока нет статистики за последние 24 часа.")
        return

    lines = []
    for i, (_, name, cnt) in enumerate(items, 1):
        lines.append(f"{i}. {safe_html(name)} — <b>{cnt}</b>")

    text_out = "📊 <b>Статистика сообщений (24 часа)</b>\n━━━━━━━━━━━━━━\n" + "\n".join(lines)
    bot.send_message(message.chat.id, text_out, parse_mode="HTML")


@bot.message_handler(func=lambda m: m.text and m.text.lower() in ['!криптофермер', 'криптофермер'])
def crypto_farmer_cmd(message):
    # Можно и в ЛС, и в группах
    user_id = str(message.from_user.id)
    data = load_data()
    user = get_user(data, user_id, message.from_user.first_name)

    now = time.time()

    # анти-спам: раз в 10 минут
    last_use = float(user.get('farm_last_use', 0))
    cd = 10 * 60
    if now - last_use < cd:
        left = int(cd - (now - last_use))
        mm = left // 60
        ss = left % 60
        bot.reply_to(message, f"⏳ Подожди {mm}м {ss}с, чтобы снова проверить ферму.")
        return

    user['farm_last_use'] = now

    # накопление: 200 000 за 30 минут
    CAP = 200000
    PERIOD = 30 * 60  # 1800 сек (30 минут)
    rate = CAP / PERIOD

    last_claim = float(user.get('farm_last_claim', now))
    elapsed = max(0.0, now - last_claim)
    earned = int(min(CAP, elapsed * rate))

    # время до полного
    left_sec = max(0, int(PERIOD - elapsed))
    hh = left_sec // 3600
    mm = (left_sec % 3600) // 60
    ss = left_sec % 60

    if earned > 0:
        user['balance'] = int(user.get('balance', 0)) + earned
        user['farm_last_claim'] = now
        save_history(user_id, "криптоферма", f"+{earned}")
        save_data(data)
        bot.reply_to(
            message,
            f"⛏ <b>Криптофермер</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"✅ Собрано: <b>{format_balance(earned)}</b> 🪙\n"
            f"💰 Баланс: <b>{format_balance(int(user.get('balance', 0)))}</b> 🪙\n"
            f"━━━━━━━━━━━━━━\n"
            f"⏳ До полного {format_balance(CAP)}: {hh}ч {mm}м {ss}с",
            parse_mode="HTML"
        )
    else:
        # если впервые — фиксируем старт накопления
        if 'farm_last_claim' not in user:
            user['farm_last_claim'] = now
            save_data(data)

        bot.reply_to(
            message,
            f"⛏ <b>Криптофермер</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"Пока пусто 😅\n"
            f"⏳ До полного {format_balance(CAP)}: {hh}ч {mm}м {ss}с",
            parse_mode="HTML"
        )




# =========================
# =========================
# 🔥 !fire (сумма) — распределение по самым активным (последние 100 сообщений)
# =========================

def _format_participant_entry(entry):
    """
    entry может быть:
      - str: "Nur"
      - dict: {"name": "Nur", "username": "nurtpp"} или {"first_name": "...", "username": "..."}
    Возвращает красивую строку: "Имя (@username)" или просто "Имя"
    """
    try:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("first_name") or entry.get("firstName") or "Игрок"
            username = entry.get("username")
            if username:
                return f"{name} (@{username})"
            return str(name)

        if isinstance(entry, str):
            s = entry.strip()
            return s if s else "Игрок"

        return "Игрок"
    except Exception:
        return "Игрок"


@bot.message_handler(func=lambda m: m.text and m.text.strip().lower().startswith('!fire'))
def fire_cmd(message):
    if message.chat.type == 'private':
        return
    if not _bot_cd_ok(message):
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❗ Используй так: <code>!fire 5000000</code>", parse_mode="HTML")
        return

    amount = parse_human_amount(parts[1])
    if amount <= 0:
        bot.reply_to(message, "❗ Сумма должна быть числом. Пример: <code>!fire 5000000</code>", parse_mode="HTML")
        return

    chat_id = str(message.chat.id)
    sender_id = int(message.from_user.id)
    sender_name = getattr(message.from_user, "first_name", "Игрок")

    # Собираем активных из последних 100 сообщений
    dq = _recent_messages.get(chat_id)
    if not dq:
        bot.reply_to(message, "❌ Пока нет истории сообщений для !fire (нужно хотя бы немного активности в чате).")
        return

    # считаем активность
    counts = {}
    for uid in dq:
        try:
            uid = int(uid)
            counts[uid] = counts.get(uid, 0) + 1
        except Exception:
            continue

    # убираем бота
    try:
        counts.pop(int(BOT_ID), None)
    except Exception:
        pass

    # ❗ НЕ раздаём тому, кто написал !fire
    counts.pop(sender_id, None)

    if not counts:
        bot.reply_to(message, "❌ Не нашёл активных игроков в последних 100 сообщениях.")
        return

    participants = list(counts.keys())
    n = len(participants)
    if n <= 0:
        bot.reply_to(message, "❌ Не нашёл активных игроков.")
        return

    # Проверяем баланс отправителя и списываем
    data = load_data()
    sender = get_user(data, sender_id, sender_name)
    if int(sender.get('balance', 0)) < amount:
        bot.reply_to(message, f"❌ Недостаточно 🪙! Нужно: <b>{format_balance(amount)}</b>", parse_mode="HTML")
        return

    # распределение: поровну, остаток — самым активным (по количеству сообщений)
    base = amount // n
    rem = amount - base * n

    # сортируем по активности (desc), потом по uid (стабильно)
    participants_sorted = sorted(participants, key=lambda uid: (counts.get(uid, 0), uid), reverse=True)

    # списываем у отправителя
    sender['balance'] = int(sender.get('balance', 0)) - amount

    # начисления + карта сколько кто получил
    give_map = {}

    for i, uid in enumerate(participants_sorted):
        give = base + (1 if i < rem else 0)
        if give <= 0:
            continue

        entry = (chat_participants.get(chat_id, {}) or {}).get(str(uid))
        disp_name = _format_participant_entry(entry)

        u = get_user(data, uid, disp_name)
        u['balance'] = int(u.get('balance', 0)) + give
        give_map[uid] = give

        try:
            save_history(uid, f"!fire от {sender_name}", f"+{give}")
        except Exception:
            pass

    try:
        save_history(sender_id, "!fire раздача", f"-{amount}")
    except Exception:
        pass

    save_data(data)

    # красивый отчёт (топ-10)
    lines = []
    for uid in participants_sorted[:10]:
        give = int(give_map.get(uid, 0))

        entry = (chat_participants.get(chat_id, {}) or {}).get(str(uid))
        name = _format_participant_entry(entry)

        # если вообще пусто — берём из data (НЕ load_data заново!)
        if not name or name == "Игрок":
            try:
                name = get_user(data, uid).get('first_name', 'Игрок')
            except Exception:
                name = "Игрок"

        lines.append(
            f"• <a href='tg://user?id={uid}'>{safe_html(name)}</a> — <b>{format_balance(give)}</b> 🪙"
        )

    text = (
        "🔥 <b>FIRE-раздача</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"От: <a href='tg://user?id={sender_id}'>{safe_html(sender_name)}</a>\n"
        f"Сумма: <b>{format_balance(amount)}</b> 🪙\n"
        f"Активных (последние 100 сообщений): <b>{n}</b>\n"
        "━━━━━━━━━━━━━━\n"
        + (("<b>Топ получателей:</b>\n" + "\n".join(lines)) if lines else "")
    )

    bot.send_message(message.chat.id, text, parse_mode="HTML")

# ================== РОЗЫГРЫШИ (ОТДЕЛЬНО ПО ЧАТАМ, МОГУТ СОЗДАВАТЬ ВСЕ) ==================

GIVEAWAYS_FILE = 'giveaways_by_chat.json'
giveaways_by_chat = {}

def _load_giveaways():
    global giveaways_by_chat
    try:
        if os.path.exists(GIVEAWAYS_FILE) and os.path.getsize(GIVEAWAYS_FILE) > 0:
            with open(GIVEAWAYS_FILE, 'r', encoding='utf-8') as f:
                d = json.load(f)
                if isinstance(d, dict):
                    giveaways_by_chat = d
    except Exception:
        giveaways_by_chat = {}

def _save_giveaways():
    try:
        with open(GIVEAWAYS_FILE, 'w', encoding='utf-8') as f:
            json.dump(giveaways_by_chat, f, ensure_ascii=False, indent=4)
    except Exception:
        pass

_load_giveaways()

def _fmt_giveaway(chat_id: str):
    g = giveaways_by_chat.get(str(chat_id))
    if not g or not g.get('active'):
        return None

    parts = list(g.get('participants', {}).items())
    lines = []
    for i, (uid, name) in enumerate(parts[:20], 1):
        lines.append(f"{i}. {safe_html(name)}")
    participants_txt = "\n".join(lines) if lines else "<i>Пока никто не участвует…</i>"

    text = (
        f"🎁 <b>РОЗЫГРЫШ</b> на <b>{format_balance(int(g.get('amount', 0)))}</b> 🪙\n"
        f"👤 Создал: {safe_html(g.get('creator_name', ''))}\n"
        f"━━━━━━━━━━━━━━\n"
        f"📝 Чтобы участвовать: <code>!ботроз</code>\n"
        f"🏁 Завершить: <code>!итог</code> (создатель/админ)\n"
        f"━━━━━━━━━━━━━━\n"
        f"{participants_txt}"
    )
    return text


# ================== РОЗЫГРЫШИ (ИНЛАЙН + НАПОМИНАНИЯ + АВТОСТАРТ 20) ==================
# Команды:
# - !роз 500к  (создать розыгрыш в чате)
# - !ботроз    (войти)
# - !итог      (старт/финиш: создатель или админ)
# - /cencelroz (принудительно отменить розыгрыш в этом чате, только админ)
#
# Визуально:
# - Инлайн кнопки: ✅ Войти / 🚀 Старт
# - Напоминание каждые 5 минут пока не стартанул
# - Автостарт когда 20 участников

_GW_REMINDER_EVERY = 300  # 5 минут
_GW_TICK_EVERY = 5        # 5 секунд
_GW_MAX_TICKS = 12        # 12 тиков = 1 минута
_GW_EMOJI_POOL = ["💎","🔥","⭐","🍀","🚀","⚡","🧿","👑","🍭","🍒","🎁","🎯"]

def _is_chat_admin(message) -> bool:
    try:
        if message.chat.type == 'private':
            return False
        member = bot.get_chat_member(message.chat.id, message.from_user.id)
        return member.status in ("administrator", "creator")
    except Exception:
        # fallback: владелец бота
        return message.from_user.id in ADMIN_IDS

def _gw_markup(chat_id: str) -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(
        types.InlineKeyboardButton("✅ Войти", callback_data=f"gw_join:{chat_id}"),
        types.InlineKeyboardButton("🚀 Старт", callback_data=f"gw_start:{chat_id}")
    )
    return mk

def _gw_render(chat_id: str) -> str:
    g = giveaways_by_chat.get(str(chat_id))
    if not g or not g.get('active'):
        return ""
    amount = int(g.get('amount', 0))
    creator = safe_html(str(g.get('creator_name', '')))
    parts = list((g.get('participants') or {}).items())
    # список участников
    lines = []
    for i, (uid, name) in enumerate(parts[:20], 1):
        lines.append(f"{i}. <a href='tg://user?id={uid}'>{safe_html(name)}</a>")
    participants_txt = "\n".join(lines) if lines else "<i>Пока никто не вошёл…</i>"
    extra = ""
    if g.get('running'):
        extra = "\n\n<b>🎲 ИДЁТ РОЗЫГРЫШ…</b> Каждые 5 секунд кому-то падает эмодзи!"
    elif g.get('started'):
        extra = "\n\n<b>⏳ Подготовка…</b>"
    return (
        f"🎉 <b>РОЗЫГРЫШ</b> на <b>{format_balance(amount)}</b> 🪙\n"
        f"👤 Создал: <b>{creator}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"✅ Войти: кнопка или <code>!ботроз</code>\n"
        f"🚀 Старт: кнопка или <code>!итог</code> (создатель/админ)\n"
        f"━━━━━━━━━━━━━━\n"
        f"👥 Участники: <b>{len(parts)}</b>/20\n"
        f"{participants_txt}"
        f"{extra}"
    )

def _gw_try_edit(chat_id_int: int, message_id: int, text: str):
    try:
        bot.edit_message_text(text, chat_id_int, message_id, parse_mode="HTML", disable_web_page_preview=True, reply_markup=_gw_markup(str(chat_id_int)))
    except Exception:
        try:
            bot.send_message(chat_id_int, text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=_gw_markup(str(chat_id_int)))
        except Exception:
            pass

def _gw_reminder_tick(chat_id: str):
    try:
        g = giveaways_by_chat.get(str(chat_id))
        if not g or not g.get('active'):
            return
        if g.get('running'):
            return
        # шлём напоминание и ещё раз планируем
        chat_id_int = int(chat_id)
        text = _gw_render(chat_id)
        if text:
            try:
                old_mid = g.get('reminder_message_id')
                if old_mid:
                    bot.delete_message(chat_id_int, int(old_mid))
            except Exception:
                pass
            try:
                msg = bot.send_message(chat_id_int, "⏰ <b>Напоминание о розыгрыше!</b>\n\n" + text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=_gw_markup(chat_id))
                g['reminder_message_id'] = int(msg.message_id)
                giveaways_by_chat[str(chat_id)] = g
                _save_giveaways()
            except Exception:
                pass
        # планируем следующий тик
        threading.Timer(_GW_REMINDER_EVERY, lambda cid=chat_id: _gw_reminder_tick(cid)).start()
    except Exception:
        pass

def _gw_start_run(chat_id: str, started_by: int = 0, auto: bool = False):
    # запускаем шоу раздачи эмодзи
    try:
        g = giveaways_by_chat.get(str(chat_id))
        if not g or not g.get('active'):
            return
        if g.get('running'):
            return
        parts = g.get('participants') or {}
        if len(parts) < 2:
            try:
                bot.send_message(int(chat_id), "⚠️ Нужно минимум 2 участника для старта розыгрыша.")
            except Exception:
                pass
            return

        g['started'] = True
        g['running'] = True
        g['ticks'] = 0
        g['emoji_counts'] = {str(uid): 0 for uid in parts.keys()}
        giveaways_by_chat[str(chat_id)] = g
        _save_giveaways()

        chat_id_int = int(chat_id)
        # пробуем редактировать основное сообщение, если есть
        main_mid = g.get('message_id')
        text = _gw_render(chat_id)
        if main_mid:
            try:
                _gw_try_edit(chat_id_int, int(main_mid), text)
            except Exception:
                pass
        else:
            try:
                msg = bot.send_message(chat_id_int, text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=_gw_markup(chat_id))
                g['message_id'] = int(msg.message_id)
                giveaways_by_chat[str(chat_id)] = g
                _save_giveaways()
            except Exception:
                pass

        # первый тик
        threading.Timer(_GW_TICK_EVERY, lambda cid=chat_id: _gw_tick(cid)).start()
        if auto:
            try:
                bot.send_message(chat_id_int, "🚀 <b>Автостарт!</b> Набралось 20 участников — поехали!", parse_mode="HTML")
            except Exception:
                pass
    except Exception:
        pass

def _gw_tick(chat_id: str):
    try:
        g = giveaways_by_chat.get(str(chat_id))
        if not g or not g.get('active') or not g.get('running'):
            return

        parts = g.get('participants') or {}
        if not parts:
            return

        g['ticks'] = int(g.get('ticks', 0)) + 1
        # рандомно кому-то добавляем "эмодзи-поинт"
        lucky_uid = str(random.choice(list(parts.keys())))
        ec = g.get('emoji_counts') or {}
        ec[lucky_uid] = int(ec.get(lucky_uid, 0)) + 1
        g['emoji_counts'] = ec

        # строим табло (топ-10)
        leaderboard = sorted(ec.items(), key=lambda x: int(x[1]), reverse=True)
        lines = []
        for i, (uid, cnt) in enumerate(leaderboard[:10], 1):
            nm = parts.get(uid, "Игрок")
            bar = random.choice(_GW_EMOJI_POOL) * min(int(cnt), 10)
            lines.append(f"{i}. <a href='tg://user?id={uid}'>{safe_html(nm)}</a> — {bar} <b>x{cnt}</b>")

        left = max(0, _GW_MAX_TICKS - int(g.get('ticks', 0)))
        dash = (
            "🎰 <b>РОЗЫГРЫШ ИДЁТ!</b>\n"
            f"⏳ Осталось тиков: <b>{left}</b> (каждые 5 сек)\n"
            "━━━━━━━━━━━━━━\n" +
            ("\n".join(lines) if lines else "<i>Пока пусто…</i>")
        )

        chat_id_int = int(chat_id)
        main_mid = g.get('message_id')
        # Каждые 2 тика: удаляем старое сообщение и отправляем новое (эффект)
        if main_mid and int(g.get('ticks', 0)) % 2 == 0:
            try:
                bot.delete_message(chat_id_int, int(main_mid))
            except Exception:
                pass
            try:
                msg = bot.send_message(chat_id_int, dash, parse_mode="HTML", disable_web_page_preview=True)
                g['message_id'] = int(msg.message_id)
            except Exception:
                pass
        else:
            if main_mid:
                try:
                    bot.edit_message_text(dash, chat_id_int, int(main_mid), parse_mode="HTML", disable_web_page_preview=True, reply_markup=None)
                except Exception:
                    try:
                        msg = bot.send_message(chat_id_int, dash, parse_mode="HTML", disable_web_page_preview=True)
                        g['message_id'] = int(msg.message_id)
                    except Exception:
                        pass
            else:
                try:
                    msg = bot.send_message(chat_id_int, dash, parse_mode="HTML", disable_web_page_preview=True)
                    g['message_id'] = int(msg.message_id)
                except Exception:
                    pass

        giveaways_by_chat[str(chat_id)] = g
        _save_giveaways()

        if int(g.get('ticks', 0)) >= _GW_MAX_TICKS:
            _gw_finish(chat_id)
            return

        threading.Timer(_GW_TICK_EVERY, lambda cid=chat_id: _gw_tick(cid)).start()
    except Exception:
        pass

def _gw_finish(chat_id: str):
    try:
        g = giveaways_by_chat.get(str(chat_id))
        if not g or not g.get('active'):
            return
        parts = g.get('participants') or {}
        ec = g.get('emoji_counts') or {}
        amount = int(g.get('amount', 0))
        chat_id_int = int(chat_id)

        # если по нулям — выбираем победителя рандомно
        total = sum(int(v) for v in ec.values()) if ec else 0
        if total <= 0:
            # выдаём весь банк одному
            winner_uid = str(random.choice(list(parts.keys())))
            data = load_data()
            winner = get_user(data, int(winner_uid), parts.get(winner_uid, "Победитель"))
            winner['balance'] = int(winner.get('balance', 0)) + amount
            save_data(data)
            winner_link = f"<a href='tg://user?id={winner_uid}'>{safe_html(winner.get('first_name','Победитель'))}</a>"
            bot.send_message(chat_id_int, f"🏆 Победитель: {winner_link}\n💰 Приз: <b>{format_balance(amount)}</b> 🪙", parse_mode="HTML")
        else:
            # распределяем пропорционально эмодзи
            data = load_data()
            leaderboard = sorted(ec.items(), key=lambda x: int(x[1]), reverse=True)

            payouts = {}
            paid_total = 0
            for uid, cnt in leaderboard:
                cnt = int(cnt)
                share = int((cnt / total) * amount)
                if share > 0:
                    payouts[str(uid)] = share
                    paid_total += share

            # остаток — лидеру
            leftover = amount - paid_total
            if leaderboard:
                leader_uid = str(leaderboard[0][0])
                payouts[leader_uid] = int(payouts.get(leader_uid, 0)) + max(0, leftover)

            # начисляем
            result_lines = []
            for uid, share in sorted(payouts.items(), key=lambda x: x[1], reverse=True)[:10]:
                u = get_user(data, int(uid), parts.get(uid, "Игрок"))
                u['balance'] = int(u.get('balance', 0)) + int(share)
                result_lines.append(f"• <a href='tg://user?id={uid}'>{safe_html(u.get('first_name','Игрок'))}</a> — <b>{format_balance(int(share))}</b> 🪙")

            save_data(data)
            res = (
                f"🏁 <b>ИТОГИ РОЗЫГРЫША</b>\n"
                f"💰 Банк: <b>{format_balance(amount)}</b> 🪙\n"
                f"━━━━━━━━━━━━━━\n" +
                ("\n".join(result_lines) if result_lines else "<i>Никому не досталось…</i>") +
                "\n━━━━━━━━━━━━━━\n✅ Зачислено на баланс!"
            )
            bot.send_message(chat_id_int, res, parse_mode="HTML")

        # закрываем
        g['active'] = False
        g['running'] = False
        g['started'] = False
        giveaways_by_chat[str(chat_id)] = g
        _save_giveaways()

        # пытаемся убрать старое сообщение розыгрыша
        try:
            mid = g.get('message_id')
            if mid:
                bot.edit_message_reply_markup(chat_id_int, int(mid), reply_markup=None)
        except Exception:
            pass
    except Exception:
        pass

@bot.message_handler(commands=['cencelroz'])
def cmd_cencelroz(message):
    # принудительная отмена розыгрыша в текущем чате (админ/создатель)
    if message.chat.type == 'private':
        return
    if not _is_chat_admin(message):
        bot.reply_to(message, "❌ Команда доступна только администраторам чата.")
        return
    chat_id = str(message.chat.id)
    g = giveaways_by_chat.get(chat_id)
    if not g or not g.get('active'):
        bot.reply_to(message, "ℹ️ В этом чате нет активного розыгрыша.")
        return

    # возврат банка создателю
    try:
        amount = int(g.get('amount', 0))
        creator_id = int(g.get('creator_id', 0))
        creator_name = str(g.get('creator_name', 'Игрок'))
        data = load_data()
        u = get_user(data, creator_id, creator_name)
        u['balance'] = int(u.get('balance', 0)) + amount
        save_data(data)
    except Exception:
        pass

    g['active'] = False
    g['running'] = False
    g['started'] = False
    giveaways_by_chat[chat_id] = g
    _save_giveaways()

    try:
        bot.reply_to(message, "✅ Розыгрыш отменён. Банк возвращён создателю.")
    except Exception:
        pass

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith('gw_'))
def cb_gw(c):
    try:
        action, chat_id = c.data.split(":", 1)
    except Exception:
        return
    chat_id = str(chat_id)

    g = giveaways_by_chat.get(chat_id)
    if not g or not g.get('active'):
        try:
            bot.answer_callback_query(c.id, "Розыгрыш не активен.", show_alert=False)
        except Exception:
            pass
        return

    uid = str(c.from_user.id)
    name = c.from_user.first_name or "Игрок"

    if action == "gw_join":
        if uid not in (g.get('participants') or {}):
            g.setdefault('participants', {})[uid] = name
            giveaways_by_chat[chat_id] = g
            _save_giveaways()

        # автостарт на 20
        if len(g.get('participants') or {}) >= 20 and not g.get('running'):
            _gw_start_run(chat_id, started_by=c.from_user.id, auto=True)

        # обновляем основное сообщение
        try:
            text = _gw_render(chat_id)
            bot.edit_message_text(text, int(chat_id), int(g.get('message_id')), parse_mode="HTML",
                                  disable_web_page_preview=True, reply_markup=_gw_markup(chat_id))
        except Exception:
            pass

        try:
            bot.answer_callback_query(c.id, "✅ Ты в розыгрыше!", show_alert=False)
        except Exception:
            pass
        return

    if action == "gw_start":
        # стартовать может создатель или админ чата
        is_creator = int(g.get('creator_id', 0)) == int(c.from_user.id)
        try:
            # проверяем админство чата
            member = bot.get_chat_member(int(chat_id), c.from_user.id)
            is_admin_chat = member.status in ("administrator", "creator")
        except Exception:
            is_admin_chat = c.from_user.id in ADMIN_IDS

        if not (is_creator or is_admin_chat):
            try:
                bot.answer_callback_query(c.id, "Только создатель/админ может стартовать.", show_alert=True)
            except Exception:
                pass
            return

        _gw_start_run(chat_id, started_by=c.from_user.id, auto=False)
        try:
            bot.answer_callback_query(c.id, "🚀 Старт!", show_alert=False)
        except Exception:
            pass
        return


@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!роз '))
def giveaway_start(message):
    if message.chat.type == 'private':
        return
    chat_id = str(message.chat.id)
    if giveaways_by_chat.get(chat_id, {}).get('active'):
        bot.reply_to(message, "⚠️ В этом чате уже идёт розыгрыш.", parse_mode="HTML")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Пример: <code>!роз 500к</code> или <code>!роз 100000</code>", parse_mode="HTML")
        return

    amount_raw = parts[1].strip().lower()
    clean_amount = re.sub(r'[^0-9.]', '', amount_raw)
    try:
        amount = int(float(clean_amount) * (1000 if 'к' in amount_raw else 1000000 if 'м' in amount_raw else 1))
    except Exception:
        bot.reply_to(message, "❌ Неверная сумма.")
        return

    if amount <= 0:
        bot.reply_to(message, "❌ Сумма должна быть больше нуля.")
        return

    data = load_data()
    user = get_user(data, message.from_user.id, message.from_user.first_name)
    if int(user.get('balance', 0)) < amount:
        bot.reply_to(message, "❌ Недостаточно средств!")
        return

    user['balance'] = int(user.get('balance', 0)) - int(amount)
    save_data(data)

    giveaways_by_chat[chat_id] = {
        'active': True,
        'amount': int(amount),
        'creator_id': int(message.from_user.id),
        'creator_name': message.from_user.first_name,
        'participants': {},     # uid -> name
        'message_id': None,
        'started': False,
        'running': False,
        'ticks': 0,
        'emoji_counts': {}
    }
    _save_giveaways()

    text = _gw_render(chat_id)
    try:
        msg = bot.send_message(message.chat.id, text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=_gw_markup(chat_id))
        giveaways_by_chat[chat_id]['message_id'] = int(msg.message_id)
        _save_giveaways()
    except Exception:
        bot.send_message(message.chat.id, text, parse_mode="HTML", disable_web_page_preview=True)

    # напоминания каждые 5 минут
    try:
        threading.Timer(_GW_REMINDER_EVERY, lambda cid=chat_id: _gw_reminder_tick(cid)).start()
    except Exception:
        pass

def giveaway_join(message):
    if message.chat.type == 'private':
        return
    chat_id = str(message.chat.id)
    g = giveaways_by_chat.get(chat_id)
    if not g:
        return
    if not g.get('active'):
        bot.reply_to(message, "⚠️ Розыгрыш сейчас не активен. Если он завис — админ может сбросить: /cencelroz", parse_mode="HTML")
        return

    uid = str(message.from_user.id)
    if uid in g.get('participants', {}):
        return

    g['participants'][uid] = message.from_user.first_name
    giveaways_by_chat[chat_id] = g
    _save_giveaways()

    text = _fmt_giveaway(chat_id)
    if text:
        bot.send_message(message.chat.id, text, parse_mode="HTML")



@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == '!ботроз')
def giveaway_join_cmd(message):
    if message.chat.type == 'private':
        return
    chat_id = str(message.chat.id)
    g = giveaways_by_chat.get(chat_id)

    # если розыгрыш не активен — подскажем про принудительную отмену
    if not g or not g.get('active'):
        if g:
            bot.reply_to(message, "⚠️ В этом чате есть зависший/неактивный розыгрыш. Админ может сбросить: /cencelroz", parse_mode="HTML")
        return

    uid = str(message.from_user.id)
    name = message.from_user.first_name or "Игрок"

    if uid not in (g.get('participants') or {}):
        g.setdefault('participants', {})[uid] = name
        giveaways_by_chat[chat_id] = g
        _save_giveaways()

    # автостарт на 20
    if len(g.get('participants') or {}) >= 20 and not g.get('running'):
        _gw_start_run(chat_id, started_by=message.from_user.id, auto=True)

    # обновим основное сообщение (если есть message_id)
    try:
        text = _gw_render(chat_id)
        mid = int(g.get('message_id') or 0)
        if mid:
            bot.edit_message_text(text, int(chat_id), mid, parse_mode="HTML",
                                  disable_web_page_preview=True, reply_markup=_gw_markup(chat_id))
        else:
            bot.send_message(message.chat.id, text, parse_mode="HTML",
                             disable_web_page_preview=True, reply_markup=_gw_markup(chat_id))
    except Exception:
        pass


@bot.message_handler(func=lambda m: m.text and m.text.lower() == '!итог')
def giveaway_finish(message):
    if message.chat.type == 'private':
        return
    chat_id = str(message.chat.id)
    g = giveaways_by_chat.get(chat_id)
    if not g or not g.get('active'):
        return

    is_creator = int(g.get('creator_id', 0)) == int(message.from_user.id)
    # админ чата или владелец бота
    is_admin_chat = _is_chat_admin(message)
    if not (is_creator or is_admin_chat):
        bot.reply_to(message, "❌ Старт/завершить может только создатель или админ чата.")
        return

    # если шоу ещё не идёт — запускаем
    if not g.get('running'):
        _gw_start_run(chat_id, started_by=message.from_user.id, auto=False)
        return

    # если уже идёт — принудительно завершаем
    _gw_finish(chat_id)


@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!ограбить')
def robbery_handler(message):
    # Только в чатах
    if message.chat.type == 'private':
        bot.reply_to(message, "ℹ️ Ограбления работают только в чатах.")
        return



    # Доступ к ограблению: только купившие (или админы)
    if (message.from_user.id not in ADMIN_IDS) and (not has_robbery_access(str(message.from_user.id))):
        url = _deep_link_url("shop")
        markup = types.InlineKeyboardMarkup()
        if url:
            markup.add(types.InlineKeyboardButton("🛒 Магазин", url=url))
        bot.reply_to(
            message,
            "❌ Команда <code>!ограбить</code> доступна после покупки.\nОткрой ЛС бота → 🛒 Магазин.",
            reply_markup=markup if url else None,
            parse_mode="HTML"
        )
        return

    if not message.reply_to_message:
        bot.reply_to(
            message,
            "❌ Нужно ответить на сообщение игрока командой <code>!ограбить</code>.",
            parse_mode="HTML"
        )
        return

    robber_id = str(message.from_user.id)
    victim_id = str(message.reply_to_message.from_user.id)
    chat_id = str(message.chat.id)

    # Нельзя грабить себя / бота
    try:
        if victim_id == robber_id:
            bot.reply_to(message, "🤨 Самого себя ограбить нельзя.")
            return
        if getattr(message.reply_to_message.from_user, 'is_bot', False) or (
            BOT_ID is not None and int(victim_id) == int(BOT_ID)
        ):
            bot.reply_to(message, "🤖 Бота грабить нельзя 🙂")
            return
    except Exception:
        pass

    # --- КД 2 часа ---
    cd_data = load_robbery_cd()
    cd_key = f"{robber_id}"  # глобальный КД: нельзя грабить в другом чате, пока не прошёл таймер
    now = int(time.time())
    last_time = int(cd_data.get(cd_key, 0))

    if now - last_time < ROBBERY_CD_SECONDS:
        left = ROBBERY_CD_SECONDS - (now - last_time)
        h = left // 3600
        m = (left % 3600) // 60
        s = left % 60
        bot.reply_to(
            message,
            f"⏳ Сейчас идет рейд! Попробуйте через <b>{h}ч {m}м {s}с</b>",
            parse_mode="HTML"
        )
        return

    lock = _lock_for_chat(chat_id)
    with lock:
        data = load_data()
        robber = get_user(data, robber_id, message.from_user.first_name)
        victim = get_user(data, victim_id, message.reply_to_message.from_user.first_name)

        robber_balance = int(robber.get('balance', 0))
        victim_balance = int(victim.get('balance', 0))

        if victim_balance <= 0:
            bot.reply_to(message, "😶 У жертвы пусто. Тут нечего брать.")
            return

        # --- НОВАЯ ЛОГИКА ОГРАБЛЕНИЯ ---
        # 1) Если жертва бедная (<= 50 000): можно грабить только 10% её баланса
        # 2) Если жертва богатая (> 50 000): лимит = твой баланс (анти-богач)
        if victim_balance <= 50_000:
            max_steal = max(1, int(victim_balance * 0.10))
            rule_text = "🧍‍♂️ Бедный режим: до <b>10%</b> (≤50 000)"
        else:
            max_steal = min(robber_balance, victim_balance)
            if max_steal < 1:
                bot.reply_to(message, "😅 У тебя нет денег, чтобы тягаться с богачами. Пополни баланс.")
                return
            rule_text = "💰 Анти-богач: до <b>твоего баланса</b>"

        amount = random.randint(1, max_steal)
        success = (random.random() < 0.5)

        robber_link = f"<a href='tg://user?id={robber_id}'>{safe_html(robber.get('first_name','Игрок'))}</a>"
        victim_link = f"<a href='tg://user?id={victim_id}'>{safe_html(victim.get('first_name','Игрок'))}</a>"

        # КД ставим в любом случае (успех/провал)
        cd_data[cd_key] = now
        save_robbery_cd(cd_data)

        if success:
            victim['balance'] = max(0, victim_balance - amount)
            robber['balance'] = robber_balance + amount
            save_data(data)

            # Статистика
            try:
                _robbery_add(chat_id, robber_id, amount)
            except Exception:
                pass

            bot.send_message(
                message.chat.id,
                f"🕵️ {robber.get('first_name', 'Игрок')} ограбил {victim.get('first_name', 'Игрок')} "
                f"на <b>{format_balance(amount)}</b> 🪙",
                parse_mode="HTML"
            )

        else:
            # --- ШТРАФ ЗА ПРОВАЛ: 1..1000 (но не больше баланса грабителя) ---
            fine = random.randint(1, 1000)
            fine = min(fine, int(robber.get('balance', 0)))

            if fine > 0:
                robber['balance'] = int(robber.get('balance', 0)) - fine
                save_data(data)

            bot.send_message(
                message.chat.id,
                f"🚨 {robber.get('first_name', 'Игрок')} не смог ограбить {victim.get('first_name', 'Игрок')}\n"
                f"💸 Штраф: <b>{format_balance(fine)}</b> 🪙",
                parse_mode="HTML"
            )


@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!топбанда')
def robbery_top_handler(message):
    # В ЛС — общий топ по всем чатам
    if message.chat.type == 'private':
        top = _robbery_top_global(10)
        if not top:
            bot.reply_to(message, "🏚️ Пока никто никого не ограбил.")
            return

        data = load_data()
        lines = []
        for i, (uid, cnt, stolen) in enumerate(top, 1):
            u = get_user(data, uid)
            name = safe_html(u.get('first_name', 'Игрок'))
            lines.append(f"{i}) <a href='tg://user?id={uid}'>{name}</a> — 🧾 {cnt} | 💰 {format_balance(stolen)} 🪙")

        bot.send_message(message.chat.id, "🏴‍☠️ <b>ТОП БАНДА (все чаты)</b>\n━━━━━━━━━━━━━━\n" + "\n".join(lines), parse_mode="HTML")
        return

    # В чате — топ только этого чата
    chat_id = str(message.chat.id)
    top = _robbery_top_chat(chat_id, 10)
    if not top:
        bot.reply_to(message, "🏚️ В этом чате пока никто никого не ограбил.")
        return

    data = load_data()
    lines = []
    for i, (uid, cnt, stolen) in enumerate(top, 1):
        u = get_user(data, uid)
        name = safe_html(u.get('first_name', 'Игрок'))
        lines.append(f"{i}) <a href='tg://user?id={uid}'>{name}</a> — 🧾 {cnt} | 💰 {format_balance(stolen)} 🪙")

    bot.send_message(message.chat.id, "🏴‍☠️ <b>ТОП БАНДА (этот чат)</b>\n━━━━━━━━━━━━━━\n" + "\n".join(lines), parse_mode="HTML")


# =========================
#      МУТ / РАЗМУТ
# =========================
def _is_chat_admin(chat_id: int, user_id: int) -> bool:
    try:
        cm = bot.get_chat_member(chat_id, user_id)
        return cm.status in ('administrator', 'creator')
    except Exception:
        return False

def _parse_mute_seconds(s: str) -> int:
    s = (s or '').strip().lower()
    if not s:
        return 60  # по умолчанию 1 минута

    # поддержка: 20с, 5м, 1ч, 1д, а также "1m"/"1h"/"1d"
    m = re.match(r'^(\d+)([smhdсчмд])$', s)
    if m:
        val = int(m.group(1))
        unit = m.group(2)
        if unit in ('s', 'с'):
            return val
        if unit in ('m', 'м'):
            return val * 60
        if unit in ('h', 'ч'):
            return val * 3600
        if unit in ('d', 'д'):
            return val * 86400

    # если просто число — считаем минутами
    if s.isdigit():
        return int(s) * 60

    return 0

@bot.message_handler(func=lambda m: m.text and (m.text.strip().lower().startswith('!мут') or m.text.strip().lower().startswith('!!мут')))
def mute_handler(message):
    if message.chat.type != 'supergroup':
        bot.reply_to(message, "❌ Мут доступен только в <b>супергруппах</b>.", parse_mode="HTML")
        return

    if not _is_chat_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "⛔ Только админы чата могут мутить.")
        return

    if not message.reply_to_message:
        bot.reply_to(message, "❌ Нужно ответить на сообщение игрока: <code>!мут 10м</code>", parse_mode="HTML")
        return

    target_id = message.reply_to_message.from_user.id

    # Нельзя мутить администраторов
    try:
        if _is_chat_admin(message.chat.id, target_id):
            target_link = f"<a href='tg://user?id={target_id}'>{safe_html(message.reply_to_message.from_user.first_name)}</a>"
            bot.reply_to(message, f"⚠️ Нельзя замутить администратора: {target_link}", parse_mode="HTML")
            return
    except Exception:
        pass


    try:
        if getattr(message.reply_to_message.from_user, 'is_bot', False) or (BOT_ID is not None and int(target_id) == int(BOT_ID)):
            bot.reply_to(message, "🤖 Бота мутить нельзя 🙂")
            return
    except Exception:
        pass

    parts = message.text.strip().split(maxsplit=1)
    if message.text.strip().lower().startswith('!!мут') and len(parts) == 1:
        seconds = 60
    else:
        seconds = _parse_mute_seconds(parts[1] if len(parts) > 1 else '')
    if seconds <= 0:
        bot.reply_to(message, "❌ Неверное время. Пример: <code>!мут 20с</code>, <code>!мут 5м</code>, <code>!мут 1ч</code>, <code>!мут 1д</code>", parse_mode="HTML")
        return

    until_date = int(time.time()) + seconds

    try:
        perms = types.ChatPermissions(can_send_messages=False, can_send_media_messages=False, can_send_polls=False,
                                      can_send_other_messages=False, can_add_web_page_previews=False,
                                      can_change_info=False, can_invite_users=False, can_pin_messages=False)
        bot.restrict_chat_member(message.chat.id, target_id, permissions=perms, until_date=until_date)
        target_link = f"<a href='tg://user?id={target_id}'>{safe_html(message.reply_to_message.from_user.first_name)}</a>"
        bot.send_message(message.chat.id, f"🔇 Замучен: {target_link}\n⏳ Время: <b>{seconds}</b> сек.", parse_mode="HTML")
    except Exception as e:
        msg = str(e)
        if 'user is an administrator of the chat' in msg or 'USER_IS_ADMIN' in msg:
            bot.reply_to(message, "⚠️ Нельзя замутить администратора.")
        else:
            bot.reply_to(message, f"❌ Не удалось замутить: {e}")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower().startswith('!размут'))
def unmute_handler(message):
    if message.chat.type != 'supergroup':
        bot.reply_to(message, "❌ Размут доступен только в <b>супергруппах</b>.", parse_mode="HTML")
        return

    if not _is_chat_admin(message.chat.id, message.from_user.id):
        bot.reply_to(message, "⛔ Только админы чата могут размучивать.")
        return

    if not message.reply_to_message:
        bot.reply_to(message, "❌ Нужно ответить на сообщение игрока: <code>!размут</code>", parse_mode="HTML")
        return

    target_id = message.reply_to_message.from_user.id
    try:
        perms = types.ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_polls=True,
                                      can_send_other_messages=True, can_add_web_page_previews=True,
                                      can_change_info=False, can_invite_users=True, can_pin_messages=False)
        bot.restrict_chat_member(message.chat.id, target_id, permissions=perms, until_date=0)
        target_link = f"<a href='tg://user?id={target_id}'>{safe_html(message.reply_to_message.from_user.first_name)}</a>"
        bot.send_message(message.chat.id, f"🔊 Размучен: {target_link}", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"❌ Не удалось размутить: {e}")



@bot.message_handler(func=lambda m: False,
                     content_types=['text','photo','document','video','animation','voice','audio','sticker'])
def _handle_broadcast_content(message):
    # Игнорируем старые апдейты (когда бот был оффлайн)
    try:
        if hasattr(message, 'date') and int(getattr(message, 'date', 0) or 0) < int(BOT_START_UNIX):
            return
    except Exception:
        pass

    admin_id = str(message.from_user.id)
    st = _BROADCAST_STATE.get(admin_id) or {}
    mode = st.get('mode')

    if message.from_user.id not in ADMIN_IDS:
        _BROADCAST_STATE.pop(admin_id, None)
        return

    if mode is None:
        bot.send_message(message.chat.id, "ℹ️ Сначала выбери тип рассылки в админ-панели.")
        return

    # Готовим payload
    sent_ok = 0
    sent_fail = 0

    bot.send_message(message.chat.id, "⏳ Начинаю рассылку...")

    for uid in _safe_iter_user_ids_for_broadcast():
        try:
            if mode == 'text':
                if not message.text:
                    continue
                bot.send_message(uid, message.text)
            elif mode == 'photo':
                if not message.photo:
                    continue
                file_id = message.photo[-1].file_id
                caption = message.caption or ""
                bot.send_photo(uid, file_id, caption=caption)
            else:  # forward
                bot.forward_message(uid, message.chat.id, message.message_id)

            sent_ok += 1
        except Exception as e:
            sent_fail += 1
            # просто пропускаем заблокировавших/удаливших
            if _is_blocked_error(e):
                continue
            # другие ошибки тоже не должны ломать бота
            continue

    _BROADCAST_STATE.pop(admin_id, None)
    bot.send_message(message.chat.id, f"✅ Рассылка завершена. Успешно: {sent_ok}, ошибок: {sent_fail}")

# =========================
# 💾 CENTRALIZED PERSISTENCE MANAGER
# Бэкап + авто-восстановление всех JSON баз при старте и периодически
# =========================

_ALL_DB_FILES = [
    'casino_users.json',
    'bank_access.json',
    'bonus_claims.json',
    'chat_participants_cache.json',
    'divorce_pending.json',
    'giveaways_by_chat.json',
    'marriages.json',
    'message_stats_24h.json',
    'results_log.json',
    'robbery_access.json',
    'robbery_cooldowns.json',
    'robbery_stats.json',
    'topday_profit.json',
    'username_map.json',
    'users_history.json',
]

_PERSIST_LOCK = threading.Lock()


def _safe_json_load(path: str):
    """Загружает JSON файл безопасно. Возвращает данные или None если пусто/битый."""
    try:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _safe_json_backup(path: str):
    """Создаёт .bak копию файла если он существует и не пуст."""
    try:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            data = _safe_json_load(path)
            if data is not None:
                bak = path + '.bak'
                tmp = bak + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                shutil.move(tmp, bak)
                return True
    except Exception:
        pass
    return False


def _restore_from_backup(path: str) -> bool:
    """Пытается восстановить файл из .bak если основной пуст/битый."""
    bak = path + '.bak'
    data = _safe_json_load(bak)
    if data is None:
        return False
    try:
        tmp = path + '.restore.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        shutil.move(tmp, path)
        print(f"✅ Восстановлено из бэкапа: {path}")
        return True
    except Exception as e:
        print(f"⚠️ Не удалось восстановить {path}: {e}")
        return False


def _init_persistence():
    """Запускается при старте бота. Проверяет все базы, восстанавливает из .bak если нужно."""
    print("🗄️ Проверка баз данных...")
    for db_file in _ALL_DB_FILES:
        main_ok = _safe_json_load(db_file) is not None
        bak_exists = os.path.exists(db_file + '.bak')

        if not main_ok and bak_exists:
            _restore_from_backup(db_file)
        elif main_ok:
            # Файл в порядке — сразу делаем свежий бэкап
            _safe_json_backup(db_file)
    print("✅ Базы данных готовы к работе.")


def _autobak_worker():
    """Фоновый поток: каждые 5 минут делает бэкап всех JSON баз."""
    while True:
        time.sleep(300)
        try:
            with _PERSIST_LOCK:
                backed = 0
                for db_file in _ALL_DB_FILES:
                    if _safe_json_backup(db_file):
                        backed += 1
                if backed:
                    print(f"💾 Автобэкап: {backed}/{len(_ALL_DB_FILES)} файлов сохранено.")
        except Exception as e:
            print(f"⚠️ Ошибка автобэкапа: {e}")


if __name__ == '__main__':
    keep_alive()
    _init_persistence()
    _bak_thread = threading.Thread(target=_autobak_worker, daemon=True, name="autobak")
    _bak_thread.start()
    while True:
        try:
            print("Бот запущен...")
            bot.polling(none_stop=True, timeout=60, long_polling_timeout=60)
        except Exception as e:
            print(f"Ошибка связи: {e}. Перезапуск через 5 секунд...")
            time.sleep(5)
