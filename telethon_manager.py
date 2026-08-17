from pathlib import Path
from telethon import TelegramClient
from config import TELEGRAM_API_ID, TELEGRAM_API_HASH, SESSIONS_DIR

Path(SESSIONS_DIR).mkdir(exist_ok=True)

clients = {}

def session_path(phone: str):
    clean = phone.replace("+","").replace(" ","")
    return str(Path(SESSIONS_DIR) / f"account_{clean}")

async def connect_session(phone: str):
    path = session_path(phone)
    client = TelegramClient(path, TELEGRAM_API_ID, TELEGRAM_API_HASH)
    await client.connect()
    clients[phone] = client
    return client

async def disconnect_session(phone: str):
    client = clients.pop(phone, None)
    if client:
        await client.disconnect()

async def is_authorized(phone: str):
    client = clients.get(phone) or await connect_session(phone)
    return await client.is_user_authorized()

async def close_all():
    for client in list(clients.values()):
        await client.disconnect()
    clients.clear()
