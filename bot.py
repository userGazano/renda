import asyncio
from decimal import Decimal
from pathlib import Path
import logging

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.types import SuccessfulPayment

import db
from config import BOT_TOKEN, ADMIN_IDS, SHOP_NAME, CURRENCY, STAR_TO_RUB
from telethon_manager import (
    connect_session, 
    session_path, 
    start_listening, 
    get_code_for_account, 
    wait_for_code, 
    is_authorized,
    disconnect_session,
    close_all
)

logger = logging.getLogger(__name__)
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# ... остальной код бота ...

# Цены в звездах (Telegram Stars)
PRICES = {
    "100": 100,   # 100 рублей = 10 звезд
    "300": 300,   # 300 рублей = 30 звезд
    "500": 500,   # 500 рублей = 50 звезд
    "1000": 1000, # 1000 рублей = 100 звезд
}

def rub_to_stars(rub: int) -> int:
    """Конвертирует рубли в звезды"""
    return int(rub / STAR_TO_RUB)

def stars_to_rub(stars: int) -> int:
    """Конвертирует звезды в рубли"""
    return stars * STAR_TO_RUB

class Form(StatesGroup):
    country = State()
    category = State()
    account = State()
    balance_user = State()
    balance_amount = State()
    block_user = State()
    deposit_amount = State()

def admin(uid): return uid in ADMIN_IDS

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
        [InlineKeyboardButton(text="🌍 Страны", callback_data="a_country")],
        [InlineKeyboardButton(text="📁 Категории", callback_data="a_category")],
        [InlineKeyboardButton(text="➕ Добавить аккаунт", callback_data="a_account")],
        [InlineKeyboardButton(text="💰 Выдать баланс", callback_data="a_balance")],
        [InlineKeyboardButton(text="🚫 Заблокировать", callback_data="a_block")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="a_stats")]
    ])

@dp.message(CommandStart())
async def start(m: Message):
    u = await db.ensure_user(m.from_user.id, m.from_user.username)
    if u["blocked"]:
        return await m.answer("🚫 Ваш аккаунт заблокирован.")
    
    # Проверяем, есть ли у пользователя звезды
    stars = await get_user_stars(m.from_user.id)
    balance_rub = u["balance"] * STAR_TO_RUB
    
    await m.answer(
        f"🛍 <b>{SHOP_NAME}</b>\n\n"
        f"⭐ Баланс: {u['balance']} звезд (~{balance_rub} ₽)\n\n"
        f"Главное меню:",
        reply_markup=main_kb(),
        parse_mode="HTML"
    )

@dp.message(Command("admin"))
async def admin_cmd(m: Message):
    if admin(m.from_user.id):
        await m.answer(
            "⚙️ <b>Админ-панель</b>",
            reply_markup=admin_kb(),
            parse_mode="HTML"
        )

# ============== ПОПОЛНЕНИЕ ЗВЕЗДАМИ ==============

@dp.callback_query(F.data == "deposit")
async def deposit(c: CallbackQuery):
    """Меню пополнения звездами"""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⭐ 10 звезд (100 ₽)", callback_data="deposit:100"),
            InlineKeyboardButton(text="⭐ 30 звезд (300 ₽)", callback_data="deposit:300")
        ],
        [
            InlineKeyboardButton(text="⭐ 50 звезд (500 ₽)", callback_data="deposit:500"),
            InlineKeyboardButton(text="⭐ 100 звезд (1000 ₽)", callback_data="deposit:1000")
        ],
        [
            InlineKeyboardButton(text="⭐ Другая сумма", callback_data="deposit_custom")
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
    ])
    
    await c.message.edit_text(
        "⭐ <b>Пополнение баланса</b>\n\n"
        "Выберите количество звезд для покупки:\n\n"
        f"💰 1 звезда = {STAR_TO_RUB} ₽",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await c.answer()

@dp.callback_query(F.data == "deposit_custom")
async def deposit_custom(c: CallbackQuery, state: FSMContext):
    """Ввод своей суммы"""
    await state.set_state(Form.deposit_amount)
    await c.message.edit_text(
        "⭐ <b>Введите сумму в рублях</b>\n\n"
        f"Минимальная сумма: 100 ₽ (10 звезд)\n"
        f"1 звезда = {STAR_TO_RUB} ₽\n\n"
        f"Пример: 250",
        parse_mode="HTML"
    )
    await c.answer()

@dp.message(Form.deposit_amount)
async def process_deposit_amount(m: Message, state: FSMContext):
    """Обработка своей суммы"""
    try:
        amount_rub = int(m.text.strip())
        if amount_rub < 100:
            return await m.answer("❌ Минимальная сумма: 100 ₽")
        
        stars = rub_to_stars(amount_rub)
        if stars < 1:
            return await m.answer("❌ Слишком мало для конвертации")
        
        await state.clear()
        await create_stars_payment(m, stars, amount_rub)
        
    except ValueError:
        await m.answer("❌ Введите число (например: 250)")

@dp.callback_query(F.data.startswith("deposit:"))
async def deposit_amount(c: CallbackQuery):
    """Обработка выбора суммы"""
    amount_rub = int(c.data.split(":")[1])
    stars = rub_to_stars(amount_rub)
    
    await create_stars_payment(c, stars, amount_rub)
    await c.answer()

async def create_stars_payment(event, stars: int, amount_rub: int):
    """Создает платеж через Telegram Stars"""
    if isinstance(event, CallbackQuery):
        message = event.message
        user_id = event.from_user.id
    else:
        message = event
        user_id = event.from_user.id
    
    # Создаем инвойс для оплаты звездами
    title = f"Пополнение баланса на {stars} ⭐"
    description = f"Покупка {stars} звезд для магазина аккаунтов\nЭквивалент: {amount_rub} ₽"
    payload = f"deposit_{user_id}_{stars}_{amount_rub}"  # Уникальный ID платежа
    
    # Цена в звездах (Telegram Stars - это XTR)
    prices = [LabeledPrice(label="⭐ Звезды", amount=stars)]
    
    try:
        await bot.send_invoice(
            chat_id=user_id,
            title=title,
            description=description,
            payload=payload,
            provider_token="",  # Для звезд оставляем пустым
            currency="XTR",  # Валюта Telegram Stars
            prices=prices,
            need_name=False,
            need_phone_number=False,
            need_email=False,
            need_shipping_address=False,
            is_flexible=False,
        )
        
        # Если это callback, обновляем сообщение
        if isinstance(event, CallbackQuery):
            await event.message.edit_text(
                f"⭐ <b>Оплата звездами</b>\n\n"
                f"Сумма: {stars} звезд (~{amount_rub} ₽)\n\n"
                f"Отправлен счет на оплату!",
                parse_mode="HTML"
            )
        
    except Exception as e:
        logger.error(f"Ошибка создания платежа: {e}")
        if isinstance(event, CallbackQuery):
            await event.message.answer(
                f"❌ Ошибка создания платежа:\n{str(e)}"
            )
        else:
            await event.answer(f"❌ Ошибка: {str(e)}")

# ============== ОБРАБОТКА ПЛАТЕЖЕЙ ==============

@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery):
    """Подтверждение платежа перед списанием"""
    try:
        # Проверяем payload
        payload = pre_checkout_query.invoice_payload
        parts = payload.split("_")
        if len(parts) != 4:
            await bot.answer_pre_checkout_query(
                pre_checkout_query.id, 
                ok=False, 
                error_message="Неверный платеж"
            )
            return
        
        # Проверяем пользователя
        user_id = int(parts[1])
        if pre_checkout_query.from_user.id != user_id:
            await bot.answer_pre_checkout_query(
                pre_checkout_query.id,
                ok=False,
                error_message="Ошибка авторизации"
            )
            return
        
        # Все проверки пройдены
        await bot.answer_pre_checkout_query(
            pre_checkout_query.id, 
            ok=True
        )
        
    except Exception as e:
        logger.error(f"Pre-checkout error: {e}")
        await bot.answer_pre_checkout_query(
            pre_checkout_query.id,
            ok=False,
            error_message="Ошибка платежа. Попробуйте позже."
        )

@dp.message(F.successful_payment)
async def successful_payment(m: Message):
    """Обработка успешного платежа"""
    try:
        payment = m.successful_payment
        
        # Парсим payload
        payload_parts = payment.invoice_payload.split("_")
        if len(payload_parts) != 4:
            logger.error(f"Неверный payload: {payment.invoice_payload}")
            return
        
        user_id = int(payload_parts[1])
        stars = int(payload_parts[2])
        amount_rub = int(payload_parts[3])
        
        # Проверяем, что платеж от того же пользователя
        if m.from_user.id != user_id:
            logger.warning(f"Платеж от другого пользователя: {m.from_user.id} != {user_id}")
            return
        
        # Начисляем баланс (в звездах)
        success = await db.change_balance(user_id, Decimal(stars))
        
        if success:
            # Логируем платеж
            await db.add_transaction(
                user_id, 
                stars, 
                "deposit", 
                f"Пополнение на {stars} звезд ({amount_rub} ₽)"
            )
            
            # Отправляем подтверждение
            await m.answer(
                f"✅ <b>Баланс пополнен!</b>\n\n"
                f"⭐ Начислено: {stars} звезд\n"
                f"💰 Эквивалент: {amount_rub} ₽\n"
                f"📊 Текущий баланс: {await get_user_balance(user_id)} звезд\n\n"
                f"Теперь ты можешь купить аккаунты в магазине!",
                parse_mode="HTML"
            )
            
            # Уведомляем админа
            for admin_id in ADMIN_IDS:
                await bot.send_message(
                    admin_id,
                    f"💰 <b>Пополнение баланса</b>\n\n"
                    f"👤 Пользователь: @{m.from_user.username or m.from_user.id}\n"
                    f"⭐ Сумма: {stars} звезд ({amount_rub} ₽)\n"
                    f"🆔 ID: {user_id}",
                    parse_mode="HTML"
                )
        else:
            await m.answer("❌ Ошибка начисления баланса. Обратитесь к администратору.")
            
    except Exception as e:
        logger.error(f"Error in successful_payment: {e}")
        await m.answer(f"❌ Ошибка обработки платежа: {str(e)}")

async def get_user_balance(user_id: int) -> Decimal:
    """Получает баланс пользователя"""
    user = await db.get_user(user_id)
    return user["balance"] if user else Decimal(0)

async def get_user_stars(user_id: int) -> Decimal:
    """Получает баланс в звездах (по сути тот же баланс)"""
    return await get_user_balance(user_id)

# ============== ПОКУПКА АККАУНТА ==============

@dp.callback_query(F.data == "countries")
async def countries(c: CallbackQuery):
    rows = await db.countries()
    kb = [[InlineKeyboardButton(text=f"{r['emoji']} {r['name']}", callback_data=f"country:{r['id']}")] for r in rows]
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="home")])
    await c.message.edit_text(
        "🌍 <b>Выберите страну:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="HTML"
    )
    await c.answer()

@dp.callback_query(F.data.startswith("country:"))
async def category_list(c: CallbackQuery):
    cid = int(c.data.split(":")[1])
    rows = await db.categories(cid)
    kb = [[InlineKeyboardButton(text=f"📁 {r['name']}", callback_data=f"category:{r['id']}")] for r in rows]
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="countries")])
    await c.message.edit_text(
        "📁 <b>Категории:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="HTML"
    )
    await c.answer()

@dp.callback_query(F.data.startswith("category:"))
async def account_list(c: CallbackQuery):
    cid = int(c.data.split(":")[1])
    rows = await db.accounts(cid)
    kb = []
    for a in rows:
        # Конвертируем цену в звезды
        price_stars = int(a['price'])
        price_rub = stars_to_rub(price_stars)
        kb.append([InlineKeyboardButton(
            text=f"📱 {a['name']} — {price_stars} {CURRENCY} (~{price_rub} ₽)",
            callback_data=f"account:{a['id']}"
        )])
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="countries")])
    await c.message.edit_text(
        "🛒 <b>Доступные аккаунты:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="HTML"
    )
    await c.answer()

@dp.callback_query(F.data.startswith("account:"))
async def account_view(c: CallbackQuery):
    aid = int(c.data.split(":")[1])
    a = await db.account(aid)
    if not a or a["status"] != "available":
        return await c.answer("Аккаунт уже продан.", show_alert=True)
    
    price_stars = int(a['price'])
    price_rub = stars_to_rub(price_stars)
    
    text = (
        f"📱 <b>{a['name']}</b>\n\n"
        f"{a['description']}\n\n"
        f"💵 Цена: <b>{price_stars} {CURRENCY}</b>\n"
        f"💰 Эквивалент: ~{price_rub} ₽"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Купить", callback_data=f"buy:{aid}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"category:{a['category_id']}")]
    ])
    await c.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data.startswith("buy:"))
async def buy(c: CallbackQuery):
    aid = int(c.data.split(":")[1])
    
    # Получаем информацию об аккаунте
    account_data = await db.account(aid)
    if not account_data or account_data["status"] != "available":
        return await c.answer("Аккаунт недоступен.", show_alert=True)
    
    # Проверяем баланс (в звездах)
    user = await db.get_user(c.from_user.id)
    if user["balance"] < account_data["price"]:
        stars_needed = int(account_data["price"] - user["balance"])
        return await c.answer(
            f"❌ Недостаточно звезд!\n\n"
            f"Нужно: {int(account_data['price'])} ⭐\n"
            f"У тебя: {int(user['balance'])} ⭐\n"
            f"Не хватает: {stars_needed} ⭐\n\n"
            f"Пополни баланс в главном меню!",
            show_alert=True
        )
    
    # Выполняем покупку
    purchase, status = await db.buy_account(c.from_user.id, aid)
    
    if status == "balance":
        return await c.answer("Недостаточно средств.", show_alert=True)
    if status != "ok":
        return await c.answer("Аккаунт недоступен.", show_alert=True)
    
    # Получаем обновленные данные
    a = await db.account(aid)
    
    # Запускаем прослушку
    try:
        await start_listening(a['phone'])
    except Exception as e:
        logger.error(f"Ошибка запуска прослушки: {e}")
    
    # Отправляем сообщение о покупке
    text = (
        f"✅ <b>Покупка #{purchase}</b>\n\n"
        f"📱 Аккаунт: {a['name']}\n"
        f"📞 Номер: <code>{a['phone']}</code>\n"
        f"⭐ Цена: {int(a['price'])} звезд\n\n"
        f"📝 {a['description']}\n\n"
        f"🔐 <b>Чтобы получить код подтверждения:</b>\n"
        f"1. Открой Telegram на телефоне\n"
        f"2. Зайди в этот аккаунт\n"
        f"3. Код придет сюда автоматически"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📩 Получить код", callback_data=f"get_code:{a['phone']}")],
        [InlineKeyboardButton(text="🧾 Мои покупки", callback_data="purchases")]
    ])
    
    await c.message.answer(text, reply_markup=kb, parse_mode="HTML")
    
    # Обновляем баланс в сообщении
    new_balance = await get_user_balance(c.from_user.id)
    await c.answer(
        f"✅ Покупка успешна! Остаток: {int(new_balance)} ⭐",
        show_alert=True
    )

# ============== ПОЛУЧЕНИЕ КОДА ==============

@dp.callback_query(F.data.startswith("get_code:"))
async def get_code(c: CallbackQuery):
    phone = c.data.split(":", 1)[1]
    user_id = c.from_user.id
    
    # Проверяем принадлежность аккаунта
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
        await wait_msg.edit_text(
            f"✅ <b>Код подтверждения:</b>\n\n"
            f"<code>{code}</code>\n\n"
            f"⏱️ Действует 10 минут",
            parse_mode="HTML"
        )
        await c.answer("✅ Код получен!", show_alert=True)
    else:
        await wait_msg.edit_text(
            "⏰ <b>Код не получен</b>\n\n"
            "Возможные причины:\n"
            "• Код не был отправлен в Telegram\n"
            "• Аккаунт не авторизован\n"
            "• Истекло время ожидания (2 минуты)\n\n"
            "Попробуй еще раз через 'Мои покупки'",
            parse_mode="HTML"
        )
        await c.answer("⏰ Время ожидания истекло", show_alert=True)

# ============== ЛИЧНЫЙ КАБИНЕТ ==============

@dp.callback_query(F.data == "balance")
async def balance(c: CallbackQuery):
    u = await db.get_user(c.from_user.id)
    balance_rub = int(u["balance"]) * STAR_TO_RUB
    await c.message.edit_text(
        f"⭐ <b>Баланс</b>\n\n"
        f"Звезд: <b>{int(u['balance'])}</b>\n"
        f"Рублей: <b>{balance_rub:,}</b> ₽\n\n"
        f"1 звезда = {STAR_TO_RUB} ₽",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Пополнить", callback_data="deposit")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
        ]),
        parse_mode="HTML"
    )
    await c.answer()

@dp.callback_query(F.data == "profile")
async def profile(c: CallbackQuery):
    u = await db.get_user(c.from_user.id)
    balance_rub = int(u["balance"]) * STAR_TO_RUB
    await c.message.edit_text(
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{u['telegram_id']}</code>\n"
        f"👤 Имя: @{u['username'] or 'Не указано'}\n"
        f"⭐ Баланс: <b>{int(u['balance'])}</b> звезд (~{balance_rub} ₽)\n"
        f"📅 Зарегистрирован: {u['created_at'].strftime('%d.%m.%Y')}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
        ]),
        parse_mode="HTML"
    )
    await c.answer()

@dp.callback_query(F.data == "purchases")
async def purchases(c: CallbackQuery):
    rows = await db.my_purchases(c.from_user.id)
    
    if not rows:
        text = "🧾 У тебя пока нет покупок."
        await c.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
            ]),
            parse_mode="HTML"
        )
        await c.answer()
        return
    
    text = "🧾 <b>Мои покупки:</b>\n\n"
    kb = []
    
    for r in rows:
        price_rub = stars_to_rub(int(r['amount']))
        text += (
            f"#{r['id']} — {r['name']}\n"
            f"📞 {r['phone']}\n"
            f"⭐ {int(r['amount'])} звезд (~{price_rub} ₽)\n"
            f"📅 {r['created_at'].strftime('%d.%m.%Y %H:%M')}\n\n"
        )
        kb.append([InlineKeyboardButton(
            text=f"📩 Получить код для {r['name']}",
            callback_data=f"get_code:{r['phone']}"
        )])
    
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="home")])
    
    await c.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        parse_mode="HTML"
    )
    await c.answer()

@dp.callback_query(F.data == "support")
async def support(c: CallbackQuery):
    await c.message.edit_text(
        "🆘 <b>Поддержка</b>\n\n"
        "По всем вопросам обращайтесь к администратору:\n"
        "📩 @admin_username\n\n"
        "Также ты можешь написать в поддержку Telegram:\n"
        "https://t.me/TelegramSupport",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
        ]),
        parse_mode="HTML"
    )
    await c.answer()

@dp.callback_query(F.data == "home")
async def home(c: CallbackQuery):
    u = await db.get_user(c.from_user.id)
    balance_rub = int(u["balance"]) * STAR_TO_RUB
    await c.message.edit_text(
        f"🛍 <b>{SHOP_NAME}</b>\n\n"
        f"⭐ Баланс: {int(u['balance'])} звезд (~{balance_rub} ₽)\n\n"
        f"Главное меню:",
        reply_markup=main_kb(),
        parse_mode="HTML"
    )
    await c.answer()

# ============== АДМИНКА ==============

@dp.callback_query(F.data == "a_country")
async def a_country(c: CallbackQuery, state: FSMContext):
    if not admin(c.from_user.id): return
    await state.set_state(Form.country)
    await c.message.answer("Отправьте: 🇺🇸 США")
    await c.answer()

@dp.message(Form.country)
async def save_country(m: Message, state: FSMContext):
    p = m.text.split(maxsplit=1)
    emoji = p[0] if len(p) > 1 else "🌍"
    name = p[1] if len(p) > 1 else p[0]
    await db.add_country(name, emoji)
    await state.clear()
    await m.answer("✅ Страна создана.", reply_markup=admin_kb())

@dp.callback_query(F.data == "a_category")
async def a_category(c: CallbackQuery, state: FSMContext):
    if not admin(c.from_user.id): return
    rows = await db.countries()
    kb = [[InlineKeyboardButton(text=f"{r['emoji']} {r['name']}", callback_data=f"newcat:{r['id']}")] for r in rows]
    await c.message.answer("Выберите страну:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await c.answer()

@dp.callback_query(F.data.startswith("newcat:"))
async def newcat(c: CallbackQuery, state: FSMContext):
    await state.update_data(country_id=int(c.data.split(":")[1]))
    await state.set_state(Form.category)
    await c.message.answer("Название категории:")
    await c.answer()

@dp.message(Form.category)
async def save_category(m: Message, state: FSMContext):
    d = await state.get_data()
    await db.add_category(d["country_id"], m.text.strip())
    await state.clear()
    await m.answer("✅ Категория создана.", reply_markup=admin_kb())

@dp.callback_query(F.data == "a_account")
async def a_account(c: CallbackQuery, state: FSMContext):
    if not admin(c.from_user.id): return
    rows = await db.countries()
    kb = []
    for country in rows:
        for cat in await db.categories(country["id"]):
            kb.append([InlineKeyboardButton(
                text=f"{country['emoji']} {cat['name']}",
                callback_data=f"newacc:{cat['id']}"
            )])
    await c.message.answer("Выберите категорию:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await c.answer()

@dp.callback_query(F.data.startswith("newacc:"))
async def newacc(c: CallbackQuery, state: FSMContext):
    await state.update_data(category_id=int(c.data.split(":")[1]))
    await state.set_state(Form.account)
    await c.message.answer(
        "Формат:\n"
        "+79990000000 | Название | Цена (в звездах) | Описание\n\n"
        "Пример: +79990000000 | VIP аккаунт | 50 | Описание аккаунта"
    )
    await c.answer()

@dp.message(Form.account)
async def save_account(m: Message, state: FSMContext):
    try:
        parts = [x.strip() for x in m.text.split("|", 3)]
        if len(parts) != 4:
            raise ValueError("Неверный формат")
        phone, name, price, description = parts
        price = Decimal(price)  # Цена в звездах
        if price <= 0:
            raise ValueError("Цена должна быть больше 0")
    except Exception as e:
        return await m.answer(f"❌ Формат: +79990000000 | Название | Цена | Описание\nОшибка: {e}")
    
    d = await state.get_data()
    
    try:
        client = await connect_session(phone)
        if not await client.is_user_authorized():
            await client.disconnect()
            return await m.answer("❌ Для добавления нужен уже авторизованный аккаунт/сессия.")
        
        path = session_path(phone)
        await db.add_account(d["category_id"], phone, name, description, price, path)
        
        # Запускаем прослушку
        await start_listening(phone)
        
    except Exception as e:
        return await m.answer(f"❌ Ошибка: {e}")
    
    await state.clear()
    await m.answer(
        f"✅ Аккаунт добавлен!\n\n"
        f"📱 {name}\n"
        f"⭐ Цена: {price} звезд\n"
        f"📞 {phone}",
        reply_markup=admin_kb()
    )

@dp.callback_query(F.data == "a_balance")
async def a_balance(c: CallbackQuery, state: FSMContext):
    if not admin(c.from_user.id): return
    await state.set_state(Form.balance_user)
    await c.message.answer("Telegram ID пользователя:")
    await c.answer()

@dp.message(Form.balance_user)
async def balance_user(m: Message, state: FSMContext):
    if not m.text.isdigit():
        return await m.answer("Введите числовой ID.")
    await state.update_data(tg_id=int(m.text))
    await state.set_state(Form.balance_amount)
    await m.answer("Сумма (в звездах, для списания укажите минус):")

@dp.message(Form.balance_amount)
async def balance_amount(m: Message, state: FSMContext):
    try:
        amount = Decimal(m.text.replace(",", "."))
    except:
        return await m.answer("Неверная сумма.")
    
    d = await state.get_data()
    ok = await db.change_balance(d["tg_id"], amount)
    await state.clear()
    await m.answer("✅ Баланс изменён." if ok else "❌ Пользователь не найден.", reply_markup=admin_kb())

@dp.callback_query(F.data == "a_block")
async def a_block(c: CallbackQuery, state: FSMContext):
    if not admin(c.from_user.id): return
    await state.set_state(Form.block_user)
    await c.message.answer("ID или ID:unblock")
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
    await c.message.answer(
        f"📊 <b>Статистика</b>\n\n"
        f"👤 Пользователи: {s['users']}\n"
        f"📱 Аккаунты: {s['accounts']}\n"
        f"🟢 В продаже: {s['available']}\n"
        f"🧾 Покупки: {s['purchases']}\n"
        f"⭐ Оборот: {int(s['revenue'])} звезд\n"
        f"💰 Эквивалент: {int(s['revenue']) * STAR_TO_RUB} ₽",
        parse_mode="HTML"
    )
    await c.answer()

# ============== ЗАПУСК ==============

async def main():
    await db.init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
