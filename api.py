"""
СИСТЕМА "ЗЕРКАЛО" — Backend (FastAPI)
"""

import os
import json
import re
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

supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_KEY")
)
claude = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


class UserInfo(BaseModel):
    telegram_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None

class SubmitAnswers(BaseModel):
    session_id: str
    user: UserInfo
    test_slug: str
    answers: dict


@app.get("/test/{slug}")
async def get_test(slug: str):
    test_resp = supabase.table("tests")\
        .select("*")\
        .eq("slug", slug)\
        .eq("is_active", True)\
        .single()\
        .execute()
    
    if not test_resp.data:
        raise HTTPException(404, "Тест не найден")
    
    test = test_resp.data
    
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
    supabase.table("users").upsert({
        "id": user.telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }, on_conflict="id").execute()
    
    test_resp = supabase.table("tests")\
        .select("id")\
        .eq("slug", test_slug)\
        .single()\
        .execute()
    
    if not test_resp.data:
        raise HTTPException(404, "Тест не найден")
    
    session_resp = supabase.table("sessions").insert({
        "user_id": user.telegram_id,
        "test_id": test_resp.data["id"],
        "answers": {},
        "status": "pending"
    }).execute()
    
    return {"session_id": session_resp.data[0]["id"]}


@app.post("/submit")
async def submit_answers(data: SubmitAnswers):
    supabase.table("sessions").update({
        "answers": data.answers,
        "status": "analyzing"
    }).eq("id", data.session_id).execute()
    
    test_resp = supabase.table("tests")\
        .select("*, questions(*)")\
        .eq("slug", data.test_slug)\
        .single()\
        .execute()
    
    test = test_resp.data
    questions = sorted(test["questions"], key=lambda q: q["order_num"])
    
    answers_text = []
    for q in questions:
        q_id = q["id"]
        answer_id = data.answers.get(q_id)
        if answer_id and q["options"]:
            options = q["options"] if isinstance(q["options"], list) else []
            answer_text = next(
                (opt["text"] for opt in options if opt["id"] == answer_id),
                answer_id
            )
            answers_text.append(f"Вопрос: {q['text']}\nОтвет: {answer_text}")
    
    answers_formatted = "\n\n".join(answers_text)
    
    # Чистим промпт от спецсимволов
    clean_prompt = test["system_prompt"]
    clean_prompt = clean_prompt.replace('\u2028', '\n')
    clean_prompt = clean_prompt.replace('\u2029', '\n')
    clean_prompt = clean_prompt.replace('\u00ad', '')
    clean_prompt = ''.join(c for c in clean_prompt if ord(c) < 128 or ord(c) >= 160)
    
    message = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=clean_prompt,
        messages=[{
            "role": "user",
            "content": f"Вот ответы пользователя на тест. Проведи анализ и верни JSON.\n\n{answers_formatted}"
        }]
    )
    
    raw_response = message.content[0].text
    
    try:
        result = json.loads(raw_response)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', raw_response, re.DOTALL)
        if match:
            result = json.loads(match.group())
        else:
            raise HTTPException(500, "Ошибка парсинга ответа")
    
    user_result = result.get("for_user", "")
    crm_result = result.get("for_crm", {})
    
    supabase.table("sessions").update({
        "user_result": user_result,
        "crm_result": crm_result,
        "status": "analyzed"
    }).eq("id", data.session_id).execute()
    
    supabase.table("users").update({
        "vak_type": crm_result.get("vak_type"),
        "stress_response": crm_result.get("stress_response"),
        "attachment_type": crm_result.get("attachment_type"),
        "decision_style": crm_result.get("decision_style"),
        "anxiety_level": crm_result.get("anxiety_level"),
        "buying_power": crm_result.get("buying_power"),
        "personality_tags": crm_result.get("personality_tags", []),
        "raw_profile": crm_result,
    }).eq("id", data.user.telegram_id).execute()
    
    return {
        "status": "ok",
        "for_user": user_result,
        "for_crm": crm_result
    }


@app.get("/user/{telegram_id}/profile")
async def get_user_profile(telegram_id: int):
    resp = supabase.table("users")\
        .select("*")\
        .eq("id", telegram_id)\
        .single()\
        .execute()
    
    if not resp.data:
        raise HTTPException(404, "Пользователь не найден")
    
    return resp.data


@app.get("/health")
async def health():
    return {"status": "ok"}
