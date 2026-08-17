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
from config import BOT_TOKEN, ADMIN_IDS, SHOP_NAME, CURRENCY, CRYPTOBOT_TOKEN, STAR_TO_RUB
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

from cryptopay import CryptoPay

logger = logging.getLogger(__name__)
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

crypto = CryptoPay(token=CRYPTOBOT_TOKEN)

# Курсы (обновляются через API)
usdt_to_rub = 100
btc_to_rub = 6000000
eth_to_rub = 300000
ton_to_rub = 5

crypto_invoices = {}

class Form(StatesGroup):
    add_phone = State()
    add_code = State()
    add_2fa = State()
    add_name = State()
    add_price = State()
    add_description = State()
    add_country = State()
    deposit_amount = State()
    deposit_crypto_rub = State()
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

def rub_to_stars(rub: int) -> int:
    return int(rub / STAR_TO_RUB)

def stars_to_rub(stars: int) -> int:
    return stars * STAR_TO_RUB

def rub_to_usdt(rub: int) -> float:
    return round(rub / usdt_to_rub, 2)

def rub_to_btc(rub: int) -> float:
    return round(rub / btc_to_rub, 8)

def rub_to_eth(rub: int) -> float:
    return round(rub / eth_to_rub, 6)

def rub_to_ton(rub: int) -> float:
    return round(rub / ton_to_rub, 2)

async def update_crypto_rates():
    global usdt_to_rub, btc_to_rub, eth_to_rub, ton_to_rub
    try:
        rates = await crypto.get_exchange_rates()
        for rate in rates:
            if rate.source == "USDT" and rate.target == "RUB":
                usdt_to_rub = float(rate.rate)
            elif rate.source == "BTC" and rate.target == "RUB":
                btc_to_rub = float(rate.rate)
            elif rate.source == "ETH" and rate.target == "RUB":
                eth_to_rub = float(rate.rate)
            elif rate.source == "TON" and rate.target == "RUB":
                ton_to_rub = float(rate.rate)
        logger.info(f"💰 Курсы обновлены")
    except Exception as e:
        logger.error(f"Ошибка обновления курсов: {e}")

def main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Купить", callback_data="countries")],
        [InlineKeyboardButton(text="💰 Баланс", callback_data="balance"),
         InlineKeyboardButton(text="💵 Пополнить", callback_data="deposit")],
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
    
    balance_rub = int(u["balance"])  # 1 звезда = 1 рубль
    
    if admin(m.from_user.id):
        await m.answer(
            f"🛍 <b>{SHOP_NAME}</b>\n\n👑 Админ-панель",
            reply_markup=admin_kb(),
            parse_mode="HTML"
        )
    else:
        await m.answer(
            f"🛍 <b>{SHOP_NAME}</b>\n\n💰 Баланс: {balance_rub} {CURRENCY}\n\nГлавное меню:",
            reply_markup=main_kb(),
            parse_mode="HTML"
        )

@dp.message(Command("admin"))
async def admin_cmd(m: Message):
    if admin(m.from_user.id):
        await m.answer("⚙️ <b>Админ-панель</b>", reply_markup=admin_kb(), parse_mode="HTML")
    else:
        await m.answer("❌ У вас нет доступа")

@dp.message(Command("rate"))
async def rate_command(m: Message):
    await update_crypto_rates()
    await m.answer(
        f"📊 <b>Текущие курсы</b>\n\n"
        f"💰 1 USDT = {usdt_to_rub} ₽\n"
        f"₿ 1 BTC = {btc_to_rub:,} ₽\n"
        f"Ξ 1 ETH = {eth_to_rub:,} ₽\n"
        f"🔷 1 TON = {ton_to_rub} ₽",
        parse_mode="HTML"
    )

# ============== СТРАНЫ ==============

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
        price_rub = int(a['price'])
        kb.append([InlineKeyboardButton(text=f"📱 {a['name']} — {price_rub} {CURRENCY}", callback_data=f"account:{a['id']}")])
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="countries")])
    
    await c.message.edit_text("📱 <b>Аккаунты в выбранной стране:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data.startswith("account:"))
async def account_view(c: CallbackQuery):
    aid = int(c.data.split(":")[1])
    a = await db.account(aid)
    if not a or a["status"] != "available":
        return await c.answer("Аккаунт уже продан.", show_alert=True)
    
    price_rub = int(a['price'])
    
    text = f"📱 <b>{a['name']}</b>\n\n{a['description']}\n\n💵 Цена: <b>{price_rub} {CURRENCY}</b>"
    
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
        rub_needed = int(account_data["price"] - user["balance"])
        return await c.answer(
            f"❌ Недостаточно средств!\n\n"
            f"Нужно: {rub_needed} {CURRENCY}\n"
            f"У тебя: {int(user['balance'])} {CURRENCY}\n\n"
            f"Пополни баланс в главном меню!",
            show_alert=True
        )
    
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
    
    price_rub = int(a['price'])
    
    text = (
        f"✅ <b>Покупка #{purchase}</b>\n\n"
        f"📱 Аккаунт: {a['name']}\n"
        f"📞 Номер: <code>{a['phone']}</code>\n"
        f"💵 Цена: {price_rub} {CURRENCY}\n\n"
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
    
    new_balance = await get_user_balance(c.from_user.id)
    await c.answer(
        f"✅ Покупка успешна! Остаток: {int(new_balance)} {CURRENCY}",
        show_alert=True
    )

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
    await c.message.edit_text(
        f"💰 <b>Баланс</b>\n\n"
        f"Баланс: <b>{int(u['balance'])} {CURRENCY}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💵 Пополнить", callback_data="deposit")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
        ]),
        parse_mode="HTML"
    )
    await c.answer()

@dp.callback_query(F.data == "profile")
async def profile(c: CallbackQuery):
    u = await db.get_user(c.from_user.id)
    await c.message.edit_text(
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{u['telegram_id']}</code>\n"
        f"👤 Имя: @{u['username'] or 'Не указано'}\n"
        f"💰 Баланс: <b>{int(u['balance'])} {CURRENCY}</b>\n"
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
        await c.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
        ]), parse_mode="HTML")
        await c.answer()
        return
    
    text = "🧾 <b>Мои покупки:</b>\n\n"
    kb = []
    
    for r in rows:
        price_rub = int(r['amount'])
        text += (
            f"#{r['id']} — {r['name']}\n"
            f"📞 {r['phone']}\n"
            f"💵 {price_rub} {CURRENCY}\n"
            f"📅 {r['created_at'].strftime('%d.%m.%Y %H:%M')}\n\n"
        )
        kb.append([InlineKeyboardButton(
            text=f"📩 Получить код для {r['name']}",
            callback_data=f"get_code:{r['phone']}"
        )])
    
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="home")])
    
    await c.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data == "support")
async def support(c: CallbackQuery):
    await c.message.edit_text(
        "🆘 <b>Поддержка</b>\n\n"
        "По всем вопросам обращайтесь к администратору.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
        ]),
        parse_mode="HTML"
    )
    await c.answer()

@dp.callback_query(F.data == "home")
async def home(c: CallbackQuery):
    u = await db.get_user(c.from_user.id)
    
    if admin(c.from_user.id):
        await c.message.edit_text(
            f"🛍 <b>{SHOP_NAME}</b>\n\n👑 Админ-панель",
            reply_markup=admin_kb(),
            parse_mode="HTML"
        )
    else:
        await c.message.edit_text(
            f"🛍 <b>{SHOP_NAME}</b>\n\n"
            f"💰 Баланс: {int(u['balance'])} {CURRENCY}\n\n"
            f"Главное меню:",
            reply_markup=main_kb(),
            parse_mode="HTML"
        )
    await c.answer()

# ============== ПОПОЛНЕНИЕ ==============

@dp.callback_query(F.data == "deposit")
async def deposit(c: CallbackQuery):
    await update_crypto_rates()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💵 100 ₽", callback_data="deposit_amount:100"),
         InlineKeyboardButton(text="💵 250 ₽", callback_data="deposit_amount:250")],
        [InlineKeyboardButton(text="💵 500 ₽", callback_data="deposit_amount:500"),
         InlineKeyboardButton(text="💵 1000 ₽", callback_data="deposit_amount:1000")],
        [InlineKeyboardButton(text="💵 Другая сумма", callback_data="deposit_custom")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="home")]
    ])
    
    await c.message.edit_text(
        f"💳 <b>Пополнение баланса</b>\n\n"
        f"Выберите сумму в рублях:\n\n"
        f"💰 1 USDT ≈ {usdt_to_rub} ₽\n"
        f"₿ 1 BTC ≈ {btc_to_rub:,} ₽\n"
        f"Ξ 1 ETH ≈ {eth_to_rub:,} ₽\n"
        f"🔷 1 TON ≈ {ton_to_rub} ₽\n"
        f"📊 Курсы обновлены автоматически",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await c.answer()

@dp.callback_query(F.data == "deposit_custom")
async def deposit_custom(c: CallbackQuery, state: FSMContext):
    await state.set_state(Form.deposit_amount)
    await c.message.edit_text(
        f"💵 <b>Введите сумму в рублях</b>\n\n"
        f"Минимум: 10 ₽\n"
        f"Пример: 150",
        parse_mode="HTML"
    )
    await c.answer()

@dp.message(Form.deposit_amount)
async def process_deposit_amount(m: Message, state: FSMContext):
    try:
        rub = int(m.text.strip())
        if rub < 10:
            return await m.answer(f"❌ Минимальная сумма: 10 ₽")
        
        await state.clear()
        await show_payment_methods(m, rub)
        
    except ValueError:
        await m.answer("❌ Введите число (например: 150)")

@dp.callback_query(F.data.startswith("deposit_amount:"))
async def deposit_amount(c: CallbackQuery):
    rub = int(c.data.split(":")[1])
    await show_payment_methods(c, rub)
    await c.answer()

async def show_payment_methods(event, rub: int):
    usdt = rub_to_usdt(rub)
    btc = rub_to_btc(rub)
    eth = rub_to_eth(rub)
    ton = rub_to_ton(rub)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💵 {usdt} USDT", callback_data=f"crypto_pay:USDT:{rub}")],
        [InlineKeyboardButton(text=f"₿ {btc} BTC", callback_data=f"crypto_pay:BTC:{rub}")],
        [InlineKeyboardButton(text=f"Ξ {eth} ETH", callback_data=f"crypto_pay:ETH:{rub}")],
        [InlineKeyboardButton(text=f"🔷 {ton} TON", callback_data=f"crypto_pay:TON:{rub}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="deposit")]
    ])
    
    text = (
        f"💰 <b>Выберите валюту для оплаты</b>\n\n"
        f"💵 Сумма: {rub} ₽\n\n"
        f"Выберите криптовалюту:"
    )
    
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("crypto_pay:"))
async def crypto_pay(c: CallbackQuery):
    parts = c.data.split(":")
    asset_str = parts[1]
    rub = int(parts[2])
    
    user_id = c.from_user.id
    stars = rub  # 1 звезда = 1 рубль
    
    if asset_str == "USDT":
        amount = rub_to_usdt(rub)
    elif asset_str == "BTC":
        amount = rub_to_btc(rub)
    elif asset_str == "ETH":
        amount = rub_to_eth(rub)
    elif asset_str == "TON":
        amount = rub_to_ton(rub)
    else:
        return await c.answer("❌ Неизвестная валюта", show_alert=True)
    
    try:
        invoice = await crypto.create_invoice(
            asset=asset_str,
            amount=amount,
            description=f"Пополнение баланса на {rub} ₽",
            hidden_message=f"ID: {user_id}",
            paid_btn_name="callback",
            paid_btn_url="https://t.me/your_bot"
        )
        
        crypto_invoices[invoice.invoice_id] = {
            'user_id': user_id,
            'rub': rub,
            'stars': stars,
            'amount': amount,
            'asset': asset_str
        }
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"💳 Оплатить {amount} {asset_str}", 
                url=invoice.pay_url
            )],
            [InlineKeyboardButton(
                text="🔄 Проверить оплату", 
                callback_data=f"check_crypto:{invoice.invoice_id}"
            )],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="deposit")]
        ])
        
        text = (
            f"💰 <b>Счет на оплату</b>\n\n"
            f"💵 Сумма: {rub} ₽\n"
            f"🪙 {amount} {asset_str}\n"
            f"⭐ Получите: {stars} звезд\n\n"
            f"📤 Нажмите кнопку ниже, чтобы оплатить\n"
            f"⏳ Счет действителен 1 час\n\n"
            f"После оплаты нажмите 'Проверить оплату'"
        )
        
        await c.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Ошибка создания счета: {e}")
        await c.message.answer(f"❌ Ошибка: {str(e)}")

@dp.callback_query(F.data.startswith("check_crypto:"))
async def check_crypto_payment(c: CallbackQuery):
    invoice_id = int(c.data.split(":")[1])
    
    if invoice_id not in crypto_invoices:
        return await c.answer("❌ Счет не найден", show_alert=True)
    
    invoice_data = crypto_invoices[invoice_id]
    user_id = invoice_data['user_id']
    
    if c.from_user.id != user_id:
        return await c.answer("❌ Это не ваш счет", show_alert=True)
    
    try:
        invoices = await crypto.get_invoices(invoice_ids=[invoice_id])
        
        if invoices and len(invoices) > 0:
            invoice = invoices[0]
            
            if invoice.status == "paid":
                rub = invoice_data['rub']
                stars = invoice_data['stars']
                
                success = await db.change_balance(user_id, Decimal(stars))
                
                if success:
                    await db.add_transaction(
                        user_id, 
                        stars, 
                        "deposit_crypto", 
                        f"Пополнение на {rub} ₽ ({invoice_data['amount']} {invoice_data['asset']})"
                    )
                    
                    del crypto_invoices[invoice_id]
                    
                    await c.message.edit_text(
                        f"✅ <b>Оплата подтверждена!</b>\n\n"
                        f"💰 Начислено: {rub} {CURRENCY}\n"
                        f"🪙 {invoice_data['amount']} {invoice_data['asset']}\n\n"
                        f"Теперь ты можешь купить аккаунты!",
                        parse_mode="HTML"
                    )
                    
                    await c.answer("✅ Баланс пополнен!", show_alert=True)
                    
                    for admin_id in ADMIN_IDS:
                        await bot.send_message(
                            admin_id,
                            f"💰 <b>Пополнение криптовалютой</b>\n\n"
                            f"👤 Пользователь: @{c.from_user.username or c.from_user.id}\n"
                            f"💵 Сумма: {rub} ₽ ({invoice_data['amount']} {invoice_data['asset']})\n"
                            f"🆔 ID: {user_id}",
                            parse_mode="HTML"
                        )
                else:
                    await c.answer("❌ Ошибка начисления баланса", show_alert=True)
                    
            elif invoice.status == "expired":
                await c.answer("⏰ Счет истек. Создайте новый.", show_alert=True)
                del crypto_invoices[invoice_id]
            else:
                await c.answer("⏳ Счет еще не оплачен. Попробуйте позже.", show_alert=True)
        else:
            await c.answer("❌ Счет не найден", show_alert=True)
            
    except Exception as e:
        logger.error(f"Ошибка проверки оплаты: {e}")
        await c.answer(f"❌ Ошибка: {str(e)}", show_alert=True)

async def get_user_balance(user_id: int) -> Decimal:
    user = await db.get_user(user_id)
    return user["balance"] if user else Decimal(0)

# ============== УПРАВЛЕНИЕ ==============

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
    await c.message.edit_text("🌍 <b>Добавление страны</b>\n\nВведите в формате:\n<code>🇺🇸 США</code>", parse_mode="HTML")
    await c.answer()

@dp.message(Form.add_country)
async def save_country(m: Message, state: FSMContext):
    text = m.text.strip()
    emoji = "🌍"
    name = text
    if len(text) > 0 and re.match(r'[\U0001F000-\U0001FFFF]', text[0]):
        emoji = text[0]
        name = text[1:].strip() if len(text) > 1 else "Страна"
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
    await c.message.edit_text(f"🌍 <b>{country['emoji']} {country['name']}</b>\n\nАккаунтов: {await db.count_country_accounts(country_id)}", reply_markup=kb, parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data.startswith("a_country_edit:"))
async def a_country_edit(c: CallbackQuery, state: FSMContext):
    if not admin(c.from_user.id): return
    country_id = int(c.data.split(":")[1])
    await state.update_data(edit_country_id=country_id)
    await state.set_state(Form.edit_country_name)
    await c.message.edit_text("✏️ <b>Введите новое название страны</b>", parse_mode="HTML")
    await c.answer()

@dp.message(Form.edit_country_name)
async def edit_country_name(m: Message, state: FSMContext):
    data = await state.get_data()
    country_id = data.get('edit_country_id')
    text = m.text.strip()
    emoji = "🌍"
    name = text
    if len(text) > 0 and re.match(r'[\U0001F000-\U0001FFFF]', text[0]):
        emoji = text[0]
        name = text[1:].strip() if len(text) > 1 else "Страна"
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
        price_rub = int(a['price'])
        text += f"{status_emoji} #{a['id']} {a['name']} — {price_rub} ₽\n📞 {a['phone']}\n\n"
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
    price_rub = int(a['price'])
    text = f"📱 <b>{a['name']}</b>\n\n🆔 ID: {a['id']}\n📞 Телефон: <code>{a['phone']}</code>\n🌍 Страна: {country_name}\n💰 Цена: {price_rub} ₽\n📝 Описание: {a['description'] or 'Нет'}\n📊 Статус: {status_emoji} {a['status']}"
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

@dp.callback_query(F.data.startswith("a_edit_name:"))
async def a_edit_name(c: CallbackQuery, state: FSMContext):
    if not admin(c.from_user.id): return
    account_id = int(c.data.split(":")[1])
    await state.update_data(edit_account_id=account_id)
    await state.set_state(Form.edit_account_name)
    await c.message.edit_text("✏️ <b>Введите новое название</b>", parse_mode="HTML")
    await c.answer()

@dp.message(Form.edit_account_name)
async def edit_account_name(m: Message, state: FSMContext):
    data = await state.get_data()
    account_id = data.get('edit_account_id')
    await db.update_account_name(account_id, m.text.strip())
    await state.clear()
    await m.answer("✅ Название обновлено!", reply_markup=admin_kb())

@dp.callback_query(F.data.startswith("a_edit_price:"))
async def a_edit_price(c: CallbackQuery, state: FSMContext):
    if not admin(c.from_user.id): return
    account_id = int(c.data.split(":")[1])
    await state.update_data(edit_account_id=account_id)
    await state.set_state(Form.edit_account_price)
    await c.message.edit_text("✏️ <b>Введите новую цену в рублях</b>", parse_mode="HTML")
    await c.answer()

@dp.message(Form.edit_account_price)
async def edit_account_price(m: Message, state: FSMContext):
    try:
        rub = int(m.text.strip())
        if rub <= 0:
            raise ValueError
    except:
        await m.answer("❌ Введите число больше 0")
        return
    data = await state.get_data()
    account_id = data.get('edit_account_id')
    await db.update_account_price(account_id, rub)
    await state.clear()
    await m.answer(f"✅ Цена обновлена! Теперь: {rub} ₽", reply_markup=admin_kb())

@dp.callback_query(F.data.startswith("a_edit_desc:"))
async def a_edit_desc(c: CallbackQuery, state: FSMContext):
    if not admin(c.from_user.id): return
    account_id = int(c.data.split(":")[1])
    await state.update_data(edit_account_id=account_id)
    await state.set_state(Form.edit_account_description)
    await c.message.edit_text("✏️ <b>Введите новое описание</b>\n\nОтправьте '-' чтобы удалить", parse_mode="HTML")
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

@dp.callback_query(F.data.startswith("a_edit_country:"))
async def a_edit_country(c: CallbackQuery):
    if not admin(c.from_user.id): return
    account_id = int(c.data.split(":")[1])
    rows = await db.countries()
    if not rows:
        return await c.answer("❌ Нет стран!", show_alert=True)
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

@dp.callback_query(F.data.startswith("a_delete_account:"))
async def a_delete_account(c: CallbackQuery):
    if not admin(c.from_user.id): return
    account_id = int(c.data.split(":")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"a_confirm_delete:{account_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"a_account_view:{account_id}")]
    ])
    await c.message.edit_text("⚠️ <b>Вы уверены?</b>\n\nЭто действие нельзя отменить!", reply_markup=kb, parse_mode="HTML")
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
        await c.message.answer("❌ Сначала добавьте страну")
        await c.answer()
        return
    kb = []
    for r in rows:
        kb.append([InlineKeyboardButton(text=f"{r['emoji']} {r['name']}", callback_data=f"add_country:{r['id']}")])
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="a_accounts_list")])
    await c.message.edit_text("🌍 <b>Выберите страну:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb), parse_mode="HTML")
    await c.answer()

@dp.callback_query(F.data.startswith("add_country:"))
async def add_country_select(c: CallbackQuery, state: FSMContext):
    country_id = int(c.data.split(":")[1])
    await state.update_data(country_id=country_id)
    await state.set_state(Form.add_phone)
    await c.message.edit_text("📱 <b>Введите номер телефона</b>\n\nПример: +79123456789", parse_mode="HTML")
    await c.answer()

@dp.message(Form.add_phone)
async def add_phone(m: Message, state: FSMContext):
    phone = format_phone(m.text.strip())
    if len(phone) < 10:
        await m.answer("❌ Слишком короткий номер. Пример: +79123456789")
        return
    exists = await db.account_by_phone(phone)
    if exists:
        await m.answer("❌ Аккаунт с таким номером уже существует!")
        return
    await state.update_data(phone=phone)
    await state.set_state(Form.add_code)
    wait_msg = await m.answer("⏳ Отправка кода...")
    try:
        success, message = await request_code(phone)
        if success:
            await wait_msg.edit_text(f"✅ {message}\n\n📝 Введите 5-значный код:", parse_mode="HTML")
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
        await m.answer("❌ Ошибка. Начните заново.")
        await state.clear()
        return
    await m.answer("⏳ Проверка кода...")
    try:
        result, message = await verify_code(phone, code)
        if result:
            await m.answer(f"✅ {message}\n\n📝 Введите название аккаунта:")
            await state.set_state(Form.add_name)
        elif message == "2FA_REQUIRED":
            await m.answer("🔐 <b>Требуется пароль 2FA</b>\n\nВведите пароль:", parse_mode="HTML")
            await state.set_state(Form.add_2fa)
        else:
            await m.answer(f"❌ {message}\n\nПопробуйте еще раз:")
    except Exception as e:
        await m.answer(f"❌ Ошибка: {str(e)}")

@dp.message(Form.add_2fa)
async def add_2fa(m: Message, state: FSMContext):
    password = m.text.strip()
    data = await state.get_data()
    phone = data.get('phone')
    if not phone:
        await m.answer("❌ Ошибка. Начните заново.")
        await state.clear()
        return
    await m.answer("⏳ Проверка 2FA...")
    try:
        result, message = await verify_2fa(phone, password)
        if result:
            await m.answer(f"✅ {message}\n\n📝 Введите название аккаунта:")
            await state.set_state(Form.add_name)
        else:
            await m.answer(f"❌ {message}\n\nПопробуйте еще раз:")
    except Exception as e:
        await m.answer(f"❌ Ошибка: {str(e)}")

@dp.message(Form.add_name)
async def add_name(m: Message, state: FSMContext):
    name = m.text.strip()
    if len(name) < 2:
        await m.answer("❌ Название слишком короткое")
        return
    await state.update_data(name=name)
    await state.set_state(Form.add_price)
    await m.answer(f"💰 <b>Введите цену в рублях</b>\n\nАккаунт: {name}\n\nПример: 50", parse_mode="HTML")

@dp.message(Form.add_price)
async def add_price(m: Message, state: FSMContext):
    try:
        rub = int(m.text.strip())
        if rub <= 0:
            raise ValueError
    except:
        await m.answer("❌ Введите число больше 0\n\nПример: 50")
        return
    await state.update_data(price=rub)
    await state.set_state(Form.add_description)
    await m.answer(f"📝 <b>Введите описание</b>\n\nЦена: {rub} ₽\n\nОтправьте '-' чтобы пропустить:", parse_mode="HTML")

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
        await db.add_account(country_id, phone, name, description, price, path)
        try:
            await start_listening(phone)
        except Exception as e:
            logger.error(f"Ошибка запуска прослушки: {e}")
        await state.clear()
        await m.answer(
            f"✅ <b>Аккаунт добавлен!</b>\n\n"
            f"📱 Название: {name}\n"
            f"📞 Телефон: {phone}\n"
            f"💰 Цена: {price} ₽\n"
            f"📝 Описание: {description or 'Нет'}\n\n"
            f"Аккаунт появился в магазине!",
            parse_mode="HTML"
        )
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
    await m.answer("💰 Введите сумму в рублях (для списания укажите минус):")

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
    await c.message.edit_text("🚫 <b>Блокировка</b>\n\nВведите ID или ID:unblock", parse_mode="HTML")
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
    revenue_rub = int(s['revenue'])
    await c.message.edit_text(
        f"📊 <b>Статистика</b>\n\n"
        f"👤 Пользователи: {s['users']}\n"
        f"📱 Аккаунты: {s['accounts']}\n"
        f"🟢 В продаже: {s['available']}\n"
        f"🧾 Покупки: {s['purchases']}\n"
        f"💰 Оборот: {revenue_rub} {CURRENCY}",
        parse_mode="HTML"
    )
    await c.answer()

async def main():
    await update_crypto_rates()
    await db.init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
