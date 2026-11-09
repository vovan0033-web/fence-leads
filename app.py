import os
import re
import threading
import time
import requests
from flask import Flask, request, jsonify

# --- Безопасный импорт Telethon ---
try:
    from telethon import TelegramClient, events
    from telethon.tl.functions.messages import SearchGlobalRequest
    from telethon.tl.types import InputMessagesFilterEmpty, InputPeerEmpty
    TELETHON_AVAILABLE = True
except ImportError:
    TELETHON_AVAILABLE = False

# ============================================================
# 1. НАСТРОЙКИ
# ============================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = os.environ.get("CHAT_ID", "")
API_ID    = os.environ.get("API_ID")
API_HASH  = os.environ.get("API_HASH")

# Список фраз из переменной окружения (для точного поиска)
KEYWORDS = os.environ.get("KEYWORDS", "нужен забор,нужен навес,нужны ворота,ищу сварщика,ищу бригаду,нужна сварка,сварочные работы,установка забора,монтаж забора,поставить забор,кто делает заборы,кто варит ворота,забор из профнастила,забор из профлиста,забор из евроштакетника,3д забор,откатные ворота,распашные ворота,навес на машину,навес над парковкой,Краснодар забор,Сочи забор,Майкоп забор,Адыгея забор,Анапа забор,Новороссийск забор").split(",")
KEYWORDS = [k.strip().lower() for k in KEYWORDS if k.strip()]

# Базовые слова для гибридного поиска (ловят сообщения, где нет целой фразы)
BASE_WORDS = ["забор", "ворота", "калитка", "навес", "сварка", "сварщик", "профлист", "профнастил", "евроштакетник", "3д", "монтаж"]

# ============================================================
# 2. ФУНКЦИИ
# ============================================================
def send_telegram_alert(source: str, contact: str, summary: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("[Ошибка] Не заданы BOT_TOKEN или CHAT_ID в Secrets!")
        return
    text = (
        f"🔥 <b>Новый горячий лид!</b>\n\n"
        f"<b>Источник:</b> {source}\n"
        f"<b>Контакт:</b> {contact}\n"
        f"<b>Суть запроса:</b> {summary}\n"
    )
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
        print(f"[Telegram] Уведомление отправлено: {summary[:30]}...")
    except Exception as e:
        print(f"[Ошибка отправки] {e}")

def contains_keyword(text: str) -> bool:
    """Гибридный поиск: ищет и целые фразы, и отдельные слова."""
    text_lower = text.lower()
    
    # 1. Сначала проверяем точные фразы (целиком)
    if any(kw in text_lower for kw in KEYWORDS):
        return True
        
    # 2. Если фраз нет, проверяем отдельные базовые слова
    for word in BASE_WORDS:
        # Ищем слово как отдельное (чтобы не ловить "заборный" в слове "заборный завод")
        if re.search(r'\b' + word + r'\b', text_lower):
            return True
            
    return False

# ============================================================
# 3. FLASK СЕРВЕР (Прием заявок с Tilda)
# ============================================================
app = Flask(__name__)

@app.route("/")
def index():
    return "Сервер работает! Ждем заявок."

@app.route("/tilda", methods=["POST"])
def tilda_webhook():
    if request.is_json:
        data = request.get_json()
    else:
        data = request.form.to_dict()

    # Ищем поля, как бы их ни назвала Tilda
    name = data.get("Name") or data.get("name") or data.get("Имя") or "Не указано"
    phone = data.get("Phone") or data.get("phone") or data.get("Телефон") or "Не указано"
    
    # Собираем весь остальной текст (расчет, комментарии)
    comment = data.get("Comment") or data.get("Textarea") or data.get("Сообщение")
    
    if not comment:
        comment = str({k: v for k, v in data.items() if k not in ['Name', 'name', 'Phone', 'phone']})

    summary = f"Клиент: {name}.\n{comment}"
    send_telegram_alert("Сайт Tilda", phone, summary)
    return jsonify({"status": "ok"}), 200

# ============================================================
# 4. ГЛОБАЛЬНЫЙ ПОИСК ЛИДОВ В TELEGRAM
# ============================================================
def start_telegram_monitor():
    if not (TELETHON_AVAILABLE and API_ID and API_HASH):
        print("⚠️ [Telethon] Нет API_ID/API_HASH. Поиск Telegram отключен.")
        return

    client = TelegramClient("search_session", int(API_ID), API_HASH)

    async def run_search():
        await client.start()
        print("✅ [Telethon] Глобальный поиск запущен!")
        
        seen_ids = set()

        while True:
            for query in KEYWORDS:
                try:
                    result = await client(SearchGlobalRequest(
                        q=query,
                        filter=InputMessagesFilterEmpty(),
                        min_date=None,
                        max_date=None,
                        offset_rate=0,
                        offset_peer=InputPeerEmpty(),
                        offset_id=0,
                        limit=10
                    ))
                    
                    for msg in result.messages:
                        if msg.id in seen_ids:
                            continue
                        
                        text = msg.message or ""
                        # Используем нашу гибридную функцию
                        if contains_keyword(text):
                            seen_ids.add(msg.id)
                            try:
                                chat = await client.get_entity(msg.peer_id)
                                chat_title = getattr(chat, 'title', 'Неизвестный чат')
                                username = getattr(chat, 'username', '')
                                contact = f"@{username}" if username else f"ID чата: {chat.id}"
                            except:
                                chat_title = "Неизвестный чат"
                                contact = "Не удалось получить контакт"

                            send_telegram_alert(
                                f"Telegram (Поиск): {chat_title}", 
                                contact, 
                                text[:250].replace("\n", " ")
                            )
                except Exception as e:
                    print(f"Ошибка поиска по слову '{query}': {e}")
                
                time.sleep(5)
            
            print("⏳ Цикл поиска завершен. Ждем 10 минут...")
            time.sleep(600)

    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_search())
    except Exception as e:
        print(f"❌ [Telethon] Ошибка запуска: {e}")

# ============================================================
# 5. ЗАПУСК
# ============================================================
if not app.debug:
    threading.Thread(target=start_telegram_monitor, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 Сервер запущен на порту {port}")
    app.run(host="0.0.0.0", port=port)
