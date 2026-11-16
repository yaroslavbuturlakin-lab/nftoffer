import asyncio
import sqlite3
import logging
from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, 
    InlineQueryResultArticle, InputTextMessageContent, Message, TelegramObject
)
from aiogram.types.inline_query import InlineQuery
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ==========================================
# НАСТРОЙКА БАЗЫ ДАННЫХ (SQLite)
# ==========================================
conn = sqlite3.connect('bot_database.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS banned (user_id INTEGER PRIMARY KEY)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT)''')
conn.commit()

MAIN_ADMIN_ID = 8648153709  # <-- ВАШ ID
cursor.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (MAIN_ADMIN_ID,))
conn.commit()

def is_admin(user_id: int) -> bool:
    cursor.execute('SELECT 1 FROM admins WHERE user_id = ?', (user_id,))
    return cursor.fetchone() is not None

def is_banned(user_id: int) -> bool:
    cursor.execute('SELECT 1 FROM banned WHERE user_id = ?', (user_id,))
    return cursor.fetchone() is not None

# ==========================================
# БОТ И ДИСПЕТЧЕР
# ==========================================
TOKEN = "9036357642:AAH_Lgeh6txPI896xxnIpRPN8SbCiO7RxE8" # <-- ВАШ ТОКЕН
bot = Bot(token=TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# ==========================================
# MIDDLEWARE (Блокировка забаненных)
# ==========================================
class BanMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and is_banned(user.id):
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

active_offers = {}

# ==========================================
# УВЕДОМЛЕНИЯ АДМИНАМ
# ==========================================
async def notify_admin(text: str):
    cursor.execute('SELECT user_id FROM admins')
    admin_ids = [row[0] for row in cursor.fetchall()]
    for admin_id in admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except:
            pass

# ==========================================
# ОБЫЧНЫЕ КОМАНДЫ
# ==========================================
@router.message(Command("start", "offer"))
async def cmd_start(message: Message):
    # Сохраняем в базу
    cursor.execute('INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)', 
                   (message.from_user.id, message.from_user.username, message.from_user.first_name))
    conn.commit()
    
    username = f"@{message.from_user.username}" if message.from_user.username else "без юзернейма"
    await notify_admin(f"👤 Запуск бота!\nИмя: {message.from_user.first_name}\nЮзернейм: {username}\nID: {message.from_user.id}")
    
    await message.answer(
        "🤖 Бот активен!\n\n"
        "Напиши в любом чате:\n"
        "@nftoffer333_bot ссылка | цена"
    )

# ==========================================
# ИНЛАЙН РЕЖИМ
# ==========================================
@router.inline_query()
async def inline_handler(inline_query: InlineQuery):
    query = inline_query.query.strip()
    username = f"@{inline_query.from_user.username}" if inline_query.from_user.username else "без юзернейма"
    
    # Сохраняем в базу всех, кто пишет в инлайне
    cursor.execute('INSERT OR IGNORE INTO users (user_id, username, first_name) VALUES (?, ?, ?)', 
                   (inline_query.from_user.id, inline_query.from_user.username, inline_query.from_user.first_name))
    conn.commit()
    
    if not query:
        return
        
    await notify_admin(f"🔍 Инлайн!\nИмя: {inline_query.from_user.first_name}\nЮзернейм: {username}\nID: {inline_query.from_user.id}\nТекст: {query}")
    
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
    active_offers[offer_id] = {"link": link, "price": price, "gift": gift_name}

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Отклонить", callback_data=f"dec_{offer_id}"),
                InlineKeyboardButton(text="Принять", callback_data=f"acc_{offer_id}"),
            ]
        ]
    )

    card_text = (
        f"🎁 {gift_name} • {price} ⭐️ Stars\n"
        f"🔗 {link}\n\n"
        f"Пользователь предлагает вам сделку."
    )

    result = InlineQueryResultArticle(
        id=offer_id,
        title=f"{gift_name} • {price} ⭐️ Stars",
        description="Нажми чтобы отправить оффер",
        input_message_content=InputTextMessageContent(message_text=card_text),
        reply_markup=keyboard
    )

    try:
        await inline_query.answer([result], cache_time=1)
    except Exception as e:
        print(f">>> ОШИБКА ИНЛАЙНА: {e}")

# ==========================================
# КНОПКИ ОФФЕРОВ
# ==========================================
@router.callback_query(F.data.startswith("acc_"))
async def accept_callback(callback: CallbackQuery):
    offer_id = callback.data.split("_")[1]
    offer = active_offers.get(offer_id, {"link": "t.me/nft/LunarSnake-120435", "price": "600", "gift": "Gift"})
    
    accepted_text = (
        f"✅ Оффер принят!\n"
        f"🎁 {offer['gift']} • {offer['price']} ⭐️ Stars\n"
        f"🔗 {offer['link']}\n\n"
        f"• Передайте NFT на @NFTbogNFT"
    )
    new_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Передать NFT", url="https://t.me/NFTbogNFT")],
            [InlineKeyboardButton(text="✅ Подтвердить оффер", callback_data=f"conf_{offer_id}")]
        ]
    )
    await callback.answer()
    try:
        if callback.inline_message_id:
            await bot.edit_message_text(inline_message_id=callback.inline_message_id, text=accepted_text, reply_markup=new_keyboard)
        elif callback.message:
            await callback.message.edit_text(text=accepted_text, reply_markup=new_keyboard)
    except Exception as e:
        print(f"Ошибка (Accept): {e}")

@router.callback_query(F.data.startswith("conf_"))
async def confirm_callback(callback: CallbackQuery):
    offer_id = callback.data.split("_")[1]
    offer = active_offers.get(offer_id, {"price": "600"})
    await callback.answer(f"GiftRedo\n\nОффер принят!\n\nПередайте NFT на @NFTbogNFT\n\nСумма предложения: {offer['price']} Stars.", show_alert=True)

@router.callback_query(F.data.startswith("dec_"))
async def decline_callback(callback: CallbackQuery):
    offer_id = callback.data.split("_")[1]
    declined_text = "❌ Оффер отклонен."
    await callback.answer("❌ Оффер отклонен.", show_alert=True)
    try:
        if callback.inline_message_id:
            await bot.edit_message_text(inline_message_id=callback.inline_message_id, text=declined_text, reply_markup=None)
        elif callback.message:
            await callback.message.edit_text(text=declined_text, reply_markup=None)
    except Exception as e:
        print(f"Ошибка (Decline): {e}")

# ==========================================
# АДМИН-ПАНЕЛЬ
# ==========================================
@router.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id): return
        
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить админа", callback_data="admin_add")
    builder.button(text="❌ Убрать админа", callback_data="admin_remove")
    builder.button(text="📋 Список админов", callback_data="admin_list")
    builder.button(text="🚫 Забанить", callback_data="admin_ban")
    builder.button(text="✅ Разбанить", callback_data="admin_unban")
    builder.button(text="📢 Сделать рассылку", callback_data="admin_broadcast")
    builder.adjust(1)
    await message.answer("🛠 **Админ-панель**\nВыберите действие:", reply_markup=builder.as_markup(), parse_mode="Markdown")

@router.callback_query(F.data == "admin_add")
async def add_admin_btn(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await callback.message.answer("Отправьте ID пользователя, которого нужно сделать админом:")
    await state.set_state(AdminStates.waiting_for_admin_id)
    await callback.answer()

@router.callback_query(F.data == "admin_remove")
async def remove_admin_btn(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await callback.message.answer("Отправьте ID пользователя, которого нужно УБРАТЬ из админов:")
    await state.set_state(AdminStates.waiting_for_remove_admin_id)
    await callback.answer()

@router.callback_query(F.data == "admin_ban")
async def ban_user_btn(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await callback.message.answer("Отправьте ID пользователя, которого нужно забанить:")
    await state.set_state(AdminStates.waiting_for_ban_id)
    await callback.answer()

@router.callback_query(F.data == "admin_unban")
async def unban_user_btn(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await callback.message.answer("Отправьте ID пользователя, которого нужно разбанить:")
    await state.set_state(AdminStates.waiting_for_unban_id)
    await callback.answer()

@router.callback_query(F.data == "admin_list")
async def list_admins_btn(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    cursor.execute('SELECT user_id FROM admins')
    admins = cursor.fetchall()
    if not admins:
        await callback.message.answer("Список админов пуст.")
        await callback.answer()
        return
    text = "👑 **Список текущих админов:**\n\n"
    for index, admin in enumerate(admins, start=1):
        admin_id = admin[0]
        try:
            chat = await bot.get_chat(admin_id)
            name = chat.first_name
            username = f"@{chat.username}" if chat.username else "без юзернейма"
            text += f"{index}. {name} ({username}) — `{admin_id}`\n"
        except:
            text += f"{index}. `{admin_id}` (нет данных)\n"
    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()

# ==========================================
# ОБРАБОТКА ВВОДА ID (FSM)
# ==========================================
@router.message(AdminStates.waiting_for_admin_id)
async def process_add_admin(message: Message, state: FSMContext):
    try:
        new_admin_id = int(message.text)
        cursor.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (new_admin_id,))
        conn.commit()
        await message.answer(f"✅ Пользователь {new_admin_id} теперь админ!")
    except ValueError:
        await message.answer("❌ Ошибка! ID должен состоять только из цифр.")
    await state.clear()

@router.message(AdminStates.waiting_for_remove_admin_id)
async def process_remove_admin(message: Message, state: FSMContext):
    try:
        remove_id = int(message.text)
        if remove_id == message.from_user.id:
            await message.answer("❌ Вы не можете убрать самого себя из админов!")
        else:
            cursor.execute('DELETE FROM admins WHERE user_id = ?', (remove_id,))
            conn.commit()
            await message.answer(f"✅ Пользователь {remove_id} больше не админ.")
    except ValueError:
        await message.answer("❌ Ошибка! ID должен состоять только из цифр.")
    await state.clear()

@router.message(AdminStates.waiting_for_ban_id)
async def process_ban_user(message: Message, state: FSMContext):
    try:
        ban_id = int(message.text)
        cursor.execute('INSERT OR IGNORE INTO banned (user_id) VALUES (?)', (ban_id,))
        conn.commit()
        await message.answer(f"🚫 Пользователь {ban_id} забанен!")
    except ValueError:
        await message.answer("❌ Ошибка! ID должен состоять только из цифр.")
    await state.clear()

@router.message(AdminStates.waiting_for_unban_id)
async def process_unban_user(message: Message, state: FSMContext):
    try:
        unban_id = int(message.text)
        cursor.execute('DELETE FROM banned WHERE user_id = ?', (unban_id,))
        conn.commit()
        await message.answer(f"✅ Пользователь {unban_id} разбанен!")
    except ValueError:
        await message.answer("❌ Ошибка! ID должен состоять только из цифр.")
    await state.clear()

# ==========================================
# РАССЫЛКА
# ==========================================
@router.callback_query(F.data == "admin_broadcast")
async def broadcast_btn(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id): return
    await callback.message.answer("Отправьте текст для рассылки:")
    await state.set_state(AdminStates.waiting_for_broadcast)
    await callback.answer()

@router.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: Message, state: FSMContext):
    text_to_send = message.text
    await state.clear()
    cursor.execute('SELECT user_id FROM users')
    users = cursor.fetchall()
    if not users:
        await message.answer("❌ В базе пока нет пользователей.")
        return
    await message.answer(f"⏳ Рассылка для {len(users)} чел...")
    success = 0
    failed = 0
    for user in users:
        try:
            await bot.send_message(user[0], text_to_send)
            success += 1
        except:
            failed += 1
        await asyncio.sleep(0.05)
    await message.answer(f"✅ Готово!\n📬 Доставлено: {success}\n🚫 Не доставлено: {failed}")

# ==========================================
# КОМАНДА .DOX
# ==========================================
@router.message(F.text.startswith(".dox "))
async def dox_user(message: Message):
    if not is_admin(message.from_user.id): return
    target = message.text.split(maxsplit=1)[1].strip()
    try:
        if target.isdigit():
            chat = await bot.get_chat(int(target))
        else:
            chat = await bot.get_chat(f"@{target.replace('@', '')}")
        text = (
            f"🕵️ **Досье:**\n\n"
            f"👤 Имя: {chat.first_name or 'Нет'}\n"
            f"👥 Фамилия: {chat.last_name or 'Нет'}\n"
            f"🔗 Юзернейм: @{chat.username if chat.username else 'Нет'}\n"
            f"🆔 ID: `{chat.id}`\n"
            f"📞 Номер: {chat.phone_number or 'Скрыт'}\n"
        )
        await message.answer(text, parse_mode="Markdown")
    except Exception as e:
        await message.answer("❌ Ошибка! Пользователь не найден или скрыл данные.")

# ==========================================
# ЗАПУСК БОТА
# ==========================================


@router.message(F.text & ~F.text.startswith("/") & ~F.text.startswith("."))
async def any_message_handler(message: Message):
    username = f"@{message.from_user.username}" if message.from_user.username else "без юзернейма"
    
    await notify_admin(
        f"💬 Новое сообщение!\n"
        f"Имя: {message.from_user.first_name}\n"
        f"Юзернейм: {username}\n"
        f"ID: {message.from_user.id}\n"
        f"Текст: {message.text}"
    )

async def main():
    print("1. Бот запускается...")
    try:
        print("2. Пробую удалить вебхук...")
        await bot.delete_webhook(drop_pending_updates=True)
        print("3. Вебхук удален. Начинаю polling...")
        await dp.start_polling(bot)
    except Exception as e:
        print(f"ОШИБКА: {e}")

if __name__ == "__main__":
    asyncio.run(main())
