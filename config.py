import os
from pathlib import Path

BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_BOT_TOKEN_HERE")
ADMIN_IDS = {123456789}
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", 123456))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "PASTE_API_HASH_HERE")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://USER:PASSWORD@HOST:5432/DATABASE")
SESSIONS_DIR = os.getenv("SESSIONS_DIR", "sessions")
CURRENCY = os.getenv("CURRENCY", "₽")  # Рубли
SHOP_NAME = os.getenv("SHOP_NAME", "Dolphy Shop")

# Криптобот настройки
CRYPTOBOT_TOKEN = os.getenv("CRYPTOBOT_TOKEN", "PASTE_CRYPTOBOT_TOKEN_HERE")

# 1 звезда = 1 рубль
STAR_TO_RUB = 1

Path(SESSIONS_DIR).mkdir(exist_ok=True)
