"""
Mini App API — FastAPI
شغّله: uvicorn mini_app.main:app --host 0.0.0.0 --port 8080 --reload
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone

from core.database import db
from core.quiz_engine import engine, get_next_question, get_level_info
from core.analytics_engine import analytics

app = FastAPI(title="Quiz Master Pro", version="2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

STATIC = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

@app.get("/", response_class=HTMLResponse)
async def root():
    with open(os.path.join(STATIC,"index.html"),"r",encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/api/questions")
async def get_questions(mode: str="all", tag: Optional[str]=None, limit: int=20):
    all_q = await db.all_questions()
    if tag:
        all_q = [q for q in all_q if tag in q.tags]
    now = datetime.now(timezone.utc).isoformat()
    if mode == "due":
        all_q = [q for q in all_q if q.next_review and q.next_review <= now]
    elif mode == "weak":
        all_q = sorted(all_q, key=lambda q: q.ease_factor)
    return [{
        "id": q.id, "text": q.text, "options": q.options,
        "correct_index": q.correct_index, "explanation": q.explanation,
        "tags": q.tags, "priority": q.priority, "ease_factor": q.ease_factor,
        "total_reviews": q.total_reviews, "streak": q.streak,
        "auto_captured": q.auto_captured,
    } for q in all_q[:limit]]

class ReviewPayload(BaseModel):
    question_id: int
    quality: int
    timestamp: Optional[str] = None

@app.post("/api/review")
async def submit_review(p: ReviewPayload):
    q = await db.get_question(p.question_id)
    if not q:
        raise HTTPException(404, "السؤال غير موجود")
    updated = engine.review(q, p.quality)
    await db.update_question(updated)
    return {"success": True, "next_review": updated.next_review,
            "streak": updated.streak, "ease_factor": round(updated.ease_factor,2)}

class SyncPayload(BaseModel):
    items: List[dict]

@app.post("/api/sync")
async def sync_offline(p: SyncPayload):
    synced = 0
    for item in p.items:
        try:
            q = await db.get_question(item["question_id"])
            if q:
                await db.update_question(engine.review(q, item["quality"]))
                synced += 1
        except: continue
    return {"synced": synced, "total": len(p.items)}

@app.get("/api/analytics")
async def get_analytics():
    return analytics.get_full_report(await db.all_questions())

@app.get("/api/stats")
async def get_stats():
    return await db.get_stats()

@app.get("/api/tags")
async def get_tags():
    return await db.get_all_tags()

@app.on_event("startup")
async def startup():
    await db.init()
