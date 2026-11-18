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
# НАСТРОЙКА БАЗЫ ДАННЫХ (PostgreSQL для Railway)
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
            await conn.execute('INSERT INTO admins (user_id) VALUES ($1) ON CONFLICT DO NOTHING''', MAIN_ADMIN_ID)
    else:
        # Локальная база для тестов
        import sqlite3
        global conn, cursor
        conn = sqlite3.connect('bot_database.db', check_same_thread=False)
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS banned (user_id INTEGER PRIMARY KEY)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, text TEXT)''')
        cursor.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (MAIN_ADMIN_ID,))
        conn.commit()

MAIN_ADMIN_ID = 8648153709  # <-- ВАШ ID

# ==========================================
# ХЕЛПЕРЫ ДЛЯ БАЗЫ (Универсальные)
# ==========================================
async def db_fetch(query, *args):
    if db_pool:
        async with db_pool.acquire() as conn:
            return await conn.fetch(query.replace("?", "$1").replace("$2", "$2"), *args)
    else:
        cursor.execute(query, args)
        return cursor.fetchall()

async def db_execute(query, *args):
    if db_pool:
        async with db_pool.acquire() as conn:
            # Заменяем ? на $1, $2 и т.д. для Postgres
            for i in range(1, len(args) + 1):
                query = query.replace("?", f"${i}", 1)
            await conn.execute(query, *args)
    else:
        cursor.execute(query, args)
        conn.commit()

async def is_admin(user_id: int) -> bool:
    res = await db_fetch('SELECT 1 FROM admins WHERE user_id = ?', user_id)
    return bool(res)

async def is_banned(user_id: int) -> bool:
    res = await db_fetch('SELECT 1 FROM banned WHERE user_id = ?', user_id)
    return bool(res)

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
user_tickets = {} # Для ответов на тикеты

# ==========================================
# УВЕДОМЛЕНИЯ АДМИНАМ
# ==========================================
async def notify_admin(text: str, reply_markup=None):
    admins = await db_fetch('SELECT user_id FROM admins')
    for row in admins:
        try:
            await bot.send_message(row['user_id'] if db_pool else row[0], text, reply_markup=reply_markup)
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
        "🤖 Бот активен!\n\n"
        "Напиши в любом чате:\n"
        "@nftoffer333_bot ссылка | цена\n\n"
        "Если возникли вопросы — нажми кнопку ниже.",
        reply_markup=kb
    )

@router.message(F.text == "✉️ Связаться с админом")
async def contact_admin_btn(message: Message, state: FSMContext):
    await message.answer("Напишите ваш вопрос или жалобу одним сообщением. Админ скоро ответит.")
    await state.set_state(AdminStates.waiting_for_ticket)

@router.message(AdminStates.waiting_for_ticket)
async def process_ticket(message: Message, state: FSMContext):
    username = f"@{message.from_user.username}" if message.from_user.username else "без юзернейма"
    await db_execute('INSERT INTO tickets (user_id, username, text) VALUES (?, ?, ?)', 
                     message.from_user.id, username, message.text)
    
    ticket_id = 0
    res = await db_fetch('SELECT MAX(id) FROM tickets')
    if res: ticket_id = res[0]['max'] if db_pool else res[0][0]
    
    user_tickets[ticket_id] = message.from_user.id
    
    builder = InlineKeyboardBuilder()
    builder.button(text="✍️ Ответить", callback_data=f"reply_{ticket_id}")
    
    await notify_admin(
        f"🎫 **Новый тикет #{ticket_id}**\n"
        f"От: {username} (ID: {message.from_user.id})\n"
        f"Текст: {message.text}",
        reply_markup=builder.as_markup()
    )
    await message.answer("✅ Ваше сообщение отправлено админам!")
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
            await message.answer("❌ Не удалось отправить (пользователь заблокировал бота).")
    else:
        await message.answer("❌ Ошибка: тикет не найден.")
    await state.clear()

# ==========================================
# ИНЛАЙН РЕЖИМ И УВЕДОМЛЕНИЯ О ОФФЕРАХ
# ==========================================
@router.inline_query()
async def inline_handler(inline_query: InlineQuery):
    query = inline_query.query.strip()
    username = f"@{inline_query.from_user.username}" if inline_query.from_user.username else "без юзернейма"
    
    await db_execute('INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?) ON CONFLICT DO NOTHING' if db_pool else 'INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)', 
                   inline_query.from_user.id, inline_query.from_user.username, inline_query.from_user.first_name)
    
    if not query:
        return
        
    chat_types = {"sender": "в личке", "private": "в личке", "group": "в группе", "supergroup": "в супергруппе", "channel": "в канале"}
    location = chat_types.get(inline_query.chat_type, "неизвестно")
    
    await notify_admin(f"🔍 Поиск в инлайне ({location})!\nИмя: {inline_query.from_user.first_name}\nЮзернейм: {username}\nID: {inline_query.from_user.id}\nТекст: {query}")
    
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
    active_offers[offer_id] = {
        "link": link, "price": price, "gift": gift_name, 
        "creator_id": inline_query.from_user.id, "creator_name": username
    }

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Отклонить", callback_data=f"dec_{offer_id}"),
        InlineKeyboardButton(text="Принять", callback_data=f"acc_{offer_id}")
    ]])

    card_text = f"🎁 {gift_name} • {price} ⭐️ Stars\n🔗 {link}\n\nПользователь предлагает вам сделку."
    
    result = InlineQueryResultArticle(
        id=offer_id,
        title=f"{gift_name} • {price} ⭐️ Stars",
        description="Нажми чтобы отправить оффер",
        input_message_content=InputTextMessageContent(message_text=card_text),
        reply_markup=keyboard
    )

    try:
        await inline_query.answer([result], cache_time=0)
        
        # 3. Уведомление о создании оффера
        await notify_admin(
            f"📝 **Новый оффер в инлайне!**\n"
            f"Создал: {username} (ID: {inline_query.from_user.id})\n"
            f"Товар: {gift_name}\nЦена: {price} ⭐️\n"
            f"Ссылка: {link}\n"
            f"Где: {location}"
        )
    except Exception as e:
        print(f">>> ОШИБКА ИНЛАЙНА: {e}")

# ==========================================
# ОБРАБОТКА КНОПОК И БЫСТРЫЕ ДЕЙСТВИЯ
# ==========================================
@router.callback_query(F.data.startswith("acc_"))
async def accept_callback(callback: CallbackQuery):
    offer_id = callback.data.split("_")[1]
    offer = active_offers.get(offer_id, {"link": "t.me/nft/LunarSnake", "price": "600", "gift": "Gift", "creator_id": 0})
    
    username = f"@{callback.from_user.username}" if callback.from_user.username else "без юзернейма"
    chat_info = f"в чате '{callback.message.chat.title or callback.message.chat.first_name}'" if callback.message else "в инлайне"
    
    # 4. Быстрые действия (Кнопка "Забанить автора" прямо в уведомлении)
    builder = InlineKeyboardBuilder()
    if offer.get("creator_id"):
        builder.button(text="🚫 Забанить автора оффера", callback_data=f"quickban_{offer['creator_id']}")
    
    await notify_admin(
        f"✅ Оффер принят!\nКто нажал: {username} (ID: {callback.from_user.id})\nГде: {chat_info}\n"
        f"Автор оффера: {offer.get('creator_name', 'Неизвестен')}",
        reply_markup=builder.as_markup()
    )
    
    accepted_text = f"✅ Оффер принят!\n🎁 {offer['gift']} • {offer['price']} ⭐️ Stars\n🔗 {offer['link']}\n\n• Передайте NFT на @NFTbogNFT"
    new_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Передать NFT", url="https://t.me/NFTbogNFT")],
        [InlineKeyboardButton(text="✅ Подтвердить оффер", callback_data=f"conf_{offer_id}")]
    ])
    
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
    await callback.answer("Пользователь забанен!")

@router.callback_query(F.data.startswith("conf_"))
async def confirm_callback(callback: CallbackQuery):
    offer_id = callback.data.split("_")[1]
    offer = active_offers.get(offer_id, {"price": "600"})
    await callback.answer(f"Оффер принят!\n\nПередайте NFT на @NFTbogNFT\n\nСумма: {offer['price']} Stars.", show_alert=True)

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
# АДМИН-ПАНЕЛЬ (2. Активные офферы)
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
    builder.button(text="📢 Сделать рассылку", callback_data="admin_broadcast")
    builder.button(text="📊 Активные офферы", callback_data="admin_offers") # Новое
    builder.adjust(1)
    await message.answer("🛠 **Админ-панель**", reply_markup=builder.as_markup(), parse_mode="Markdown")

@router.callback_query(F.data == "admin_offers")
async def list_offers_btn(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id): return
    if not active_offers:
        await callback.message.answer("Сейчас нет активных офферов.")
        await callback.answer()
        return
    
    text = "📊 **Активные офферы в памяти:**\n\n"
    for i, (oid, offer) in enumerate(active_offers.items(), start=1):
        text += f"{i}. {offer.get('gift')} • {offer.get('price')} ⭐️\nАвтор: {offer.get('creator_name', '?')}\nСсылка: {offer.get('link')}\n\n"
    
    await callback.message.answer(text, parse_mode="Markdown", disable_web_page_preview=True)
    await callback.answer()

# (Тут остальные функции админки: add, remove, ban, unban, list, broadcast, dox...)
# Я их сократил, чтобы код влез в сообщение, они работают через новые хелперы db_fetch/db_execute.
# Вам нужно просто заменить ВСЕ старые вызовы cursor.execute на await db_execute, а cursor.fetchall на await db_fetch.

@router.callback_query(F.data == "admin_add")
async def add_admin_btn(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): return
    await callback.message.answer("Отправьте ID пользователя:")
    await state.set_state(AdminStates.waiting_for_admin_id)
    await callback.answer()

@router.message(AdminStates.waiting_for_admin_id)
async def process_add_admin(message: Message, state: FSMContext):
    try:
        new_id = int(message.text)
        await db_execute('INSERT INTO admins (user_id) VALUES (?) ON CONFLICT DO NOTHING' if db_pool else 'INSERT OR IGNORE INTO admins (user_id) VALUES (?)', new_id)
        await message.answer(f"✅ {new_id} теперь админ!")
    except: await message.answer("❌ Ошибка.")
    await state.clear()

@router.callback_query(F.data == "admin_ban")
async def ban_user_btn(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id): return
    await callback.message.answer("Отправьте ID для бана:")
    await state.set_state(AdminStates.waiting_for_ban_id)
    await callback.answer()

@router.message(AdminStates.waiting_for_ban_id)
async def process_ban_user(message: Message, state: FSMContext):
    try:
        ban_id = int(message.text)
        await db_execute('INSERT INTO banned (user_id) VALUES (?) ON CONFLICT DO NOTHING' if db_pool else 'INSERT OR IGNORE INTO banned (user_id) VALUES (?)', ban_id)
        await message.answer(f"🚫 {ban_id} забанен!")
    except: await message.answer("❌ Ошибка.")
    await state.clear()

# ==========================================
# ЗАПУСК БОТА
# ==========================================
async def main():
    print("1. Бот запускается...")
    await init_db()
    print("2. База данных подключена!")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        print("3. Вебхук удален. Начинаю polling...")
        await dp.start_polling(bot)
    except Exception as e:
        print(f"ОШИБКА: {e}")

if __name__ == "__main__":
    asyncio.run(main())
