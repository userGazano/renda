import asyncio
from decimal import Decimal
from pathlib import Path
import logging
import re

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.types import SuccessfulPayment

import db
from config import BOT_TOKEN, ADMIN_IDS, SHOP_NAME, CURRENCY
from telethon_manager import (
    connect_session, 
    session_path, 
    start_listening, 
    get_code_for_account, 
    wait_for_code, 
    is_authorized,
    disconnect_session,
    close_all,
    request_code,
    verify_code,
    verify_2fa
)

logger = logging.getLogger(__name__)
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

class Form(StatesGroup):
    add_phone = State()
    add_code = State()
    add_2fa = State()
    add_name = State()
    add_price = State()
    add_description = State()
    add_country = State()
    deposit_amount = State()
    balance_user = State()
    balance_amount = State()
    block_user = State()
    edit_account_name = State()
    edit_account_price = State()
    edit_account_description = State()
    edit_account_country = State()
    edit_country_name = State()
    delete_account_confirm = State()

def admin(uid): return uid in ADMIN_IDS

def format_phone(phone: str) -> str:
    phone = re.sub(r'[^0-9+]', '', phone)
    if not phone.startswith('+'):
        phone = '+' + phone
    return phone

def main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Купить", callback_data="countries")],
        [InlineKeyboardButton(text="💰 Баланс", callback_data="balance"),
         InlineKeyboardButton(text="⭐ Пополнить", callback_data="deposit")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="🧾 Мои покупки", callback_data="purchases")],
        [InlineKeyboardButton(text="🆘 Поддержка", callback_data="support")]
    ])

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌍 Управление странами", callback_data="a_country_menu")],
        [InlineKeyboardButton(text="➕ Добавить аккаунт", callback_data="a_account")],
        [InlineKeyboardButton(text="📋 Управление аккаунтами", callback_data="a_accounts_list")],
        [InlineKeyboardButton(text="💰 Выдать баланс", callback_data="a_balance")],
        [InlineKeyboardButton(text="🚫 Заблокировать", callback_data="a_block")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="a_stats")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
    ])

@dp.message(CommandStart())
async def start(m: Message):
    u = await db.ensure_user(m.from_user.id, m.from_user.username)
    if u["blocked"]:
        return await m.answer("🚫 Ваш аккаунт заблокирован.")
    
    if admin(m.from_user.id):
        await m.answer(
            f"🛍 <b>{SHOP_NAME}</b>\n\n👑 Админ-панель",
            reply_markup=admin_kb(),
            parse_mode="HTML"
        )
    else:
        await m.answer(
            f"🛍 <b>{SHOP_NAME}</b>\n\n⭐ Баланс: {int(u['balance'])} звезд\n\nГлавное меню:",
            reply_markup=main_kb(),
            parse_mode="HTML"
        )

@dp.message(Command("admin"))
async def admin_cmd(m: Message):
    if admin(m.from_user.id):
        await m.answer("⚙️ <b>Админ-панель</b>", reply_markup=admin_kb(), parse_mode="HTML")
    else:
        await m.answer("❌ У вас нет доступа")

# ============== СТРАНЫ ДЛЯ ПОКУПАТЕЛЯ ==============

@dp.callback_query(F.data == "countries")
async def countries(c: CallbackQuery):
    rows = await db.countries()
    kb = []
    for r in rows:
        kb.append([InlineKeyboardButton(text=f"{r['emoji']} {r['name']}", callback_data=f"country:{r['id']}")])
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="home")])
    await c.message.edit_text("🌍 <b>Выберите страну:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data.startswith("country:"))
async def country_accounts(c: CallbackQuery):
    country_id = int(c.data.split(":")[1])
    rows = await db.get_country_accounts(country_id)
    
    if not rows:
        kb = [[InlineKeyboardButton(text="⬅️ Назад", callback_data="countries")]]
        await c.message.edit_text("📭 <b>В этой стране пока нет аккаунтов</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
        await c.answer()
        return
    
    kb = []
    for a in rows:
        kb.append([InlineKeyboardButton(text=f"📱 {a['name']} — {int(a['price'])} {CURRENCY}", callback_data=f"account:{a['id']}")])
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="countries")])
    
    await c.message.edit_text("📱 <b>Аккаунты в выбранной стране:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    await c.answer()

# ============== АККАУНТЫ ДЛЯ ПОКУПАТЕЛЯ ==============

@dp.callback_query(F.data.startswith("account:"))
async def account_view(c: CallbackQuery):
    aid = int(c.data.split(":")[1])
    a = await db.account(aid)
    if not a or a["status"] != "available":
        return await c.answer("Аккаунт уже продан.", show_alert=True)
    
    text = f"📱 <b>{a['name']}</b>\n\n{a['description']}\n\n💵 Цена: <b>{int(a['price'])} {CURRENCY}</b>"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Купить", callback_data=f"buy:{aid}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="countries")]
    ])
    await c.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data.startswith("buy:"))
async def buy(c: CallbackQuery):
    aid = int(c.data.split(":")[1])
    
    account_data = await db.account(aid)
    if not account_data or account_data["status"] != "available":
        return await c.answer("Аккаунт недоступен.", show_alert=True)
    
    user = await db.get_user(c.from_user.id)
    if user["balance"] < account_data["price"]:
        stars_needed = int(account_data["price"] - user["balance"])
        return await c.answer(f"❌ Недостаточно звезд!\n\nНужно: {int(account_data['price'])} ⭐\nУ тебя: {int(user['balance'])} ⭐\nНе хватает: {stars_needed} ⭐\n\nПополни баланс в главном меню!", show_alert=True)
    
    purchase, status = await db.buy_account(c.from_user.id, aid)
    
    if status == "balance":
        return await c.answer("Недостаточно средств.", show_alert=True)
    if status != "ok":
        return await c.answer("Аккаунт недоступен.", show_alert=True)
    
    a = await db.account(aid)
    
    try:
        await start_listening(a['phone'])
    except Exception as e:
        logger.error(f"Ошибка запуска прослушки: {e}")
    
    text = f"✅ <b>Покупка #{purchase}</b>\n\n📱 Аккаунт: {a['name']}\n📞 Номер: <code>{a['phone']}</code>\n⭐ Цена: {int(a['price'])} звезд\n\n📝 {a['description']}\n\n🔐 <b>Чтобы получить код подтверждения:</b>\n1. Открой Telegram на телефоне\n2. Зайди в этот аккаунт\n3. Код придет сюда автоматически"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📩 Получить код", callback_data=f"get_code:{a['phone']}")],
        [InlineKeyboardButton(text="🧾 Мои покупки", callback_data="purchases")]
    ])
    
    await c.message.answer(text, reply_markup=kb, parse_mode="HTML")
    
    new_balance = await get_user_balance(c.from_user.id)
    await c.answer(f"✅ Покупка успешна! Остаток: {int(new_balance)} ⭐", show_alert=True)

# ============== ПОЛУЧЕНИЕ КОДА ==============

@dp.callback_query(F.data.startswith("get_code:"))
async def get_code(c: CallbackQuery):
    phone = c.data.split(":", 1)[1]
    user_id = c.from_user.id
    
    purchases = await db.my_purchases(user_id)
    user_phone = None
    for p in purchases:
        if p['phone'] == phone:
            user_phone = phone
            break
    
    if not user_phone:
        return await c.answer("❌ У вас нет доступа к этому аккаунту", show_alert=True)
    
    wait_msg = await c.message.answer("⏳ Ожидание кода подтверждения...")
    
    code = await wait_for_code(phone, timeout=120)
    
    if code:
        await wait_msg.edit_text(f"✅ <b>Код подтверждения:</b>\n\n<code>{code}</code>\n\n⏱️ Действует 10 минут", parse_mode="HTML")
        await c.answer("✅ Код получен!", show_alert=True)
    else:
        await wait_msg.edit_text("⏰ <b>Код не получен</b>\n\nВозможные причины:\n• Код не был отправлен в Telegram\n• Аккаунт не авторизован\n• Истекло время ожидания (2 минуты)\n\nПопробуй еще раз через 'Мои покупки'", parse_mode="HTML")
        await c.answer("⏰ Время ожидания истекло", show_alert=True)

# ============== ЛИЧНЫЙ КАБИНЕТ ==============

@dp.callback_query(F.data == "balance")
async def balance(c: CallbackQuery):
    u = await db.get_user(c.from_user.id)
    await c.message.edit_text(f"⭐ <b>Баланс</b>\n\nЗвезд: <b>{int(u['balance'])}</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Пополнить (от 3⭐)", callback_data="deposit")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
    ]), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data == "profile")
async def profile(c: CallbackQuery):
    u = await db.get_user(c.from_user.id)
    await c.message.edit_text(f"👤 <b>Профиль</b>\n\n🆔 ID: <code>{u['telegram_id']}</code>\n👤 Имя: @{u['username'] or 'Не указано'}\n⭐ Баланс: <b>{int(u['balance'])}</b> звезд\n📅 Зарегистрирован: {u['created_at'].strftime('%d.%m.%Y')}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
    ]), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data == "purchases")
async def purchases(c: CallbackQuery):
    rows = await db.my_purchases(c.from_user.id)
    
    if not rows:
        text = "🧾 У тебя пока нет покупок."
        await c.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
        ]), parse_mode="HTML")
        await c.answer()
        return
    
    text = "🧾 <b>Мои покупки:</b>\n\n"
    kb = []
    
    for r in rows:
        text += f"#{r['id']} — {r['name']}\n📞 {r['phone']}\n⭐ {int(r['amount'])} звезд\n📅 {r['created_at'].strftime('%d.%m.%Y %H:%M')}\n\n"
        kb.append([InlineKeyboardButton(text=f"📩 Получить код для {r['name']}", callback_data=f"get_code:{r['phone']}")])
    
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="home")])
    
    await c.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data == "support")
async def support(c: CallbackQuery):
    await c.message.edit_text("🆘 <b>Поддержка</b>\n\nПо всем вопросам обращайтесь к администратору.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
    ]), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data == "home")
async def home(c: CallbackQuery):
    u = await db.get_user(c.from_user.id)
    
    if admin(c.from_user.id):
        await c.message.edit_text(f"🛍 <b>{SHOP_NAME}</b>\n\n👑 Админ-панель", reply_markup=admin_kb(), parse_mode="HTML")
    else:
        await c.message.edit_text(f"🛍 <b>{SHOP_NAME}</b>\n\n⭐ Баланс: {int(u['balance'])} звезд\n\nГлавное меню:", reply_markup=main_kb(), parse_mode="HTML")
    await c.answer()

# ============== ПОПОЛНЕНИЕ (только звезды, от 3) ==============

@dp.callback_query(F.data == "deposit")
async def deposit(c: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ 3 звезды", callback_data="deposit:3"),
         InlineKeyboardButton(text="⭐ 5 звезд", callback_data="deposit:5")],
        [InlineKeyboardButton(text="⭐ 10 звезд", callback_data="deposit:10"),
         InlineKeyboardButton(text="⭐ 25 звезд", callback_data="deposit:25")],
        [InlineKeyboardButton(text="⭐ 50 звезд", callback_data="deposit:50"),
         InlineKeyboardButton(text="⭐ 100 звезд", callback_data="deposit:100")],
        [InlineKeyboardButton(text="⭐ Другая сумма", callback_data="deposit_custom")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
    ])
    
    await c.message.edit_text("⭐ <b>Пополнение баланса</b>\n\nВыберите количество звезд для покупки:\n\nМинимальная сумма: 3 звезды", reply_markup=kb, parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data == "deposit_custom")
async def deposit_custom(c: CallbackQuery, state: FSMContext):
    await state.set_state(Form.deposit_amount)
    await c.message.edit_text("⭐ <b>Введите количество звезд</b>\n\nМинимум: 3 звезды\n\nПример: 15", parse_mode="HTML")
    await c.answer()

@dp.message(Form.deposit_amount)
async def process_deposit_amount(m: Message, state: FSMContext):
    try:
        stars = int(m.text.strip())
        if stars < 3:
            return await m.answer("❌ Минимальная сумма: 3 звезды")
        
        await state.clear()
        await create_stars_payment(m, stars)
        
    except ValueError:
        await m.answer("❌ Введите число (например: 15)")

@dp.callback_query(F.data.startswith("deposit:"))
async def deposit_amount(c: CallbackQuery):
    stars = int(c.data.split(":")[1])
    await create_stars_payment(c, stars)
    await c.answer()

async def create_stars_payment(event, stars: int):
    if isinstance(event, CallbackQuery):
        user_id = event.from_user.id
    else:
        user_id = event.from_user.id
    
    title = f"Пополнение баланса на {stars} ⭐"
    description = f"Покупка {stars} звезд для магазина аккаунтов"
    payload = f"deposit_{user_id}_{stars}"
    
    prices = [LabeledPrice(label="⭐ Звезды", amount=stars)]
    
    try:
        await bot.send_invoice(
            chat_id=user_id,
            title=title,
            description=description,
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=prices,
            need_name=False,
            need_phone_number=False,
            need_email=False,
            need_shipping_address=False,
            is_flexible=False,
        )
        
        if isinstance(event, CallbackQuery):
            await event.message.edit_text(f"⭐ <b>Оплата звездами</b>\n\nСумма: {stars} звезд\n\nОтправлен счет на оплату!", parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Ошибка создания платежа: {e}")
        if isinstance(event, CallbackQuery):
            await event.message.answer(f"❌ Ошибка: {str(e)}")

@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery):
    try:
        payload = pre_checkout_query.invoice_payload
        parts = payload.split("_")
        if len(parts) != 3:
            await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=False, error_message="Неверный платеж")
            return
        
        user_id = int(parts[1])
        if pre_checkout_query.from_user.id != user_id:
            await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=False, error_message="Ошибка авторизации")
            return
        
        await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)
        
    except Exception as e:
        logger.error(f"Pre-checkout error: {e}")
        await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=False, error_message="Ошибка платежа")

@dp.message(F.successful_payment)
async def successful_payment(m: Message):
    try:
        payment = m.successful_payment
        payload_parts = payment.invoice_payload.split("_")
        
        if len(payload_parts) != 3:
            logger.error(f"Неверный payload: {payment.invoice_payload}")
            return
        
        user_id = int(payload_parts[1])
        stars = int(payload_parts[2])
        
        if m.from_user.id != user_id:
            logger.warning(f"Платеж от другого пользователя: {m.from_user.id} != {user_id}")
            return
        
        success = await db.change_balance(user_id, Decimal(stars))
        
        if success:
            await db.add_transaction(user_id, stars, "deposit", f"Пополнение на {stars} звезд")
            
            await m.answer(f"✅ <b>Баланс пополнен!</b>\n\n⭐ Начислено: {stars} звезд\n\nТеперь ты можешь купить аккаунты в магазине!", parse_mode="HTML")
            
            for admin_id in ADMIN_IDS:
                await bot.send_message(admin_id, f"💰 <b>Пополнение баланса</b>\n\n👤 Пользователь: @{m.from_user.username or m.from_user.id}\n⭐ Сумма: {stars} звезд\n🆔 ID: {user_id}", parse_mode="HTML")
        else:
            await m.answer("❌ Ошибка начисления баланса. Обратитесь к администратору.")
            
    except Exception as e:
        logger.error(f"Error in successful_payment: {e}")
        await m.answer(f"❌ Ошибка обработки платежа: {str(e)}")

async def get_user_balance(user_id: int) -> Decimal:
    user = await db.get_user(user_id)
    return user["balance"] if user else Decimal(0)

# ============== УПРАВЛЕНИЕ СТРАНАМИ ==============

@dp.callback_query(F.data == "a_country_menu")
async def a_country_menu(c: CallbackQuery):
    if not admin(c.from_user.id): return
    
    rows = await db.countries()
    kb = []
    for r in rows:
        kb.append([InlineKeyboardButton(text=f"{r['emoji']} {r['name']}", callback_data=f"a_country_view:{r['id']}")])
    kb.append([InlineKeyboardButton(text="➕ Добавить страну", callback_data="a_country_add")])
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="home")])
    
    await c.message.edit_text("🌍 <b>Управление странами</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data == "a_country_add")
async def a_country_add(c: CallbackQuery, state: FSMContext):
    if not admin(c.from_user.id): return
    await state.set_state(Form.add_country)
    await c.message.edit_text("🌍 <b>Добавление страны</b>\n\nВведите в формате:\n<code>🇺🇸 США</code>\n\nИли просто название: США", parse_mode="HTML")
    await c.answer()

@dp.message(Form.add_country)
async def save_country(m: Message, state: FSMContext):
    text = m.text.strip()
    emoji = "🌍"
    name = text
    
    if len(text) > 0:
        first_char = text[0]
        if re.match(r'[\U0001F000-\U0001FFFF]', first_char):
            parts = text.split(maxsplit=1)
            emoji = parts[0]
            name = parts[1] if len(parts) > 1 else "Страна"
    
    await db.add_country(name, emoji)
    await state.clear()
    await m.answer(f"✅ Страна добавлена!\n\n{emoji} {name}", reply_markup=admin_kb())

@dp.callback_query(F.data.startswith("a_country_view:"))
async def a_country_view(c: CallbackQuery):
    if not admin(c.from_user.id): return
    country_id = int(c.data.split(":")[1])
    country = await db.get_country(country_id)
    
    if not country:
        return await c.answer("Страна не найдена", show_alert=True)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"a_country_edit:{country_id}")],
        [InlineKeyboardButton(text="🗑️ Удалить страну", callback_data=f"a_country_delete:{country_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="a_country_menu")]
    ])
    
    await c.message.edit_text(f"🌍 <b>{country['emoji']} {country['name']}</b>\n\nАккаунтов в стране: {await db.count_country_accounts(country_id)}", reply_markup=kb, parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data.startswith("a_country_edit:"))
async def a_country_edit(c: CallbackQuery, state: FSMContext):
    if not admin(c.from_user.id): return
    country_id = int(c.data.split(":")[1])
    await state.update_data(edit_country_id=country_id)
    await state.set_state(Form.edit_country_name)
    await c.message.edit_text("✏️ <b>Введите новое название страны</b>\n\nПример: 🇷🇺 Россия", parse_mode="HTML")
    await c.answer()

@dp.message(Form.edit_country_name)
async def edit_country_name(m: Message, state: FSMContext):
    data = await state.get_data()
    country_id = data.get('edit_country_id')
    
    text = m.text.strip()
    emoji = "🌍"
    name = text
    
    if len(text) > 0:
        first_char = text[0]
        if re.match(r'[\U0001F000-\U0001FFFF]', first_char):
            parts = text.split(maxsplit=1)
            emoji = parts[0]
            name = parts[1] if len(parts) > 1 else "Страна"
    
    await db.update_country(country_id, name, emoji)
    await state.clear()
    await m.answer(f"✅ Страна обновлена!\n\n{emoji} {name}", reply_markup=admin_kb())

@dp.callback_query(F.data.startswith("a_country_delete:"))
async def a_country_delete(c: CallbackQuery):
    if not admin(c.from_user.id): return
    country_id = int(c.data.split(":")[1])
    
    count = await db.count_country_accounts(country_id)
    if count > 0:
        return await c.answer(f"❌ В стране {count} аккаунтов! Сначала удалите их.", show_alert=True)
    
    await db.delete_country(country_id)
    await c.answer("✅ Страна удалена!", show_alert=True)
    await a_country_menu(c)

# ============== УПРАВЛЕНИЕ АККАУНТАМИ ==============

@dp.callback_query(F.data == "a_accounts_list")
async def a_accounts_list(c: CallbackQuery):
    if not admin(c.from_user.id): return
    
    rows = await db.all_accounts()
    
    if not rows:
        kb = [[InlineKeyboardButton(text="➕ Добавить аккаунт", callback_data="a_account")]]
        kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="home")])
        await c.message.edit_text("📭 <b>Аккаунтов нет</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
        await c.answer()
        return
    
    text = "📋 <b>Все аккаунты:</b>\n\n"
    kb = []
    
    for a in rows:
        status_emoji = "🟢" if a['status'] == 'available' else "🔴"
        text += f"{status_emoji} #{a['id']} {a['name']} — {int(a['price'])} ⭐\n📞 {a['phone']}\n\n"
        kb.append([InlineKeyboardButton(text=f"📱 {a['name']}", callback_data=f"a_account_view:{a['id']}")])
    
    kb.append([InlineKeyboardButton(text="➕ Добавить аккаунт", callback_data="a_account")])
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="home")])
    
    await c.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data.startswith("a_account_view:"))
async def a_account_view(c: CallbackQuery):
    if not admin(c.from_user.id): return
    account_id = int(c.data.split(":")[1])
    a = await db.account(account_id)
    
    if not a:
        return await c.answer("Аккаунт не найден", show_alert=True)
    
    status_emoji = "🟢" if a['status'] == 'available' else "🔴"
    country = await db.get_country(a['country_id'])
    country_name = f"{country['emoji']} {country['name']}" if country else "❌ Не указана"
    
    text = f"📱 <b>{a['name']}</b>\n\n"
    text += f"🆔 ID: {a['id']}\n"
    text += f"📞 Телефон: <code>{a['phone']}</code>\n"
    text += f"🌍 Страна: {country_name}\n"
    text += f"⭐ Цена: {int(a['price'])} звезд\n"
    text += f"📝 Описание: {a['description'] or 'Нет'}\n"
    text += f"📊 Статус: {status_emoji} {a['status']}\n"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"a_edit_name:{account_id}")],
        [InlineKeyboardButton(text="✏️ Изменить цену", callback_data=f"a_edit_price:{account_id}")],
        [InlineKeyboardButton(text="✏️ Изменить описание", callback_data=f"a_edit_desc:{account_id}")],
        [InlineKeyboardButton(text="🌍 Изменить страну", callback_data=f"a_edit_country:{account_id}")],
        [InlineKeyboardButton(text="🗑️ Удалить аккаунт", callback_data=f"a_delete_account:{account_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="a_accounts_list")]
    ])
    
    await c.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await c.answer()

# Редактирование названия
@dp.callback_query(F.data.startswith("a_edit_name:"))
async def a_edit_name(c: CallbackQuery, state: FSMContext):
    if not admin(c.from_user.id): return
    account_id = int(c.data.split(":")[1])
    await state.update_data(edit_account_id=account_id)
    await state.set_state(Form.edit_account_name)
    await c.message.edit_text("✏️ <b>Введите новое название аккаунта</b>", parse_mode="HTML")
    await c.answer()

@dp.message(Form.edit_account_name)
async def edit_account_name(m: Message, state: FSMContext):
    data = await state.get_data()
    account_id = data.get('edit_account_id')
    name = m.text.strip()
    
    await db.update_account_name(account_id, name)
    await state.clear()
    await m.answer("✅ Название обновлено!", reply_markup=admin_kb())

# Редактирование цены
@dp.callback_query(F.data.startswith("a_edit_price:"))
async def a_edit_price(c: CallbackQuery, state: FSMContext):
    if not admin(c.from_user.id): return
    account_id = int(c.data.split(":")[1])
    await state.update_data(edit_account_id=account_id)
    await state.set_state(Form.edit_account_price)
    await c.message.edit_text("✏️ <b>Введите новую цену в звездах</b>\n\nПример: 50", parse_mode="HTML")
    await c.answer()

@dp.message(Form.edit_account_price)
async def edit_account_price(m: Message, state: FSMContext):
    try:
        price = int(m.text.strip())
        if price <= 0:
            raise ValueError
    except:
        await m.answer("❌ Введите число больше 0")
        return
    
    data = await state.get_data()
    account_id = data.get('edit_account_id')
    
    await db.update_account_price(account_id, price)
    await state.clear()
    await m.answer(f"✅ Цена обновлена! Теперь: {price} ⭐", reply_markup=admin_kb())

# Редактирование описания
@dp.callback_query(F.data.startswith("a_edit_desc:"))
async def a_edit_desc(c: CallbackQuery, state: FSMContext):
    if not admin(c.from_user.id): return
    account_id = int(c.data.split(":")[1])
    await state.update_data(edit_account_id=account_id)
    await state.set_state(Form.edit_account_description)
    await c.message.edit_text("✏️ <b>Введите новое описание</b>\n\nОтправьте '-' чтобы удалить описание", parse_mode="HTML")
    await c.answer()

@dp.message(Form.edit_account_description)
async def edit_account_description(m: Message, state: FSMContext):
    data = await state.get_data()
    account_id = data.get('edit_account_id')
    description = m.text.strip()
    
    if description == "-":
        description = ""
    
    await db.update_account_description(account_id, description)
    await state.clear()
    await m.answer("✅ Описание обновлено!", reply_markup=admin_kb())

# Изменение страны
@dp.callback_query(F.data.startswith("a_edit_country:"))
async def a_edit_country(c: CallbackQuery, state: FSMContext):
    if not admin(c.from_user.id): return
    account_id = int(c.data.split(":")[1])
    await state.update_data(edit_account_id=account_id)
    
    rows = await db.countries()
    if not rows:
        return await c.answer("❌ Нет стран! Сначала добавьте страну.", show_alert=True)
    
    kb = []
    for r in rows:
        kb.append([InlineKeyboardButton(text=f"{r['emoji']} {r['name']}", callback_data=f"a_set_country:{account_id}:{r['id']}")])
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"a_account_view:{account_id}")])
    
    await c.message.edit_text("🌍 <b>Выберите новую страну</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data.startswith("a_set_country:"))
async def a_set_country(c: CallbackQuery):
    if not admin(c.from_user.id): return
    parts = c.data.split(":")
    account_id = int(parts[1])
    country_id = int(parts[2])
    
    await db.update_account_country(account_id, country_id)
    await c.answer("✅ Страна обновлена!", show_alert=True)
    await a_account_view(c)

# Удаление аккаунта
@dp.callback_query(F.data.startswith("a_delete_account:"))
async def a_delete_account(c: CallbackQuery):
    if not admin(c.from_user.id): return
    account_id = int(c.data.split(":")[1])
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"a_confirm_delete:{account_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"a_account_view:{account_id}")]
    ])
    
    await c.message.edit_text("⚠️ <b>Вы уверены, что хотите удалить этот аккаунт?</b>\n\nЭто действие нельзя отменить!", reply_markup=kb, parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data.startswith("a_confirm_delete:"))
async def a_confirm_delete(c: CallbackQuery):
    if not admin(c.from_user.id): return
    account_id = int(c.data.split(":")[1])
    
    await db.delete_account(account_id)
    await c.answer("✅ Аккаунт удален!", show_alert=True)
    await a_accounts_list(c)

# ============== ДОБАВЛЕНИЕ АККАУНТА ==============

@dp.callback_query(F.data == "a_account")
async def a_account(c: CallbackQuery, state: FSMContext):
    if not admin(c.from_user.id): return
    
    rows = await db.countries()
    if not rows:
        await c.message.answer("❌ Сначала добавьте страну через 'Управление странами'")
        await c.answer()
        return
    
    kb = []
    for r in rows:
        kb.append([InlineKeyboardButton(text=f"{r['emoji']} {r['name']}", callback_data=f"add_country:{r['id']}")])
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="a_accounts_list")])
    
    await c.message.edit_text("🌍 <b>Выберите страну для аккаунта:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data.startswith("add_country:"))
async def add_country_select(c: CallbackQuery, state: FSMContext):
    country_id = int(c.data.split(":")[1])
    await state.update_data(country_id=country_id)
    await state.set_state(Form.add_phone)
    
    await c.message.edit_text("📱 <b>Введите номер телефона</b>\n\nПоддерживаются любые форматы:\n+7XXXXXXXXXX (Россия)\n+1XXXXXXXXXX (США)\n+380XXXXXXXXX (Украина)\n\nПример: +79123456789", parse_mode="HTML")
    await c.answer()

@dp.message(Form.add_phone)
async def add_phone(m: Message, state: FSMContext):
    phone = format_phone(m.text.strip())
    
    if len(phone) < 10:
        await m.answer("❌ Слишком короткий номер. Пример: +79123456789")
        return
    
    exists = await db.account_by_phone(phone)
    if exists:
        await m.answer("❌ Аккаунт с таким номером уже существует в базе!")
        return
    
    await state.update_data(phone=phone)
    await state.set_state(Form.add_code)
    
    wait_msg = await m.answer("⏳ Отправка кода подтверждения...")
    
    try:
        success, message = await request_code(phone)
        
        if success:
            await wait_msg.edit_text(f"✅ {message}\n\n📝 Введите 5-значный код из Telegram:", parse_mode="HTML")
        else:
            await wait_msg.edit_text(f"❌ {message}")
            await state.clear()
            
    except Exception as e:
        await wait_msg.edit_text(f"❌ Ошибка: {str(e)}")
        await state.clear()

@dp.message(Form.add_code)
async def add_code(m: Message, state: FSMContext):
    code = m.text.strip()
    
    if not code.isdigit() or len(code) != 5:
        await m.answer("❌ Код должен состоять из 5 цифр")
        return
    
    data = await state.get_data()
    phone = data.get('phone')
    
    if not phone:
        await m.answer("❌ Ошибка: номер не найден. Начните заново.")
        await state.clear()
        return
    
    await m.answer("⏳ Проверка кода...")
    
    try:
        result, message = await verify_code(phone, code)
        
        if result:
            await m.answer(f"✅ {message}\n\n📝 Введите название аккаунта:")
            await state.set_state(Form.add_name)
            await state.update_data(session_ready=True)
        elif message == "2FA_REQUIRED":
            await m.answer("🔐 <b>Требуется пароль 2FA</b>\n\nВведите пароль двухфакторной аутентификации:", parse_mode="HTML")
            await state.set_state(Form.add_2fa)
        else:
            await m.answer(f"❌ {message}\n\nПопробуйте еще раз ввести код:")
            
    except Exception as e:
        await m.answer(f"❌ Ошибка: {str(e)}")

@dp.message(Form.add_2fa)
async def add_2fa(m: Message, state: FSMContext):
    password = m.text.strip()
    data = await state.get_data()
    phone = data.get('phone')
    
    if not phone:
        await m.answer("❌ Ошибка: номер не найден. Начните заново.")
        await state.clear()
        return
    
    await m.answer("⏳ Проверка 2FA...")
    
    try:
        result, message = await verify_2fa(phone, password)
        
        if result:
            await m.answer(f"✅ {message}\n\n📝 Введите название аккаунта:")
            await state.set_state(Form.add_name)
            await state.update_data(session_ready=True)
        else:
            await m.answer(f"❌ {message}\n\nПопробуйте еще раз ввести пароль 2FA:")
            
    except Exception as e:
        await m.answer(f"❌ Ошибка: {str(e)}")

@dp.message(Form.add_name)
async def add_name(m: Message, state: FSMContext):
    name = m.text.strip()
    
    if len(name) < 2:
        await m.answer("❌ Название слишком короткое (минимум 2 символа)")
        return
    
    await state.update_data(name=name)
    await state.set_state(Form.add_price)
    
    await m.answer(f"💰 <b>Введите цену в звездах</b>\n\nАккаунт: {name}\n\nПример: 50", parse_mode="HTML")

@dp.message(Form.add_price)
async def add_price(m: Message, state: FSMContext):
    try:
        price = int(m.text.strip())
        if price <= 0:
            raise ValueError("Цена должна быть больше 0")
    except:
        await m.answer("❌ Введите число больше 0\n\nПример: 50")
        return
    
    await state.update_data(price=price)
    await state.set_state(Form.add_description)
    
    await m.answer(f"📝 <b>Введите описание аккаунта</b>\n\nЦена: {price} звезд\n\nОпишите аккаунт (можно пропустить, отправьте '-'):", parse_mode="HTML")

@dp.message(Form.add_description)
async def add_description(m: Message, state: FSMContext):
    description = m.text.strip()
    if description == "-":
        description = ""
    
    data = await state.get_data()
    phone = data.get('phone')
    name = data.get('name')
    price = data.get('price')
    country_id = data.get('country_id')
    
    try:
        path = session_path(phone)
        
        await db.add_account(
            country_id=country_id,
            phone=phone,
            name=name,
            description=description,
            price=price,
            session_path=path
        )
        
        try:
            await start_listening(phone)
        except Exception as e:
            logger.error(f"Ошибка запуска прослушки: {e}")
        
        await state.clear()
        
        await m.answer(f"✅ <b>Аккаунт добавлен!</b>\n\n📱 Название: {name}\n📞 Телефон: {phone}\n⭐ Цена: {price} звезд\n📝 Описание: {description or 'Нет'}\n\nАккаунт появился в магазине!", parse_mode="HTML")
        
    except Exception as e:
        await m.answer(f"❌ Ошибка сохранения: {str(e)}")

# ============== БАЛАНС, БЛОК, СТАТИСТИКА ==============

@dp.callback_query(F.data == "a_balance")
async def a_balance(c: CallbackQuery, state: FSMContext):
    if not admin(c.from_user.id): return
    await state.set_state(Form.balance_user)
    await c.message.edit_text("💰 <b>Выдача баланса</b>\n\nВведите Telegram ID пользователя:", parse_mode="HTML")
    await c.answer()

@dp.message(Form.balance_user)
async def balance_user(m: Message, state: FSMContext):
    if not m.text.isdigit():
        return await m.answer("❌ Введите числовой ID.")
    await state.update_data(tg_id=int(m.text))
    await state.set_state(Form.balance_amount)
    await m.answer("⭐ Введите количество звезд (для списания укажите минус):")

@dp.message(Form.balance_amount)
async def balance_amount(m: Message, state: FSMContext):
    try:
        amount = Decimal(m.text.replace(",", "."))
    except:
        return await m.answer("❌ Неверная сумма.")
    
    d = await state.get_data()
    ok = await db.change_balance(d["tg_id"], amount)
    await state.clear()
    await m.answer("✅ Баланс изменён." if ok else "❌ Пользователь не найден.", reply_markup=admin_kb())

@dp.callback_query(F.data == "a_block")
async def a_block(c: CallbackQuery, state: FSMContext):
    if not admin(c.from_user.id): return
    await state.set_state(Form.block_user)
    await c.message.edit_text("🚫 <b>Блокировка пользователя</b>\n\nВведите ID или ID:unblock для разблокировки:", parse_mode="HTML")
    await c.answer()

@dp.message(Form.block_user)
async def block_user(m: Message, state: FSMContext):
    parts = m.text.split(":")
    tg = int(parts[0])
    blocked = not (len(parts) > 1 and parts[1].lower() == "unblock")
    await db.set_block(tg, blocked)
    await state.clear()
    await m.answer("✅ Готово.", reply_markup=admin_kb())

@dp.callback_query(F.data == "a_stats")
async def a_stats(c: CallbackQuery):
    if not admin(c.from_user.id): return
    s = await db.stats()
    await c.message.edit_text(f"📊 <b>Статистика</b>\n\n👤 Пользователи: {s['users']}\n📱 Аккаунты: {s['accounts']}\n🟢 В продаже: {s['available']}\n🧾 Покупки: {s['purchases']}\n⭐ Оборот: {int(s['revenue'])} звезд", parse_mode="HTML")
    await c.answer()

# ============== ЗАПУСК ==============

async def main():
    await db.init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
