import psycopg

from app.config import DATABASE_URL


def get_connection() -> psycopg.Connection:
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL не задан. Скопируйте .env.example в .env и укажите строку подключения Supabase."
        )
    return psycopg.connect(DATABASE_URL)


def check_connection() -> None:
    with get_connection() as conn:
        conn.execute("select 1")
