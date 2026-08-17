import asyncpg
from config import DATABASE_URL

pool = None

async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with pool.acquire() as c:
        await c.execute("""
        CREATE TABLE IF NOT EXISTS users(
            id BIGSERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE NOT NULL,
            username TEXT,
            balance NUMERIC(14,2) DEFAULT 0,
            blocked BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS countries(
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            emoji TEXT DEFAULT '🌍',
            active BOOLEAN DEFAULT TRUE
        );
        CREATE TABLE IF NOT EXISTS categories(
            id BIGSERIAL PRIMARY KEY,
            country_id BIGINT REFERENCES countries(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            active BOOLEAN DEFAULT TRUE
        );
        CREATE TABLE IF NOT EXISTS accounts(
            id BIGSERIAL PRIMARY KEY,
            category_id BIGINT REFERENCES categories(id) ON DELETE SET NULL,
            phone TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            price NUMERIC(14,2) NOT NULL DEFAULT 0,
            session_path TEXT,
            status TEXT NOT NULL DEFAULT 'available',
            sold_to BIGINT REFERENCES users(id),
            sold_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS purchases(
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(id),
            account_id BIGINT REFERENCES accounts(id),
            amount NUMERIC(14,2) NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS transactions(
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(id),
            amount NUMERIC(14,2) NOT NULL,
            kind TEXT NOT NULL,
            note TEXT DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        );
        """)

# ... все предыдущие функции остаются без изменений ...

# Добавляем новую функцию
async def add_transaction(tg_id, amount, kind, note):
    """Добавляет транзакцию"""
    async with pool.acquire() as c:
        u = await c.fetchrow("SELECT id FROM users WHERE telegram_id=$1", tg_id)
        if u:
            await c.execute("""
                INSERT INTO transactions(user_id, amount, kind, note)
                VALUES($1, $2, $3, $4)
            """, u["id"], amount, kind, note)
            return True
    return False