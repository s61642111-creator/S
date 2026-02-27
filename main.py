#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Quiz Master Pro 2026 - النسخة الكاملة في ملف واحد
تعمل مع python-telegram-bot v21.6 و SQLAlchemy
جميع الحقوق محفوظة
"""

import asyncio
import logging
import re
import json
import tempfile
import os
import datetime
from datetime import datetime as dt, timedelta, timezone
from typing import List, Optional, Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, WebAppInfo
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)
from telegram.constants import ParseMode

# SQLAlchemy
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, JSON, Text, select, func, or_
from sqlalchemy.orm import declarative_base

# ==================== الإعدادات ====================
DATABASE_URL = "sqlite+aiosqlite:///quiz_data.db"
# إعداد التسجيل
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== نماذج قاعدة البيانات ====================
Base = declarative_base()

class Question(Base):
    __tablename__ = "questions"
    id = Column(Integer, primary_key=True)
    text = Column(Text, nullable=False)
    options = Column(JSON, default=list)
    correct_index = Column(Integer, default=-1)          # -1 يعني غير معروف
    explanation = Column(Text, nullable=True)
    tags = Column(JSON, default=list)
    priority = Column(String(10), default="normal")      # urgent, normal, low
    ease_factor = Column(Float, default=2.5)
    interval = Column(Integer, default=0)                # الأيام حتى المراجعة القادمة
    next_review = Column(DateTime, nullable=True)
    total_reviews = Column(Integer, default=0)
    wrong_count = Column(Integer, default=0)
    streak = Column(Integer, default=0)                  # الإجابات الصحيحة المتتالية
    auto_captured = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: dt.now(timezone.utc))
    review_dates = Column(JSON, default=list)            # تواريخ المراجعات

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

# إنشاء المحرك والجلسة
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# ==================== دوال قاعدة البيانات ====================
class Database:
    @staticmethod
    async def add_question(question: Question) -> int:
        async with async_session() as session:
            session.add(question)
            await session.commit()
            await session.refresh(question)
            return question.id

    @staticmethod
    async def get_question(qid: int) -> Optional[Question]:
        async with async_session() as session:
            return await session.get(Question, qid)

    @staticmethod
    async def update_question(question: Question):
        async with async_session() as session:
            session.add(question)
            await session.commit()

    @staticmethod
    async def delete_question(qid: int) -> bool:
        async with async_session() as session:
            q = await session.get(Question, qid)
            if q:
                await session.delete(q)
                await session.commit()
                return True
            return False

    @staticmethod
    async def all_questions() -> List[Question]:
        async with async_session() as session:
            result = await session.execute(select(Question).order_by(Question.id))
            return result.scalars().all()

    @staticmethod
    async def get_stats() -> Dict[str, Any]:
        async with async_session() as session:
            total = await session.scalar(select(func.count(Question.id))) or 0
            due = await session.scalar(
                select(func.count(Question.id)).where(
                    Question.next_review <= dt.now(timezone.utc)
                )
            ) or 0
            urgent = await session.scalar(select(func.count(Question.id)).where(Question.priority == "urgent")) or 0
            normal = await session.scalar(select(func.count(Question.id)).where(Question.priority == "normal")) or 0
            low = await session.scalar(select(func.count(Question.id)).where(Question.priority == "low")) or 0
            auto = await session.scalar(select(func.count(Question.id)).where(Question.auto_captured == True)) or 0
            avg_ease = await session.scalar(select(func.avg(Question.ease_factor))) or 0.0
            return {
                "total": total,
                "due": due,
                "by_priority": {"urgent": urgent, "normal": normal, "low": low},
                "auto_captured": auto,
                "avg_ease": round(avg_ease, 2)
            }

    @staticmethod
    async def get_weakest(limit: int = 5) -> List[Question]:
        async with async_session() as session:
            result = await session.execute(
                select(Question)
                .where(Question.total_reviews > 0)
                .order_by(Question.wrong_count.desc())
                .limit(limit)
            )
            return result.scalars().all()

    @staticmethod
    async def search(term: str) -> List[Question]:
        async with async_session() as session:
            pattern = f"%{term}%"
            result = await session.execute(
                select(Question).where(
                    or_(Question.text.ilike(pattern), Question.options.ilike(pattern))
                ).limit(20)
            )
            return result.scalars().all()

    @staticmethod
    async def get_all_tags() -> List[str]:
        async with async_session() as session:
            result = await session.execute(select(Question.tags))
            tags_set = set()
            for row in result:
                tags_set.update(row[0])
            return sorted(tags_set)

    @staticmethod
    async def clear_all():
        async with async_session() as session:
            await session.execute(Question.__table__.delete())
            await session.commit()

db = Database()

# ==================== خوارزمية SM-2 والتحليلات ====================
def calculate_streak(review_dates: List[str]) -> int:
    if not review_dates:
        return 0
    dates = sorted(set(dt.fromisoformat(d).date() for d in review_dates))
    max_streak = 1
    current = 1
    for i in range(1, len(dates)):
        if (dates[i] - dates[i-1]).days == 1:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 1
    return max_streak

def get_level_info(total_reviews: int):
    levels = [
        (0, "مبتدئ", "🌱"),
        (10, "نشيط", "🌿"),
        (25, "مجتهد", "🍀"),
        (50, "خبير", "🏅"),
        (100, "محترف", "👑"),
        (250, "أسطورة", "🔥"),
        (500, "عبقري", "💎"),
    ]
    level_name = "مبتدئ"
    badge = "🌱"
    xp = total_reviews
    next_level_xp = 10
    for i, (threshold, name, badge_char) in enumerate(levels):
        if xp >= threshold:
            level_name = name
            badge = badge_char
            if i+1 < len(levels):
                next_level_xp = levels[i+1][0]
            else:
                next_level_xp = threshold + 100
    # حساب التقدم
    current_threshold = 0
    for th, nm, _ in levels:
        if nm == level_name:
            current_threshold = th
            break
    xp_progress = xp - current_threshold
    xp_needed = next_level_xp - current_threshold
    xp_percent = min(10, int((xp_progress / xp_needed) * 10)) if xp_needed else 10
    return {
        "level": level_name,
        "badge": badge,
        "xp": xp_progress,
        "xp_needed": xp_needed,
        "xp_percent": xp_percent,
        "total_reviews": total_reviews
    }

def get_next_question(questions: List[Question], mode: str = "all", tag: Optional[str] = None, exclude_id: Optional[int] = None) -> Optional[Question]:
    now = dt.now(timezone.utc)
    if mode == "due":
        filtered = [q for q in questions if q.next_review and q.next_review <= now]
    elif mode == "weak":
        filtered = [q for q in questions if q.total_reviews > 0 and (q.wrong_count / q.total_reviews) > 0.3]
    elif mode == "tag" and tag:
        filtered = [q for q in questions if tag in q.tags]
    else:
        filtered = questions
    if exclude_id:
        filtered = [q for q in filtered if q.id != exclude_id]
    if not filtered:
        return None
    def sort_key(q):
        due_score = 0 if q.next_review and q.next_review <= now else 1
        weak_score = - (q.wrong_count / max(q.total_reviews, 1))
        return (due_score, weak_score, -q.id)
    filtered.sort(key=sort_key)
    return filtered[0]

def sm2_review(question: Question, quality: int) -> Question:
    if quality < 0 or quality > 5:
        raise ValueError("quality must be 0-5")
    now = dt.now(timezone.utc)
    question.total_reviews += 1
    question.review_dates.append(now.isoformat())
    if quality >= 3:
        question.streak += 1
    else:
        question.streak = 0
    if quality < 3:
        question.wrong_count += 1
    if quality >= 3:
        new_ef = question.ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        question.ease_factor = max(1.3, min(2.5, new_ef))
        if question.interval == 0:
            question.interval = 1
        elif question.interval == 1:
            question.interval = 6
        else:
            question.interval = round(question.interval * question.ease_factor)
        question.next_review = now + timedelta(days=question.interval)
    else:
        question.interval = 1
        question.next_review = now + timedelta(days=1)
        question.ease_factor = max(1.3, question.ease_factor)
    return question

def predict_score(questions: List[Question]) -> Dict[str, Any]:
    if not questions:
        return {"overall": 0, "confidence": "منخفض"}
    total_reviews = sum(q.total_reviews for q in questions)
    avg_ease = sum(q.ease_factor for q in questions) / len(questions)
    wrong_ratio = sum(q.wrong_count for q in questions) / max(total_reviews, 1)
    base = avg_ease / 2.5 * 100
    penalty = wrong_ratio * 50
    predicted = max(0, min(100, base - penalty))
    if total_reviews < 10:
        conf = "منخفض"
    elif total_reviews < 50:
        conf = "متوسط"
    else:
        conf = "مرتفع"
    return {"overall": round(predicted, 1), "confidence": conf}

# ==================== دوال مساعدة ====================
def clean_text(raw: str) -> str:
    lines = raw.splitlines()
    cleaned = []
    for line in lines:
        s = line.strip()
        if not s:
            cleaned.append(line)
            continue
        if any(ch in s for ch in ["⏳", "⌛", "⏱", "⏰"]):
            continue
        low = s.lower()
        if any(kw in low for kw in ["ثانية", "ثوان", "الوقت المتبقي", "time left", "sec"]):
            if len(s) <= 30:
                continue
        if re.fullmatch(r"[0-9]{1,2}[:\.][0-9]{1,2}", s):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)

def extract_options(text: str):
    lines = text.splitlines()
    question_lines = []
    options = []
    correct_index = -1
    patterns = [r'^[أ-هأ-ي]\s*[\)\-.]+', r'^[a-zA-Z]\)', r'^\d+\)']
    for line in lines:
        stripped = line.strip()
        is_opt = False
        for pat in patterns:
            if re.match(pat, stripped):
                options.append(stripped)
                is_opt = True
                break
        if not is_opt:
            question_lines.append(line)
    for line in lines:
        if "الإجابة الصحيحة" in line or "✅" in line:
            match = re.search(r'[أ-هأ-ي]', line)
            if match:
                correct_index = ord(match.group()) - ord('أ')
            else:
                match = re.search(r'\d+', line)
                if match:
                    correct_index = int(match.group()) - 1
    return "\n".join(question_lines), options, correct_index

def priority_text(p: str) -> str:
    return {"urgent": "🔥 عاجل", "normal": "⚡ متوسط", "low": "📖 عادي"}.get(p, p)

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 إضافة سؤال يدوي", callback_data="menu_add")],
        [InlineKeyboardButton("🧠 مراجعة عامة", callback_data="menu_quiz_all")],
        [InlineKeyboardButton("📆 مراجعة اليوم", callback_data="menu_quiz_due"),
         InlineKeyboardButton("❗ نقاط الضعف", callback_data="menu_quiz_weak")],
        [InlineKeyboardButton("🏷️ مراجعة حسب المادة", callback_data="menu_quiz_tag")],
        [InlineKeyboardButton("📋 آخر الأسئلة", callback_data="menu_list"),
         InlineKeyboardButton("🔍 بحث", callback_data="menu_search")],
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="menu_stats"),
         InlineKeyboardButton("🏆 مستواك", callback_data="menu_level")],
        [InlineKeyboardButton("🌐 فتح Mini App", web_app=WebAppInfo(url="https://your-mini-app.com"))],
        [InlineKeyboardButton("📤 تصدير", callback_data="menu_export"),
         InlineKeyboardButton("🗑️ مسح الكل", callback_data="menu_clear")],
    ])

# ==================== حالات المحادثة ====================
ADD_TEXT, ADD_PRIO, ADD_TAGS = range(3)

# ==================== المعالجات (Handlers) ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("⛔ غير مصرح.")
        return
    all_q = await db.all_questions()
    stats = await db.get_stats()
    dates = []
    for q in all_q:
        dates.extend(q.review_dates)
    streak = calculate_streak(dates)
    total_rev = sum(q.total_reviews for q in all_q)
    level = get_level_info(total_rev)
    text = (
        f"🤖 *Quiz Master Pro 2026*\n\n"
        f"{level['badge']} المستوى *{level['level']}* | التقدم: {'█'*level['xp_percent']}{'░'*(10-level['xp_percent'])}\n"
        f"🔥 السلسلة: *{streak}* يوم | ⏰ مستحقة: *{stats['due']}*\n"
        f"🤖 التقطها الصائد: *{stats['auto_captured']}*\n\n"
        f"📌 أرسل أي سؤال أو استخدم /help"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=main_keyboard(), parse_mode=ParseMode.MARKDOWN)
    else:
        await update.callback_query.edit_message_text(text, reply_markup=main_keyboard(), parse_mode=ParseMode.MARKDOWN)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    text = (
        "🛟 *شرح الأوامر*\n\n"
        "/start - القائمة الرئيسية\n"
        "/help - هذه المساعدة\n"
        "/wrong (بالرد على رسالة) - حفظ السؤال كخطأ\n"
        "/weak - مراجعة الأسئلة الضعيفة\n"
        "/today - ملخص اليوم\n"
        "/search كلمة - بحث في الأسئلة\n"
        "/delete رقم - حذف سؤال\n"
        "/tag رقم وسم - إضافة وسم\n"
        "/list - آخر الأسئلة\n"
        "/ping - اختبار الاتصال"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    await update.message.reply_text("🏓 بونغ! البوت شغال 🚀")

# ==================== إضافة يدوية (محادثة) ====================
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📝 *إضافة سؤال يدوي*\n\nأرسل نص السؤال (يمكن أن يتضمن خيارات بصيغة أ) ب) ...):",
        parse_mode=ParseMode.MARKDOWN
    )
    return ADD_TEXT

async def add_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text
    cleaned = clean_text(raw)
    q_text, options, correct_idx = extract_options(cleaned)
    context.user_data["question"] = {
        "text": q_text,
        "options": options,
        "correct_index": correct_idx
    }
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 عاجل", callback_data="prio_urgent")],
        [InlineKeyboardButton("⚡ متوسط", callback_data="prio_normal")],
        [InlineKeyboardButton("📖 عادي", callback_data="prio_low")],
    ])
    await update.message.reply_text("اختر مستوى الأهمية:", reply_markup=kb)
    return ADD_PRIO

async def add_prio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    priority = query.data.replace("prio_", "")
    context.user_data["question"]["priority"] = priority
    await query.edit_message_text("🏷️ أرسل وسم المادة (مثال: قدرات) أو /skip للتخطي:")
    return ADD_TAGS

async def add_tags(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    tags = [t.strip() for t in text.split(",")] if text and not text.startswith("/") else []
    q_data = context.user_data["question"]
    q = Question(
        text=q_data["text"],
        options=q_data.get("options", []),
        correct_index=q_data.get("correct_index", -1),
        priority=q_data.get("priority", "normal"),
        tags=tags
    )
    qid = await db.add_question(q)
    await update.message.reply_text(
        f"✅ *تم الحفظ!* #️⃣{qid}\nوسوم: {', '.join(tags) if tags else 'بدون'}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_keyboard()
    )
    return ConversationHandler.END

async def add_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q_data = context.user_data["question"]
    q = Question(
        text=q_data["text"],
        options=q_data.get("options", []),
        correct_index=q_data.get("correct_index", -1),
        priority=q_data.get("priority", "normal"),
        tags=[]
    )
    qid = await db.add_question(q)
    await update.message.reply_text(
        f"✅ *تم الحفظ!* #️⃣{qid} (بدون وسوم)",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_keyboard()
    )
    return ConversationHandler.END

async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم الإلغاء.", reply_markup=main_keyboard())
    return ConversationHandler.END

# تعريف ConversationHandler مع per_message=True
add_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(add_start, pattern="^menu_add$")],
    states={
        ADD_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_text)],
        ADD_PRIO: [CallbackQueryHandler(add_prio, pattern="^prio_")],
        ADD_TAGS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, add_tags),
            CommandHandler("skip", add_skip)
        ],
    },
    fallbacks=[CommandHandler("cancel", add_cancel)],
    allow_reentry=True,
)

# ==================== معالجات الكويز ====================
async def _send_question(target, question: Question, context: ContextTypes.DEFAULT_TYPE):
    tags_text = f" [{', '.join(question.tags)}]" if question.tags else ""
    auto_text = " 🤖" if question.auto_captured else ""
    keyboard = []
    labels = ['أ', 'ب', 'ج', 'د', 'هـ', 'و']
    for i, opt in enumerate(question.options[:4]):
        btn_text = f"{labels[i]}) {opt[:40]}..." if len(opt) > 40 else f"{labels[i]}) {opt}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"opt_{question.id}_{i}")])
    keyboard.append([
        InlineKeyboardButton("⏭ تخطي", callback_data=f"skip_{question.id}"),
        InlineKeyboardButton("⏹ إنهاء", callback_data="end_quiz")
    ])
    text = (
        f"🧠 *مراجعة*\n"
        f"#{question.id} | {priority_text(question.priority)}{tags_text}{auto_text}\n"
        f"📊 سهولة: {question.ease_factor:.1f} | ❌ أخطاء: {question.wrong_count}\n\n"
        f"{question.text}"
    )
    if hasattr(target, 'edit_message_text'):
        await target.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    else:
        await target.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

async def _start_quiz(update, context, mode, tag=None):
    query = update.callback_query
    await query.answer()
    context.user_data["quiz_mode"] = mode
    context.user_data["quiz_tag"] = tag
    all_q = await db.all_questions()
    next_q = get_next_question(all_q, mode=mode, tag=tag)
    if not next_q:
        msgs = {"all": "📭 لا توجد أسئلة.", "due": "📆 لا توجد أسئلة مستحقة.", "weak": "💪 لا توجد أسئلة ضعيفة."}
        await query.edit_message_text(msgs.get(mode, "📭"), reply_markup=main_keyboard())
        return
    context.user_data["current_qid"] = next_q.id
    await _send_question(query, next_q, context)

async def menu_quiz_all(update, context): await _start_quiz(update, context, "all")
async def menu_quiz_due(update, context): await _start_quiz(update, context, "due")
async def menu_quiz_weak(update, context): await _start_quiz(update, context, "weak")

async def menu_quiz_tag(update, context):
    query = update.callback_query
    await query.answer()
    tags = await db.get_all_tags()
    if not tags:
        await query.edit_message_text("🏷️ لا توجد وسوم.", reply_markup=main_keyboard())
        return
    buttons = [[InlineKeyboardButton(f"🏷️ {t}", callback_data=f"tag_{t}")] for t in tags]
    buttons.append([InlineKeyboardButton("❌ إلغاء", callback_data="menu_back")])
    await query.edit_message_text("🏷️ اختر المادة:", reply_markup=InlineKeyboardMarkup(buttons))

async def quiz_tag_selected(update, context):
    query = update.callback_query
    await query.answer()
    tag = query.data.replace("tag_", "")
    await _start_quiz(update, context, "tag", tag)

async def quiz_option(update, context):
    query = update.callback_query
    await query.answer()
    _, qid_str, opt_idx_str = query.data.split('_')
    qid, selected_idx = int(qid_str), int(opt_idx_str)
    question = await db.get_question(qid)
    if not question:
        await query.edit_message_text("❌ السؤال غير موجود.")
        return
    correct = (selected_idx == question.correct_index)
    quality = 5 if correct else 0
    updated = sm2_review(question, quality)
    await db.update_question(updated)
    result = "✅ *صحيح!* 👏" if correct else "❌ *خطأ!* 📚"
    if not correct and question.explanation:
        result += f"\n\n💡 *الشرح:* {question.explanation}"
    rating_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Again (0)", callback_data=f"rate_{qid}_0")],
        [InlineKeyboardButton("⚡ Hard (3)", callback_data=f"rate_{qid}_3")],
        [InlineKeyboardButton("✅ Good (4)", callback_data=f"rate_{qid}_4")],
        [InlineKeyboardButton("🌟 Easy (5)", callback_data=f"rate_{qid}_5")],
        [InlineKeyboardButton("⏭ التالي", callback_data="next_question")]
    ])
    await query.edit_message_text(result, reply_markup=rating_kb, parse_mode=ParseMode.MARKDOWN)
    context.user_data["last_question_id"] = qid

async def quiz_rate(update, context):
    query = update.callback_query
    await query.answer()
    _, qid_str, quality_str = query.data.split('_')
    qid, quality = int(qid_str), int(quality_str)
    question = await db.get_question(qid)
    if question:
        updated = sm2_review(question, quality)
        await db.update_question(updated)
    await next_question(update, context)

async def next_question(update, context):
    query = update.callback_query
    mode = context.user_data.get("quiz_mode", "all")
    tag = context.user_data.get("quiz_tag")
    exclude = context.user_data.get("current_qid")
    all_q = await db.all_questions()
    next_q = get_next_question(all_q, mode=mode, tag=tag, exclude_id=exclude)
    if not next_q:
        await query.edit_message_text("🎉 انتهت المراجعة! أحسنت.", reply_markup=main_keyboard())
        context.user_data.clear()
        return
    context.user_data["current_qid"] = next_q.id
    await _send_question(query, next_q, context)

async def quiz_skip(update, context):
    query = update.callback_query
    await query.answer()
    await next_question(update, context)

async def quiz_end(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✅ تم إنهاء المراجعة.", reply_markup=main_keyboard())
    context.user_data.clear()

# ==================== معالجات القوائم الأخرى ====================
async def menu_list(update, context):
    query = update.callback_query
    await query.answer()
    all_q = await db.all_questions()
    if not all_q:
        await query.edit_message_text("📭 لا توجد أسئلة.", reply_markup=main_keyboard())
        return
    last = sorted(all_q, key=lambda x: x.id, reverse=True)[:10]
    lines = ["📋 *آخر 10 أسئلة:*\n"]
    for q in last:
        short = q.text.replace("\n", " ")[:70] + ("..." if len(q.text) > 70 else "")
        lines.append(f"*#{q.id}*{'🤖' if q.auto_captured else ''} — {short}")
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())

async def menu_search(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔍 أرسل `/search كلمة`", parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())

async def menu_stats(update, context):
    query = update.callback_query
    await query.answer()
    stats = await db.get_stats()
    all_q = await db.all_questions()
    pred = predict_score(all_q)
    tags = await db.get_all_tags()
    text = (
        f"📊 *إحصائيات شاملة*\n\n"
        f"📌 إجمالي: *{stats['total']}* | ⏰ مستحقة: *{stats['due']}*\n"
        f"🔥 عاجل: {stats['by_priority']['urgent']} | ⚡ متوسط: {stats['by_priority']['normal']} | 📖 عادي: {stats['by_priority']['low']}\n"
        f"🤖 صائد: *{stats['auto_captured']}*\n"
        f"📈 متوسط ease: *{stats['avg_ease']}*\n\n"
        f"📊 درجة متوقعة: *{pred['overall']}%* (ثقة {pred['confidence']})\n"
        f"🏷️ الوسوم: {', '.join(tags) if tags else 'لا يوجد'}"
    )
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())

async def menu_level(update, context):
    query = update.callback_query
    await query.answer()
    all_q = await db.all_questions()
    total = sum(q.total_reviews for q in all_q)
    dates = []
    for q in all_q:
        dates.extend(q.review_dates)
    streak = calculate_streak(dates)
    level = get_level_info(total)
    bar = "█" * level["xp_percent"] + "░" * (10 - level["xp_percent"])
    text = (
        f"{level['badge']} *المستوى: {level['level']}*\n\n"
        f"التقدم: [{bar}] {level['xp']}/{level['xp_needed']}\n"
        f"🔁 إجمالي المراجعات: *{total}*\n"
        f"🔥 السلسلة الحالية: *{streak}* يوم"
    )
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())

async def menu_export(update, context):
    query = update.callback_query
    await query.answer()
    qs = await db.all_questions()
    if not qs:
        await query.edit_message_text("📂 لا توجد بيانات.", reply_markup=main_keyboard())
        return
    await query.edit_message_text("📤 جاري التصدير...")
    data = [q.to_dict() for q in qs]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        tmp = f.name
    with open(tmp, "rb") as f:
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=InputFile(f, filename=f"quiz_backup_{dt.now(timezone.utc).strftime('%Y%m%d')}.json"),
            caption="📦 نسخة احتياطية"
        )
    os.unlink(tmp)
    await context.bot.send_message(chat_id=query.message.chat_id, text="اختر:", reply_markup=main_keyboard())

async def menu_clear(update, context):
    query = update.callback_query
    await query.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️ نعم، امسح", callback_data="clear_yes")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="clear_no")]
    ])
    await query.edit_message_text("⚠️ *هل تريد مسح جميع الأسئلة؟*", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def clear_decision(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "clear_yes":
        await db.clear_all()
        await query.edit_message_text("🗑️ تم مسح جميع الأسئلة.")
    else:
        await query.edit_message_text("❌ تم الإلغاء.")
    await query.message.reply_text("اختر:", reply_markup=main_keyboard())

async def menu_back(update, context):
    await start(update, context)

# ==================== معالجات الرسائل العامة ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    msg = update.message
    if not msg or not msg.text:
        return
    raw = msg.text
    fwd = bool(msg.forward_date)
    is_error = any(marker in raw.lower() for marker in ["#خطأ", "#غلط", "#weak", "#ضعيف"])
    cleaned = clean_text(raw)
    if is_error:
        for marker in ["#خطأ", "#غلط", "#weak", "#ضعيف"]:
            cleaned = cleaned.replace(marker, "").strip()
    q_text, options, correct_idx = extract_options(cleaned)
    q = Question(
        text=q_text,
        options=options,
        correct_index=correct_idx,
        priority="urgent" if (fwd or is_error) else "normal",
        tags=["weak"] if is_error else [],
        auto_captured=fwd
    )
    qid = await db.add_question(q)
    reply = f"✅ *تم حفظ السؤال!*\n🆔 #{qid} | {priority_text(q.priority)}"
    if is_error:
        reply += "\n🏷️ وسم: weak"
    reply += f"\n💡 `/tag {qid} قدرات`"
    await msg.reply_text(reply, parse_mode=ParseMode.MARKDOWN)

async def handle_poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    msg = update.message
    poll = msg.poll
    if not poll:
        return
    caption = msg.caption or ""
    is_error = any(marker in caption.lower() for marker in ["#خطأ", "#غلط", "#weak", "#ضعيف"])
    labels = ["أ", "ب", "ج", "د", "هـ", "و"]
    lines = [poll.question]
    opts = []
    for i, opt in enumerate(poll.options):
        lbl = labels[i] if i < len(labels) else str(i+1)
        lines.append(f"{lbl}) {opt.text}")
        opts.append(opt.text)
    cidx = poll.correct_option_id if poll.correct_option_id is not None else -1
    if cidx != -1 and cidx < len(poll.options):
        cl = labels[cidx] if cidx < len(labels) else str(cidx+1)
        lines.append(f"\n✅ الإجابة الصحيحة: {cl}) {poll.options[cidx].text}")
    q = Question(
        text="\n".join(lines),
        options=opts,
        correct_index=cidx,
        priority="urgent",
        tags=["weak"] if is_error else []
    )
    qid = await db.add_question(q)
    reply = f"✅ *تم حفظ الاستطلاع #{qid}!*"
    if is_error:
        reply += "\n🏷️ وسم: weak"
    reply += f"\n💡 `/tag {qid} قدرات`"
    await msg.reply_text(reply, parse_mode=ParseMode.MARKDOWN)

# ==================== أوامر إضافية ====================
async def wrong_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ يجب الرد على رسالة.")
        return
    replied = update.message.reply_to_message
    raw = replied.text or replied.caption
    if not raw:
        await update.message.reply_text("❌ الرسالة لا تحتوي على نص.")
        return
    cleaned = clean_text(raw)
    q_text, options, correct_idx = extract_options(cleaned)
    q = Question(
        text=q_text,
        options=options,
        correct_index=correct_idx,
        priority="urgent",
        tags=["weak"],
        auto_captured=True
    )
    qid = await db.add_question(q)
    await update.message.reply_text(f"✅ *تم حفظ السؤال كـ \"ضعيف\"!*\n🆔 #{qid}", parse_mode=ParseMode.MARKDOWN)

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    if not context.args:
        await update.message.reply_text("اكتب: `/search كلمة`", parse_mode=ParseMode.MARKDOWN)
        return
    term = " ".join(context.args)
    results = await db.search(term)
    if not results:
        await update.message.reply_text("❌ لا نتائج.")
        return
    lines = [f"🔍 *نتائج البحث عن:* {term}\n"]
    for q in results[:15]:
        short = q.text.replace("\n", " ")[:70] + ("..." if len(q.text) > 70 else "")
        lines.append(f"*#{q.id}* — {short}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    if not context.args:
        await update.message.reply_text("اكتب: `/delete رقم`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        qid = int(context.args[0])
    except:
        await update.message.reply_text("❌ رقم غير صحيح.")
        return
    ok = await db.delete_question(qid)
    await update.message.reply_text(f"🗑️ تم حذف #{qid}." if ok else "❌ غير موجود.")

async def tag_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    if len(context.args) < 2:
        await update.message.reply_text("اكتب: `/tag رقم وسم`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        qid = int(context.args[0])
    except:
        await update.message.reply_text("❌ رقم غير صحيح.")
        return
    tag = context.args[1].strip()
    q = await db.get_question(qid)
    if not q:
        await update.message.reply_text("❌ غير موجود.")
        return
    if tag not in q.tags:
        q.tags.append(tag)
        await db.update_question(q)
    await update.message.reply_text(f"🏷️ تمت إضافة الوسم *{tag}* للسؤال #{qid}.", parse_mode=ParseMode.MARKDOWN)

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    all_q = await db.all_questions()
    if not all_q:
        await update.message.reply_text("📭 لا توجد أسئلة.")
        return
    last = sorted(all_q, key=lambda x: x.id, reverse=True)[:10]
    lines = ["📋 *آخر 10 أسئلة:*\n"]
    for q in last:
        short = q.text.replace("\n", " ")[:70] + ("..." if len(q.text) > 70 else "")
        lines.append(f"*#{q.id}*{'🤖' if q.auto_captured else ''} — {short}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

async def weak_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    weak = await db.get_weakest(5)
    if not weak:
        await update.message.reply_text("❗ لا توجد أسئلة ضعيفة بعد.")
        return
    lines = ["❗ *أضعف 5 أسئلة:*\n"]
    for q in weak:
        short = q.text.replace("\n", " ")[:60] + ("..." if len(q.text) > 60 else "")
        lines.append(f"*#{q.id}* ease:{q.ease_factor:.1f} | خطأ:{q.wrong_count}x\n  {short}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    stats = await db.get_stats()
    all_q = await db.all_questions()
    total = sum(q.total_reviews for q in all_q)
    dates = []
    for q in all_q:
        dates.extend(q.review_dates)
    streak = calculate_streak(dates)
    level = get_level_info(total)
    pred = predict_score(all_q)
    text = (
        f"📅 *ملخص اليوم*\n\n"
        f"{level['badge']} المستوى {level['level']} | التقدم: {level['xp']}/{level['xp_needed']}\n"
        f"🔥 السلسلة: *{streak}* يوم\n"
        f"📌 الإجمالي: *{stats['total']}* | ⏰ المستحقة: *{stats['due']}*\n"
        f"🤘 الصائد: *{stats['auto_captured']}*\n\n"
        f"📊 *درجتك المتوقعة: {pred['overall']}%* (ثقة {pred['confidence']})"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ==================== المهام المجدولة ====================
async def daily_report_job(context: ContextTypes.DEFAULT_TYPE):
    stats = await db.get_stats()
    all_q = await db.all_questions()
    total = sum(q.total_reviews for q in all_q)
    dates = []
    for q in all_q:
        dates.extend(q.review_dates)
    streak = calculate_streak(dates)
    level = get_level_info(total)
    pred = predict_score(all_q)
    text = (
        f"☀️ *تقريرك اليومي*\n\n"
        f"{level['badge']} المستوى: {level['level']}\n"
        f"🔥 السلسلة: {streak} يوم\n"
        f"⏰ أسئلة مستحقة اليوم: {stats['due']}\n"
        f"📊 الدرجة المتوقعة: {pred['overall']}%\n\n"
        f"{'💪 حان وقت المراجعة!' if stats['due'] > 0 else '✅ كل شيء على ما يرام!'}"
    )
    try:
        await context.bot.send_message(chat_id=ALLOWED_USER_ID, text=text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())
    except Exception as e:
        logger.error(f"فشل التقرير اليومي: {e}")

# ==================== معالج الأخطاء ====================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("استثناء:", exc_info=context.error)
    if update and hasattr(update, "effective_chat") and update.effective_chat:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ حدث خطأ داخلي.")

# ==================== الإعداد والتشغيل النهائي (بدون مشاكل حلقة الأحداث) ====================
import nest_asyncio
nest_asyncio.apply()  # يسمح بتداخل الحلقات (آمن)

async def post_init(app):
    app.bot_data["allowed_user_id"] = ALLOWED_USER_ID
    if app.job_queue:
        app.job_queue.run_daily(
            daily_report_job,
            time=datetime.time(hour=DAILY_REPORT_HOUR, minute=DAILY_REPORT_MINUTE),
            name="daily_report"
        )
    logger.info("✅ البوت جاهز للعمل!")

async def main():
    # تهيئة قاعدة البيانات
    await init_db()
    
    # بناء التطبيق (بدون post_shutdown لتجنب التعقيدات)
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    
    # إضافة جميع المعالجات (نفس الكود السابق)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("ping", ping_cmd))
    app.add_handler(CommandHandler("wrong", wrong_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("tag", tag_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("weak", weak_cmd))
    app.add_handler(CommandHandler("today", today_cmd))
    
    # محادثة الإضافة
    app.add_handler(add_conv)
    
    # معالجات القوائم
    app.add_handler(CallbackQueryHandler(menu_list, pattern="^menu_list$"))
    app.add_handler(CallbackQueryHandler(menu_search, pattern="^menu_search$"))
    app.add_handler(CallbackQueryHandler(menu_stats, pattern="^menu_stats$"))
    app.add_handler(CallbackQueryHandler(menu_level, pattern="^menu_level$"))
    app.add_handler(CallbackQueryHandler(menu_export, pattern="^menu_export$"))
    app.add_handler(CallbackQueryHandler(menu_clear, pattern="^menu_clear$"))
    app.add_handler(CallbackQueryHandler(clear_decision, pattern="^clear_(yes|no)$"))
    app.add_handler(CallbackQueryHandler(menu_back, pattern="^menu_back$"))
    
    # معالجات الكويز
    app.add_handler(CallbackQueryHandler(menu_quiz_all, pattern="^menu_quiz_all$"))
    app.add_handler(CallbackQueryHandler(menu_quiz_due, pattern="^menu_quiz_due$"))
    app.add_handler(CallbackQueryHandler(menu_quiz_weak, pattern="^menu_quiz_weak$"))
    app.add_handler(CallbackQueryHandler(menu_quiz_tag, pattern="^menu_quiz_tag$"))
    app.add_handler(CallbackQueryHandler(quiz_tag_selected, pattern="^tag_"))
    app.add_handler(CallbackQueryHandler(quiz_option, pattern="^opt_"))
    app.add_handler(CallbackQueryHandler(quiz_rate, pattern="^rate_"))
    app.add_handler(CallbackQueryHandler(quiz_skip, pattern="^skip_"))
    app.add_handler(CallbackQueryHandler(next_question, pattern="^next_question$"))
    app.add_handler(CallbackQueryHandler(quiz_end, pattern="^end_quiz$"))
    
    # معالجات الرسائل
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.POLL, handle_poll))
    
    # معالج الأخطاء
    app.add_error_handler(error_handler)
    
    logger.info("🚀 تشغيل البوت...")
    await app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    asyncio.run(main())