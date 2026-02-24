"""
СИСТЕМА "ЗЕРКАЛО" — Telegram Bot
"""

import os
import asyncio
import json
import httpx
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_URL = os.getenv("API_URL", "http://localhost:8000")
WEBAPP_URL = os.getenv("WEBAPP_URL")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user = message.from_user
    
    first_name = user.first_name or ""
    username = user.username or ""
    
    webapp_url = (
        f"{WEBAPP_URL}"
        f"?test=profile-v1"
        f"&user_id={user.id}"
        f"&username={username}"
        f"&first_name={first_name}"
    )
    
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(
        text="🪞 Пройти тест «Зеркало»",
        web_app=WebAppInfo(url=webapp_url)
    ))
    
    await message.answer(
        f"Привет, {first_name}! 👋\n\n"
        "«*Зеркало*» — короткое исследование, которое поможет увидеть "
        "скрытые стороны своей психики.\n\n"
        "⏱ Займёт около 5-7 минут\n"
        "✨ В конце получишь персональный разбор\n\n"
        "Готов?",
        parse_mode="Markdown",
        reply_markup=kb.as_markup()
    )


@dp.message(F.web_app_data)
async def handle_webapp_data(message: types.Message):
    user = message.from_user
    
    try:
        data = json.loads(message.web_app_data.data)
    except Exception:
        await message.answer("Что-то пошло не так. Попробуй ещё раз.")
        return
    
    if data.get("status") == "completed":
        user_result = data.get("for_user", "")
        
        await message.answer(
            f"🪞 *Твоё Зеркало*\n\n{user_result}",
            parse_mode="Markdown"
        )
        
        first_name = user.first_name or ""
        username = user.username or ""
        webapp_url = (
            f"{WEBAPP_URL}"
            f"?test=profile-v1"
            f"&user_id={user.id}"
            f"&username={username}"
            f"&first_name={first_name}"
        )
        
        kb = InlineKeyboardBuilder()
        kb.add(InlineKeyboardButton(
            text="🔄 Пройти ещё раз",
            web_app=WebAppInfo(url=webapp_url)
        ))
        
        await message.answer(
            "Поделись этим тестом с кем-то близким 💙",
            reply_markup=kb.as_markup()
        )
    
    elif data.get("status") == "error":
        await message.answer(
            "Произошла ошибка при анализе. Попробуй пройти тест ещё раз."
        )


@dp.message(F.text == "/profile")
async def cmd_profile(message: types.Message):
    user_id = message.from_user.id
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{API_URL}/user/{user_id}/profile")
            if resp.status_code == 404:
                await message.answer("Ты ещё не проходил тесты. Начни с /start")
                return
            
            profile = resp.json()
            tags = profile.get("personality_tags") or []
            tags_text = ", ".join(tags) if tags else "—"
            
            vak_map = {"visual": "Визуал 👁", "audial": "Аудиал 👂", "kinesthetic": "Кинестетик 🤲"}
            stress_map = {"fight": "Борьба ⚡", "flight": "Бегство 🏃", "freeze": "Замирание 🧊"}
            decision_map = {"logical": "Логик 🧮", "emotional": "Эмоциональный ❤️", "impulsive": "Импульсивный ⚡", "deliberate": "Взвешенный ⚖️"}
            
            text = (
                f"*Твой психографический профиль*\n\n"
                f"🎯 Тип восприятия: {vak_map.get(profile.get('vak_type', ''), '—')}\n"
                f"⚡ Реакция на стресс: {stress_map.get(profile.get('stress_response', ''), '—')}\n"
                f"🧠 Стиль решений: {decision_map.get(profile.get('decision_style', ''), '—')}\n"
                f"🏷 Теги: {tags_text}\n"
            )
            
            await message.answer(text, parse_mode="Markdown")
            
        except Exception:
            await message.answer("Не удалось загрузить профиль. Попробуй позже.")


async def main():
    print("🤖 Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
