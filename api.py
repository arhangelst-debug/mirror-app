"""
СИСТЕМА "ЗЕРКАЛО" — Backend (FastAPI)
=====================================
Запуск: uvicorn api:app --reload
"""

import os
import json
import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Mirror System API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Клиенты
supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_KEY")
)
claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


# ================================================
# СХЕМЫ
# ================================================

class UserInfo(BaseModel):
    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None

class SubmitAnswers(BaseModel):
    session_id: str
    user: UserInfo
    test_slug: str
    answers: dict  # {"question_id": "answer_id", ...}


# ================================================
# ЭНДПОИНТЫ
# ================================================

@app.get("/test/{slug}")
async def get_test(slug: str):
    """Получить тест с вопросами по slug"""
    # Получаем тест
    test_resp = supabase.table("tests")\
        .select("*")\
        .eq("slug", slug)\
        .eq("is_active", True)\
        .single()\
        .execute()
    
    if not test_resp.data:
        raise HTTPException(404, "Тест не найден")
    
    test = test_resp.data
    
    # Получаем вопросы
    questions_resp = supabase.table("questions")\
        .select("*")\
        .eq("test_id", test["id"])\
        .order("order_num")\
        .execute()
    
    return {
        "id": test["id"],
        "slug": test["slug"],
        "title": test["title"],
        "description": test["description"],
        "questions": questions_resp.data
    }


@app.post("/session/start")
async def start_session(user: UserInfo, test_slug: str):
    """Создать сессию прохождения теста"""
    # Убедиться что пользователь существует
    supabase.table("users").upsert({
        "id": user.telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }, on_conflict="id").execute()
    
    # Получаем test_id
    test_resp = supabase.table("tests")\
        .select("id")\
        .eq("slug", test_slug)\
        .single()\
        .execute()
    
    if not test_resp.data:
        raise HTTPException(404, "Тест не найден")
    
    # Создаём сессию
    session_resp = supabase.table("sessions").insert({
        "user_id": user.telegram_id,
        "test_id": test_resp.data["id"],
        "answers": {},
        "status": "pending"
    }).execute()
    
    return {"session_id": session_resp.data[0]["id"]}


@app.post("/submit")
async def submit_answers(data: SubmitAnswers):
    """Принять ответы и запустить анализ через Claude"""
    
    # 1. Сохраняем ответы в сессию
    supabase.table("sessions").update({
        "answers": data.answers,
        "status": "analyzing"
    }).eq("id", data.session_id).execute()
    
    # 2. Получаем тест с промптом и вопросами
    test_resp = supabase.table("tests")\
        .select("*, questions(*)")\
        .eq("slug", data.test_slug)\
        .single()\
        .execute()
    
    test = test_resp.data
    questions = sorted(test["questions"], key=lambda q: q["order_num"])
    
    # 3. Формируем текст ответов для Claude
    answers_text = []
    for q in questions:
        q_id = q["id"]
        answer_id = data.answers.get(q_id)
        if answer_id and q["options"]:
            # Найти текст ответа по id
            options = q["options"] if isinstance(q["options"], list) else []
            answer_text = next(
                (opt["text"] for opt in options if opt["id"] == answer_id),
                answer_id
            )
            answers_text.append(f"Вопрос: {q['text']}\nОтвет: {answer_text}")
    
    answers_formatted = "\n\n".join(answers_text)
    
    # 4. Отправляем в Claude
    message = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=test["system_prompt"],
        messages=[{
            "role": "user",
            "content": f"Вот ответы пользователя на тест «{test['title']}»:\n\n{answers_formatted}\n\nПроведи анализ и верни JSON."
        }]
    )
    
    raw_response = message.content[0].text
    
    # 5. Парсим JSON ответ
    try:
        result = json.loads(raw_response)
    except json.JSONDecodeError:
        # Пробуем извлечь JSON если Claude добавил текст вокруг
        import re
        match = re.search(r'\{.*\}', raw_response, re.DOTALL)
        if match:
            result = json.loads(match.group())
        else:
            raise HTTPException(500, "Ошибка парсинга ответа от Claude")
    
    user_result = result.get("for_user", "")
    crm_result = result.get("for_crm", {})
    
    # 6. Обновляем сессию с результатами
    supabase.table("sessions").update({
        "user_result": user_result,
        "crm_result": crm_result,
        "status": "analyzed"
    }).eq("id", data.session_id).execute()
    
    # 7. Обновляем профиль пользователя
    supabase.table("users").update({
        "vak_type": crm_result.get("vak_type"),
        "stress_response": crm_result.get("stress_response"),
        "attachment_type": crm_result.get("attachment_type"),
        "decision_style": crm_result.get("decision_style"),
        "anxiety_level": crm_result.get("anxiety_level"),
        "buying_power": crm_result.get("buying_power"),
        "personality_tags": crm_result.get("personality_tags", []),
        "raw_profile": crm_result,
        "updated_at": "NOW()"
    }).eq("id", data.user.telegram_id).execute()
    
    return {
        "status": "ok",
        "for_user": user_result,
        "for_crm": crm_result
    }


@app.get("/user/{telegram_id}/profile")
async def get_user_profile(telegram_id: int):
    """Получить профиль пользователя"""
    resp = supabase.table("users")\
        .select("*, sessions(id, created_at, status, crm_result, tests(title))")\
        .eq("id", telegram_id)\
        .single()\
        .execute()
    
    if not resp.data:
        raise HTTPException(404, "Пользователь не найден")
    
    return resp.data


@app.get("/health")
async def health():
    return {"status": "ok"}
