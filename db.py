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
            active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS categories(
            id BIGSERIAL PRIMARY KEY,
            country_id BIGINT REFERENCES countries(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
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
            value TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        """)

async def ensure_user(tg_id, username):
    async with pool.acquire() as c:
        return await c.fetchrow("""
        INSERT INTO users(telegram_id,username)
        VALUES($1,$2)
        ON CONFLICT(telegram_id) DO UPDATE SET username=EXCLUDED.username
        RETURNING *
        """, tg_id, username)

async def get_user(tg_id):
    async with pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM users WHERE telegram_id=$1", tg_id)

async def countries():
    async with pool.acquire() as c:
        return await c.fetch("SELECT * FROM countries WHERE active ORDER BY id")

async def categories(country_id):
    async with pool.acquire() as c:
        return await c.fetch(
            "SELECT * FROM categories WHERE country_id=$1 AND active ORDER BY id",
            country_id)

async def accounts(category_id):
    async with pool.acquire() as c:
        return await c.fetch("""
        SELECT * FROM accounts
        WHERE category_id=$1 AND status='available'
        ORDER BY id
        """, category_id)

async def account(account_id):
    async with pool.acquire() as c:
        return await c.fetchrow("SELECT * FROM accounts WHERE id=$1", account_id)

async def add_country(name, emoji):
    async with pool.acquire() as c:
        return await c.fetchrow(
            "INSERT INTO countries(name,emoji) VALUES($1,$2) RETURNING *",
            name, emoji)

async def add_category(country_id, name):
    async with pool.acquire() as c:
        return await c.fetchrow(
            "INSERT INTO categories(country_id,name) VALUES($1,$2) RETURNING *",
            country_id, name)

async def add_account(category_id, phone, name, description, price, session_path):
    async with pool.acquire() as c:
        return await c.fetchrow("""
        INSERT INTO accounts(category_id,phone,name,description,price,session_path)
        VALUES($1,$2,$3,$4,$5,$6) RETURNING *
        """, category_id, phone, name, description, price, session_path)

async def buy_account(tg_id, account_id):
    async with pool.acquire() as c:
        async with c.transaction():
            u = await c.fetchrow(
                "SELECT * FROM users WHERE telegram_id=$1 FOR UPDATE", tg_id)
            a = await c.fetchrow(
                "SELECT * FROM accounts WHERE id=$1 FOR UPDATE", account_id)

            if not u or u["blocked"]:
                return None, "blocked"
            if not a or a["status"] != "available":
                return None, "unavailable"
            if u["balance"] < a["price"]:
                return None, "balance"

            await c.execute(
                "UPDATE users SET balance=balance-$1 WHERE id=$2",
                a["price"], u["id"])
            await c.execute("""
                UPDATE accounts
                SET status='sold',sold_to=$1,sold_at=NOW()
                WHERE id=$2
            """, u["id"], a["id"])

            p = await c.fetchrow("""
                INSERT INTO purchases(user_id,account_id,amount)
                VALUES($1,$2,$3) RETURNING id
            """, u["id"], a["id"], a["price"])

            await c.execute("""
                INSERT INTO transactions(user_id,amount,kind,note)
                VALUES($1,$2,'purchase',$3)
            """, u["id"], -a["price"], f"Покупка #{p['id']}")

            return p["id"], "ok"

async def change_balance(tg_id, amount):
    async with pool.acquire() as c:
        async with c.transaction():
            u = await c.fetchrow(
                "SELECT * FROM users WHERE telegram_id=$1 FOR UPDATE", tg_id)
            if not u:
                return False
            await c.execute("UPDATE users SET balance=balance+$1 WHERE id=$2",
                             amount, u["id"])
            await c.execute("""
                INSERT INTO transactions(user_id,amount,kind,note)
                VALUES($1,$2,'admin','Изменение администратором')
            """, u["id"], amount)
            return True

async def set_block(tg_id, blocked):
    async with pool.acquire() as c:
        await c.execute(
            "UPDATE users SET blocked=$2 WHERE telegram_id=$1", tg_id, blocked)

async def stats():
    async with pool.acquire() as c:
        return await c.fetchrow("""
        SELECT
        (SELECT COUNT(*) FROM users) users,
        (SELECT COUNT(*) FROM accounts) accounts,
        (SELECT COUNT(*) FROM accounts WHERE status='available') available,
        (SELECT COUNT(*) FROM purchases) purchases,
        (SELECT COALESCE(SUM(amount),0) FROM purchases) revenue
        """)

async def my_purchases(tg_id):
    async with pool.acquire() as c:
        return await c.fetch("""
        SELECT p.id,p.amount,p.created_at,a.id account_id,a.name,a.phone,
               a.description,a.status
        FROM purchases p
        JOIN users u ON u.id=p.user_id
        JOIN accounts a ON a.id=p.account_id
        WHERE u.telegram_id=$1 ORDER BY p.id DESC
        """, tg_id)

async def add_transaction(tg_id, amount, kind, note):
    async with pool.acquire() as c:
        u = await c.fetchrow("SELECT id FROM users WHERE telegram_id=$1", tg_id)
        if u:
            await c.execute("""
                INSERT INTO transactions(user_id, amount, kind, note)
                VALUES($1, $2, $3, $4)
            """, u["id"], amount, kind, note)
            return True
    return False
