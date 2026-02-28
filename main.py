#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Quiz Master Pro 2026 — النسخة النهائية المعدلة للإنتاج
✅ SM-2 مطبق فوراً بعد الإجابة (بدون أزرار تقييم)
✅ عرض الخيارات مع أيقونات ✅❌◻️
✅ استخراج الشرح التلقائي
✅ استخدام datetime.timezone.utc للتوافق
✅ Cache مع قفل (Lock) لمنع السباق
✅ معالج أخطاء شامل
✅ جاهز لـ Railway
"""

import asyncio
import logging
import warnings
from telegram.warnings import PTBUserWarning

# تجاهل تحذيرات ConversationHandler (لا تؤثر على الأداء)
warnings.filterwarnings("ignore", category=PTBUserWarning)

# تجاهل تحذيرات coroutines غير المنتظرة (خاصة ببيئة Railway)
warnings.filterwarnings("ignore", message="coroutine 'Application.*' was never awaited")
import re
import json
import tempfile
import os
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean,
    JSON, Text, select, func, or_, Index
)
from sqlalchemy.orm import declarative_base

# ═══════════════════════════════════════════════════
#  إعدادات البيئة (عدّل القيم عبر Railway Variables)
# ═══════════════════════════════════════════════════
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8242666905:AAHljuGOMBxWmYMsjPzAK0zDL7_tAqEYqeg")
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID", "6782657661"))
DATABASE_URL    = os.environ.get("DATABASE_URL",    "sqlite+aiosqlite:///quiz_data.db")
DAILY_HOUR      = int(os.environ.get("DAILY_REPORT_HOUR",   "5"))
DAILY_MINUTE    = int(os.environ.get("DAILY_REPORT_MINUTE", "0"))

# تحقق من المتغيرات الأساسية
if BOT_TOKEN == "YOUR_TOKEN_HERE" or ALLOWED_USER_ID == 0:
    raise ValueError("❌ BOT_TOKEN و ALLOWED_USER_ID يجب ضبطهما في متغيرات البيئة")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════
#  نموذج قاعدة البيانات
# ═══════════════════════════════════════════════════
Base = declarative_base()

class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (
        Index("ix_next_review", "next_review"),
        Index("ix_priority",    "priority"),
    )
    id            = Column(Integer, primary_key=True, autoincrement=True)
    text          = Column(Text,    nullable=False)
    options       = Column(JSON,    default=list)
    correct_index = Column(Integer, default=-1)
    explanation   = Column(Text,    nullable=True)
    tags          = Column(JSON,    default=list)
    priority      = Column(String(10), default="normal")
    ease_factor   = Column(Float,   default=2.5)
    interval      = Column(Integer, default=0)
    next_review   = Column(DateTime, nullable=True)
    total_reviews = Column(Integer, default=0)
    wrong_count   = Column(Integer, default=0)
    streak        = Column(Integer, default=0)
    auto_captured = Column(Boolean, default=False)
    created_at    = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    review_dates  = Column(JSON,    default=list)

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

engine        = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# ═══════════════════════════════════════════════════
#  Cache ذكي مع قفل (للوقاية من سباق الحالة)
# ═══════════════════════════════════════════════════
class _QuestionCache:
    def __init__(self):
        self._data: List[Question] = []
        self._ts: float = 0.0
        self._lock = asyncio.Lock()
        self.TTL = 30  # ثانية

    def invalidate(self):
        self._ts = 0.0

    async def get(self) -> List[Question]:
        async with self._lock:
            if time.monotonic() - self._ts > self.TTL or not self._data:
                self._data = await _Database.all_questions_raw()
                self._ts = time.monotonic()
            return self._data

_cache = _QuestionCache()

# ═══════════════════════════════════════════════════
#  طبقة قاعدة البيانات
# ═══════════════════════════════════════════════════
class _Database:

    @staticmethod
    async def add_question(q: Question) -> int:
        async with async_session() as s:
            s.add(q)
            await s.commit()
            await s.refresh(q)
        _cache.invalidate()
        return q.id

    @staticmethod
    async def get_question(qid: int) -> Optional[Question]:
        async with async_session() as s:
            return await s.get(Question, qid)

    @staticmethod
    async def update_question(q: Question):
        async with async_session() as s:
            await s.merge(q)
            await s.commit()
        _cache.invalidate()

    @staticmethod
    async def delete_question(qid: int) -> bool:
        async with async_session() as s:
            obj = await s.get(Question, qid)
            if not obj:
                return False
            await s.delete(obj)
            await s.commit()
        _cache.invalidate()
        return True

    @staticmethod
    async def all_questions_raw() -> List[Question]:
        async with async_session() as s:
            res = await s.execute(select(Question).order_by(Question.id))
            return list(res.scalars().all())

    @staticmethod
    async def get_stats() -> Dict[str, Any]:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with async_session() as s:
            total    = await s.scalar(select(func.count(Question.id))) or 0
            due      = await s.scalar(
                select(func.count(Question.id)).where(Question.next_review <= now)
            ) or 0
            urgent   = await s.scalar(select(func.count(Question.id)).where(Question.priority == "urgent"))  or 0
            normal   = await s.scalar(select(func.count(Question.id)).where(Question.priority == "normal"))  or 0
            low      = await s.scalar(select(func.count(Question.id)).where(Question.priority == "low"))     or 0
            auto     = await s.scalar(select(func.count(Question.id)).where(Question.auto_captured == True)) or 0
            avg_ease = await s.scalar(select(func.avg(Question.ease_factor))) or 0.0
        return {
            "total": total, "due": due,
            "by_priority": {"urgent": urgent, "normal": normal, "low": low},
            "auto_captured": auto,
            "avg_ease": round(float(avg_ease), 2),
        }

    @staticmethod
    async def get_weakest(limit: int = 5) -> List[Question]:
        async with async_session() as s:
            res = await s.execute(
                select(Question)
                .where(Question.total_reviews > 0)
                .order_by(Question.wrong_count.desc())
                .limit(limit)
            )
            return list(res.scalars().all())

    @staticmethod
    async def search(term: str) -> List[Question]:
        async with async_session() as s:
            p   = f"%{term}%"
            res = await s.execute(
                select(Question).where(
                    or_(Question.text.ilike(p), Question.options.cast(Text).ilike(p))
                ).limit(20)
            )
            return list(res.scalars().all())

    @staticmethod
    async def get_all_tags() -> List[str]:
        async with async_session() as s:
            res      = await s.execute(select(Question.tags))
            tags_set: set = set()
            for (row,) in res:
                if row:
                    tags_set.update(row)
            return sorted(tags_set)

    @staticmethod
    async def clear_all():
        async with async_session() as s:
            await s.execute(Question.__table__.delete())
            await s.commit()
        _cache.invalidate()

db = _Database()

# ═══════════════════════════════════════════════════
#  extract_options المحسّن (يدعم شرح متعدد الأسطر)
# ═══════════════════════════════════════════════════
_ARABIC_LABELS = {
    "أ": 0, "ا": 0, "ب": 1, "ج": 2, "د": 3,
    "ه": 4, "هـ": 4, "و": 5, "ز": 6, "ح": 7,
}
_OPTION_RE   = re.compile(r"^(?P<label>[أ-يa-zA-Z\d])\s*[\)\-\.–—]\s*(?P<body>.+)", re.UNICODE)
_CORRECT_RE  = re.compile(
    r"(?:الإجابة\s*الصحيحة|الجواب\s*الصحيح|✅)"
    r"[^\u0600-\u06FFa-zA-Z\d]*(?P<label>[أ-يa-zA-Z]|\d+)",
    re.UNICODE,
)
_EXPL_START  = re.compile(r"^(?:شرح|توضيح|ملاحظة|تنبيه|📝|💡|🔍|لأن|السبب|وبالتالي|إذن)", re.UNICODE)
_EXPL_INLINE = re.compile(r"(?:لأن|حيث|بمعنى|يعني|أي أن|وهو|نلاحظ|وبالتالي|لذلك)", re.UNICODE)
_TIMER_RE    = re.compile(r"^[0-9]{1,2}[:\.][0-9]{1,2}$")
_TIMER_KW    = {"ثانية", "ثوان", "الوقت المتبقي", "time left", "sec"}
_LABELS      = ["أ", "ب", "ج", "د", "هـ", "و"]


def _label_to_index(label: str, options: List[str]) -> int:
    """تحويل حرف/رقم → index — يبحث في الخيارات أولاً ثم الخريطة."""
    for i, opt in enumerate(options):
        m = _OPTION_RE.match(opt)
        if m and m.group("label") == label:
            return i
    if label in _ARABIC_LABELS:
        return _ARABIC_LABELS[label]
    if label.isdigit():
        return int(label) - 1
    if label.isascii() and label.isalpha():
        return ord(label.lower()) - ord("a")
    return -1


def clean_text(raw: str) -> str:
    """إزالة المؤقتات والضجيج من النص."""
    result = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            result.append(line); continue
        if any(ch in s for ch in "⏳⌛⏱⏰"):
            continue
        if any(kw in s.lower() for kw in _TIMER_KW) and len(s) <= 30:
            continue
        if _TIMER_RE.fullmatch(s):
            continue
        result.append(line)
    return "\n".join(result)


def extract_options(text: str) -> Tuple[str, List[str], int, Optional[str]]:
    """
    يستخرج: (نص_السؤال, خيارات, index_الإجابة, شرح)
    
    يدعم:
    • أحرف عربية/لاتينية/أرقام مع ) - . – —
    • ✅ داخل سطر الخيار أو في سطر منفصل
    • شرح متعدد الأسطر
    • fallback للخيارات بدون فاصل (أ النص)
    """
    lines           = text.splitlines()
    options         : List[str]  = []
    correct_index   : int        = -1
    option_set      : set[int]   = set()
    correct_line    : int        = -1
    expl_set        : set[int]   = set()

    # ── 1. رصد الخيارات ──────────────────────────────────────────────
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        if _OPTION_RE.match(s):
            if "✅" in s:
                correct_line = i
                clean = re.sub(r"\s*✅.*$", "", s).strip()
                options.append(clean)
                correct_index = len(options) - 1
            else:
                options.append(s)
            option_set.add(i)

    # fallback: "أ النص" بدون فاصل
    if not options:
        _NP = re.compile(r"^(?P<label>[أ-دa-dA-D])\s+(?P<body>\S.+)", re.UNICODE)
        for i, line in enumerate(lines):
            s = line.strip()
            if _NP.match(s):
                options.append(s)
                option_set.add(i)

    # ── 2. الإجابة الصحيحة (سطر منفصل) ──────────────────────────────
    if correct_line == -1:
        for i, line in enumerate(lines):
            if i in option_set:
                continue
            m = _CORRECT_RE.search(line)
            if m:
                correct_line  = i
                correct_index = _label_to_index(m.group("label"), options)
                break
        if correct_line == -1:
            for i, line in enumerate(lines):
                if i in option_set or "✅" not in line:
                    continue
                correct_line = i
                for pat in (r"[أ-ي]", r"[a-zA-Z]", r"\d+"):
                    lm = re.search(pat, line)
                    if lm:
                        correct_index = _label_to_index(lm.group(), options)
                        break
                break

    # ── 3. الشرح (متعدد الأسطر) ──────────────────────────────────────
    excluded   = option_set | ({correct_line} if correct_line != -1 else set())
    expl_parts : List[str] = []
    in_expl    = False

    for i, line in enumerate(lines):
        if i in excluded:
            continue
        s = line.strip()
        if not s:
            if in_expl:
                break
            continue
        if _EXPL_START.match(s) or (not in_expl and _EXPL_INLINE.search(s)):
            in_expl = True
            expl_parts.append(s)
            expl_set.add(i)
        elif in_expl:
            expl_parts.append(s)
            expl_set.add(i)

    explanation = "\n".join(expl_parts).strip() or None

    # ── 4. نص السؤال ─────────────────────────────────────────────────
    all_excl = excluded | expl_set
    q_lines  = [
        lines[i].strip()
        for i in range(len(lines))
        if i not in all_excl and lines[i].strip()
    ]
    return "\n".join(q_lines).strip(), options, correct_index, explanation

# ═══════════════════════════════════════════════════
#  دوال مساعدة
# ═══════════════════════════════════════════════════
def priority_text(p: str) -> str:
    return {"urgent": "🔥 عاجل", "normal": "⚡ متوسط", "low": "📖 عادي"}.get(p, p)


def calculate_streak(review_dates: List[str]) -> int:
    if not review_dates:
        return 0
    dates = sorted({datetime.fromisoformat(d).date() for d in review_dates if d})
    if not dates:
        return 0
    mx = cur = 1
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days == 1:
            cur += 1
            mx   = max(mx, cur)
        else:
            cur = 1
    return mx


def get_level_info(total: int) -> Dict[str, Any]:
    levels = [
        (0,   "مبتدئ",  "🌱"), (10,  "نشيط",   "🌿"),
        (25,  "مجتهد",  "🍀"), (50,  "خبير",   "🏅"),
        (100, "محترف",  "👑"), (250, "أسطورة", "🔥"),
        (500, "عبقري",  "💎"),
    ]
    name = levels[0][1]; badge = levels[0][2]; cur_th = 0; next_th = levels[1][0]
    for i, (th, nm, bg) in enumerate(levels):
        if total >= th:
            name = nm; badge = bg; cur_th = th
            next_th = levels[i + 1][0] if i + 1 < len(levels) else th + 100
    xp       = total - cur_th
    xp_need  = max(next_th - cur_th, 1)
    bar      = min(10, int(xp / xp_need * 10))
    return {"level": name, "badge": badge, "xp": xp, "xp_needed": xp_need, "bar": bar, "total": total}


def sm2_review(q: Question, quality: int) -> Question:
    """خوارزمية SM-2 — تُستدعى مرة واحدة فقط بعد الإجابة."""
    now             = datetime.now(timezone.utc).replace(tzinfo=None)
    q.total_reviews = (q.total_reviews or 0) + 1
    q.review_dates  = list(q.review_dates or [])
    q.review_dates.append(now.isoformat())

    if quality >= 3:
        q.streak     = (q.streak or 0) + 1
        ef           = (q.ease_factor or 2.5) + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)
        q.ease_factor = max(1.3, min(2.5, ef))
        iv           = q.interval or 0
        if   iv == 0: q.interval = 1
        elif iv == 1: q.interval = 6
        else:         q.interval = round(iv * q.ease_factor)
        q.next_review = now + timedelta(days=q.interval)
    else:
        q.streak      = 0
        q.wrong_count = (q.wrong_count or 0) + 1
        q.interval    = 1
        q.next_review = now + timedelta(days=1)
    return q


def get_next_question(
    questions: List[Question], mode="all",
    tag: Optional[str] = None, exclude_id: Optional[int] = None
) -> Optional[Question]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if   mode == "due":  pool = [q for q in questions if q.next_review and q.next_review <= now]
    elif mode == "weak": pool = [q for q in questions if (q.total_reviews or 0) > 0 and (q.wrong_count or 0) / (q.total_reviews or 1) > 0.3]
    elif mode == "tag" and tag: pool = [q for q in questions if tag in (q.tags or [])]
    else: pool = list(questions)

    if exclude_id:
        pool = [q for q in pool if q.id != exclude_id]
    if not pool:
        return None

    pool.sort(key=lambda q: (
        0 if q.next_review and q.next_review <= now else 1,
        -((q.wrong_count or 0) / max(q.total_reviews or 1, 1)),
        -(q.id or 0),
    ))
    return pool[0]


def predict_score(questions: List[Question]) -> Dict[str, Any]:
    if not questions:
        return {"overall": 0, "confidence": "منخفض"}
    tr       = sum(q.total_reviews or 0 for q in questions)
    avg_ef   = sum(q.ease_factor or 2.5 for q in questions) / len(questions)
    wrong_r  = sum(q.wrong_count or 0 for q in questions) / max(tr, 1)
    score    = max(0, min(100, avg_ef / 2.5 * 100 - wrong_r * 50))
    conf     = "منخفض" if tr < 10 else "متوسط" if tr < 50 else "مرتفع"
    return {"overall": round(score, 1), "confidence": conf}


def build_options_display(opts: List[str], correct_idx: int, selected_idx: int = -1) -> str:
    """يعرض الخيارات بعد الإجابة مع أيقونة لكل خيار."""
    lines = []
    for i, opt in enumerate(opts[:6]):
        body = re.sub(r"^[أ-يa-zA-Z\d]\s*[\)\-\.–—]\s*", "", opt).strip() or opt
        lbl  = _LABELS[i] if i < len(_LABELS) else str(i + 1)
        if   i == correct_idx:                    icon = "✅"
        elif i == selected_idx != correct_idx:    icon = "❌"
        else:                                      icon = "◻️"
        lines.append(f"{icon} {lbl}) {body}")
    return "\n".join(lines)

# ═══════════════════════════════════════════════════
#  لوحات المفاتيح
# ═══════════════════════════════════════════════════
def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 إضافة يدوي",   callback_data="menu_add")],
        [InlineKeyboardButton("🧠 مراجعة عامة",  callback_data="menu_quiz_all"),
         InlineKeyboardButton("📆 مراجعة اليوم", callback_data="menu_quiz_due")],
        [InlineKeyboardButton("❗ نقاط الضعف",   callback_data="menu_quiz_weak"),
         InlineKeyboardButton("🏷️ حسب المادة",  callback_data="menu_quiz_tag")],
        [InlineKeyboardButton("📋 آخر الأسئلة",  callback_data="menu_list"),
         InlineKeyboardButton("🔍 بحث",          callback_data="menu_search")],
        [InlineKeyboardButton("📊 الإحصائيات",   callback_data="menu_stats"),
         InlineKeyboardButton("🏆 مستواك",       callback_data="menu_level")],
        [InlineKeyboardButton("📤 تصدير",        callback_data="menu_export"),
         InlineKeyboardButton("🗑️ مسح الكل",    callback_data="menu_clear")],
    ])

# ═══════════════════════════════════════════════════
#  حالات المحادثة
# ═══════════════════════════════════════════════════
ADD_TEXT, ADD_PRIO, ADD_TAGS = range(3)

# ═══════════════════════════════════════════════════
#  /start  /help  /ping
# ═══════════════════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        if update.message:
            await update.message.reply_text("⛔ غير مصرح.")
        return
    all_q  = await _cache.get()
    stats  = await db.get_stats()
    dates  = [d for q in all_q for d in (q.review_dates or [])]
    streak = calculate_streak(dates)
    total  = sum(q.total_reviews or 0 for q in all_q)
    lv     = get_level_info(total)
    bar    = "█" * lv["bar"] + "░" * (10 - lv["bar"])
    text   = (
        f"🤖 *Quiz Master Pro 2026*\n\n"
        f"{lv['badge']} المستوى *{lv['level']}* | [{bar}]\n"
        f"🔥 السلسلة: *{streak}* يوم | ⏰ مستحقة: *{stats['due']}*\n"
        f"📌 البنك: *{stats['total']}* سؤال | 🤖 ملتقطة: *{stats['auto_captured']}*\n\n"
        f"أرسل أي سؤال أو استخدم الأزرار 👇"
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=main_keyboard(), parse_mode=ParseMode.MARKDOWN)
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=main_keyboard(), parse_mode=ParseMode.MARKDOWN)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    text = (
        "🛟 *الأوامر المتاحة*\n\n"
        "/start — القائمة الرئيسية\n"
        "/help — هذه المساعدة\n"
        "/wrong — (رد على رسالة) حفظ كخطأ\n"
        "/weak — الأسئلة الأضعف\n"
        "/search كلمة — بحث في البنك\n"
        "/delete رقم — حذف سؤال\n"
        "/tag رقم وسم — إضافة وسم\n"
        "/list — آخر 10 أسئلة\n"
        "/ping — اختبار الاتصال\n\n"
        "📌 *أرسل أي نص لحفظه تلقائياً*\n"
        "📊 *أرسل استطلاعاً (Poll) لحفظه مع الإجابة*\n"
        "📸 *أرسل صورة مع كابشن لحفظ السؤال*"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    await update.message.reply_text("🏓 بونغ! البوت شغال 🚀")

# ═══════════════════════════════════════════════════
#  إضافة يدوي (ConversationHandler)
# ═══════════════════════════════════════════════════
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "📝 *إضافة سؤال يدوي*\n\nأرسل نص السؤال (يمكن أن يتضمن خيارات بصيغة أ) ب) ...):",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ADD_TEXT


async def _add_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cleaned                   = clean_text(update.message.text)
    q_text, opts, cidx, expl = extract_options(cleaned)
    context.user_data["q"]    = {"text": q_text, "options": opts, "correct_index": cidx, "explanation": expl}
    preview = (
        f"📋 *معاينة*\n\n"
        f"*سؤال:* {q_text[:200]}\n"
        f"*خيارات:* {len(opts)} | *إجابة:* {'محددة ✅' if cidx >= 0 else 'غير محددة'}\n\n"
        "اختر مستوى الأهمية:"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 عاجل",  callback_data="prio_urgent")],
        [InlineKeyboardButton("⚡ متوسط", callback_data="prio_normal")],
        [InlineKeyboardButton("📖 عادي",  callback_data="prio_low")],
    ])
    await update.message.reply_text(preview, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    return ADD_PRIO


async def _add_prio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["q"]["priority"] = q.data.replace("prio_", "")
    await q.edit_message_text("🏷️ أرسل وسم المادة (مثال: قدرات، إنجليزي) أو /skip للتخطي:")
    return ADD_TAGS


async def _add_tags(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tags = [t.strip() for t in update.message.text.split(",") if t.strip()]
    return await _finish_add(update, context, tags)


async def _add_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _finish_add(update, context, [])


async def _finish_add(update, context, tags: List[str]):
    d   = context.user_data.get("q", {})
    obj = Question(
        text=d.get("text", ""), options=d.get("options", []),
        correct_index=d.get("correct_index", -1), explanation=d.get("explanation"),
        priority=d.get("priority", "normal"), tags=tags,
    )
    qid = await db.add_question(obj)
    await update.message.reply_text(
        f"✅ *تم الحفظ!* #️⃣{qid}\n🏷️ {', '.join(tags) if tags else 'بدون وسوم'}",
        parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard(),
    )
    return ConversationHandler.END


async def _add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم الإلغاء.", reply_markup=main_keyboard())
    return ConversationHandler.END


add_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(add_start, pattern="^menu_add$")],
    states={
        ADD_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, _add_text)],
        ADD_PRIO: [CallbackQueryHandler(_add_prio, pattern="^prio_")],
        ADD_TAGS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, _add_tags),
            CommandHandler("skip", _add_skip),
        ],
    },
    fallbacks=[CommandHandler("cancel", _add_cancel)],
    allow_reentry=True,
)

# ═══════════════════════════════════════════════════
#  عرض السؤال
# ═══════════════════════════════════════════════════
async def _send_question(target, question: Question, context: ContextTypes.DEFAULT_TYPE):
    q_text, ex_opts, _, _ = extract_options(question.text)
    opts    = ex_opts or question.options or []
    display = q_text or question.text
    tags_tx = f" [{', '.join(question.tags)}]" if question.tags else ""
    auto_tx = " 🤖" if question.auto_captured else ""

    header = (
        f"🧠 *سؤال #{question.id}*\n"
        f"{priority_text(question.priority)}{tags_tx}{auto_tx}\n"
        f"📊 EF: {question.ease_factor:.1f} | "
        f"❌ {question.wrong_count or 0} | "
        f"🔁 {question.total_reviews or 0}\n\n"
    )

    if not opts:
        text = header + display
        kb   = InlineKeyboardMarkup([[
            InlineKeyboardButton("⏭ تخطي",  callback_data=f"skip_{question.id}"),
            InlineKeyboardButton("⏹ إنهاء", callback_data="end_quiz"),
        ]])
    else:
        rows = []
        for i, opt in enumerate(opts[:6]):
            body = re.sub(r"^[أ-يa-zA-Z\d]\s*[\)\-\.–—]\s*", "", opt).strip() or opt
            lbl  = _LABELS[i] if i < len(_LABELS) else str(i + 1)
            btn  = f"{lbl}) {body[:45]}{'…' if len(body) > 45 else ''}"
            rows.append([InlineKeyboardButton(btn, callback_data=f"opt_{question.id}_{i}")])
        rows.append([
            InlineKeyboardButton("⏭ تخطي",  callback_data=f"skip_{question.id}"),
            InlineKeyboardButton("⏹ إنهاء", callback_data="end_quiz"),
        ])
        text = header + display
        kb   = InlineKeyboardMarkup(rows)

    try:
        if hasattr(target, "edit_message_text"):
            await target.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        else:
            await target.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    except TelegramError as e:
        logger.warning(f"_send_question: {e}")

# ═══════════════════════════════════════════════════
#  الكويز
# ═══════════════════════════════════════════════════
async def _start_quiz(update, context, mode: str, tag: Optional[str] = None):
    q = update.callback_query
    await q.answer()
    context.user_data.update({"quiz_mode": mode, "quiz_tag": tag, "quiz_correct": 0, "quiz_total": 0})
    all_qs = await _cache.get()
    nxt    = get_next_question(all_qs, mode=mode, tag=tag)
    msgs   = {
        "all":  "📭 البنك فارغ، أضف أسئلة أولاً.",
        "due":  "📆 لا مراجعات مستحقة الآن، عد لاحقاً.",
        "weak": "💪 أداؤك ممتاز! لا أسئلة ضعيفة.",
        "tag":  f"🏷️ لا أسئلة بوسم [{tag}].",
    }
    if not nxt:
        await q.edit_message_text(msgs.get(mode, "📭 لا أسئلة."), reply_markup=main_keyboard())
        return
    context.user_data["current_qid"] = nxt.id
    await _send_question(q, nxt, context)


async def menu_quiz_all(u, c):  await _start_quiz(u, c, "all")
async def menu_quiz_due(u, c):  await _start_quiz(u, c, "due")
async def menu_quiz_weak(u, c): await _start_quiz(u, c, "weak")


async def menu_quiz_tag(update, context):
    q = update.callback_query
    await q.answer()
    tags = await db.get_all_tags()
    if not tags:
        await q.edit_message_text("🏷️ لا توجد وسوم بعد.", reply_markup=main_keyboard())
        return
    btns = [[InlineKeyboardButton(f"🏷️ {t}", callback_data=f"tag_{t}")] for t in tags]
    btns.append([InlineKeyboardButton("❌ إلغاء", callback_data="menu_back")])
    await q.edit_message_text("🏷️ اختر المادة:", reply_markup=InlineKeyboardMarkup(btns))


async def quiz_tag_selected(update, context):
    q   = update.callback_query
    await q.answer()
    tag = q.data.replace("tag_", "")
    await _start_quiz(update, context, "tag", tag)


async def quiz_option(update, context):
    """معالج اختيار خيار: يعرض النتيجة مع الشرح، ويطبق SM-2 فوراً، ثم يقدم زر التالي."""
    q = update.callback_query
    await q.answer()
    _, qid_str, idx_str = q.data.split("_")
    qid, sel            = int(qid_str), int(idx_str)
    question            = await db.get_question(qid)
    if not question:
        await q.edit_message_text("❌ السؤال غير موجود.")
        return

    correct = (sel == question.correct_index)
    # تطبيق SM-2 فوراً (بدون انتظار تقييم المستخدم)
    quality = 5 if correct else 0
    updated = sm2_review(question, quality)
    await db.update_question(updated)

    # تحديث عداد الجلسة
    context.user_data["quiz_total"] = context.user_data.get("quiz_total", 0) + 1
    if correct:
        context.user_data["quiz_correct"] = context.user_data.get("quiz_correct", 0) + 1

    # عرض الخيارات مع أيقونات
    _, opts, _, _ = extract_options(question.text)
    all_opts = opts or question.options or []
    opts_display = build_options_display(all_opts, question.correct_index, sel)

    result_icon = "✅ *صحيح!* 🎉" if correct else "❌ *خطأ!*"
    expl_block  = f"\n\n💡 *شرح:*\n{question.explanation}" if (not correct and question.explanation) else ""

    text = f"{result_icon}\n\n{opts_display}{expl_block}"

    # زر واحد فقط: التالي
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⏭ السؤال التالي", callback_data="next_question")]])

    await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    context.user_data["last_question_id"] = qid


async def _next_question(update, context):
    q       = update.callback_query
    await q.answer()
    mode    = context.user_data.get("quiz_mode", "all")
    tag     = context.user_data.get("quiz_tag")
    exclude = context.user_data.get("current_qid")
    all_qs  = await _cache.get()
    nxt     = get_next_question(all_qs, mode=mode, tag=tag, exclude_id=exclude)

    if not nxt:
        correct = context.user_data.get("quiz_correct", 0)
        total   = context.user_data.get("quiz_total",   0)
        pct     = round(correct / total * 100) if total else 0
        await q.edit_message_text(
            f"🎉 *انتهت المراجعة!*\n\n"
            f"✅ صحيح: {correct}/{total} ({pct}%)\n"
            f"أحسنت! 💪",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_keyboard(),
        )
        context.user_data.clear()
        return

    context.user_data["current_qid"] = nxt.id
    await _send_question(q, nxt, context)


async def quiz_skip(update, context):
    q = update.callback_query
    await q.answer()
    await _next_question(update, context)


async def quiz_end(update, context):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("✅ تم إنهاء المراجعة.", reply_markup=main_keyboard())
    context.user_data.clear()

# ═══════════════════════════════════════════════════
#  قوائم / بحث / إحصائيات / مستوى / تصدير / مسح
# ═══════════════════════════════════════════════════
async def menu_list(update, context):
    q = update.callback_query
    await q.answer()
    all_qs = await _cache.get()
    if not all_qs:
        await q.edit_message_text("📭 لا توجد أسئلة.", reply_markup=main_keyboard()); return
    last  = sorted(all_qs, key=lambda x: x.id, reverse=True)[:10]
    lines = ["📋 *آخر 10 أسئلة:*\n"]
    for item in last:
        short = item.text.replace("\n", " ")[:70]
        short += "…" if len(item.text) > 70 else ""
        lines.append(f"*#{item.id}*{'🤖' if item.auto_captured else ''} — {short}")
    await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())


async def menu_search(update, context):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("🔍 أرسل `/search كلمة`", parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())


async def menu_stats(update, context):
    q = update.callback_query
    await q.answer()
    stats  = await db.get_stats()
    all_qs = await _cache.get()
    pred   = predict_score(all_qs)
    tags   = await db.get_all_tags()
    text   = (
        f"📊 *إحصائيات شاملة*\n\n"
        f"📌 إجمالي: *{stats['total']}* | ⏰ مستحقة: *{stats['due']}*\n"
        f"🔥 عاجل: {stats['by_priority']['urgent']} | "
        f"⚡ متوسط: {stats['by_priority']['normal']} | "
        f"📖 عادي: {stats['by_priority']['low']}\n"
        f"🤖 ملتقطة تلقائياً: *{stats['auto_captured']}*\n"
        f"📈 متوسط Ease Factor: *{stats['avg_ease']}*\n\n"
        f"🎯 درجة متوقعة: *{pred['overall']}%* (ثقة {pred['confidence']})\n"
        f"🏷️ الوسوم: {', '.join(tags) if tags else 'لا يوجد'}"
    )
    await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())


async def menu_level(update, context):
    q      = update.callback_query
    await q.answer()
    all_qs = await _cache.get()
    total  = sum(item.total_reviews or 0 for item in all_qs)
    dates  = [d for item in all_qs for d in (item.review_dates or [])]
    streak = calculate_streak(dates)
    lv     = get_level_info(total)
    bar    = "█" * lv["bar"] + "░" * (10 - lv["bar"])
    text   = (
        f"{lv['badge']} *المستوى: {lv['level']}*\n\n"
        f"[{bar}] {lv['xp']}/{lv['xp_needed']} XP\n"
        f"🔁 إجمالي المراجعات: *{total}*\n"
        f"🔥 السلسلة: *{streak}* يوم"
    )
    await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard())


async def menu_export(update, context):
    q = update.callback_query
    await q.answer()
    qs = await _cache.get()
    if not qs:
        await q.edit_message_text("📂 لا توجد بيانات.", reply_markup=main_keyboard()); return
    await q.edit_message_text("📤 جاري التصدير…")
    data = [item.to_dict() for item in qs]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        tmp = f.name
    with open(tmp, "rb") as f:
        await context.bot.send_document(
            chat_id=q.message.chat_id,
            document=InputFile(f, filename=f"quiz_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.json"),
            caption=f"📦 {len(qs)} سؤال — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        )
    os.unlink(tmp)
    await context.bot.send_message(chat_id=q.message.chat_id, text="✅ تم التصدير.", reply_markup=main_keyboard())


async def menu_clear(update, context):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "⚠️ *هل تريد مسح جميع الأسئلة؟* لا يمكن التراجع!",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ نعم، امسح الكل", callback_data="clear_yes")],
            [InlineKeyboardButton("❌ إلغاء",           callback_data="clear_no")],
        ]),
    )


async def clear_decision(update, context):
    q = update.callback_query
    await q.answer()
    if q.data == "clear_yes":
        await db.clear_all()
        await q.edit_message_text("🗑️ تم مسح جميع الأسئلة.")
    else:
        await q.edit_message_text("❌ تم الإلغاء.")
    await context.bot.send_message(chat_id=q.message.chat_id, text="اختر:", reply_markup=main_keyboard())


async def menu_back(update, context):
    await start(update, context)

# ═══════════════════════════════════════════════════
#  معالجات الرسائل (نص / صورة / poll)
# ═══════════════════════════════════════════════════
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    msg = update.message
    if not msg or not msg.text:
        return
    raw      = msg.text
    is_error = any(m in raw for m in ["#خطأ", "#غلط", "#weak", "#ضعيف"])
    cleaned  = clean_text(raw)
    for marker in ["#خطأ", "#غلط", "#weak", "#ضعيف"]:
        cleaned = cleaned.replace(marker, "").strip()

    q_text, opts, cidx, expl = extract_options(cleaned)
    obj = Question(
        text=q_text, options=opts, correct_index=cidx, explanation=expl,
        priority="urgent" if (msg.forward_date or is_error) else "normal",
        tags=["weak"] if is_error else [],
        auto_captured=bool(msg.forward_date),
    )
    qid   = await db.add_question(obj)

    # تعديل هنا: استخدام إضافة نصوص بدلاً من f-string مع \n
    reply = f"✅ *تم الحفظ!* #️⃣{qid} | {priority_text(obj.priority)}\n"
    if is_error:
        reply += "🏷️ وسم: weak\n"
    if opts:
        reply += f"💡 `/tag {qid} قدرات`"
    else:
        reply += "⚠️ لم تُكتشف خيارات — `/tag {qid} قدرات`"

    await msg.reply_text(reply, parse_mode=ParseMode.MARKDOWN)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    caption = (update.message.caption or "").strip()
    if not caption:
        await update.message.reply_text(
            "📸 أرسل الصورة مع *كابشن* يحتوي نص السؤال.\n"
            "مثال: الصورة + كابشن: أ) خيار١ ب) خيار٢ …",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    cleaned               = clean_text(caption)
    q_text, opts, cidx, expl = extract_options(cleaned)
    obj = Question(
        text=q_text, options=opts, correct_index=cidx,
        explanation=expl, priority="normal", auto_captured=True,
    )
    qid = await db.add_question(obj)
    await update.message.reply_text(f"✅ *تم الحفظ من الصورة!* #️⃣{qid}", parse_mode=ParseMode.MARKDOWN)


async def handle_poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    poll     = update.message.poll
    caption  = update.message.caption or ""
    is_error = any(m in caption for m in ["#خطأ", "#غلط", "#weak"])
    labs     = ["أ", "ب", "ج", "د", "هـ", "و"]
    opts     = [o.text for o in poll.options]
    cidx     = poll.correct_option_id if poll.correct_option_id is not None else -1
    lines    = [poll.question]
    for i, opt in enumerate(poll.options):
        lbl = labs[i] if i < len(labs) else str(i + 1)
        lines.append(f"{lbl}) {opt.text}")
    if cidx >= 0:
        lbl = labs[cidx] if cidx < len(labs) else str(cidx + 1)
        lines.append(f"\n✅ الإجابة الصحيحة: {lbl}) {poll.options[cidx].text}")

    obj = Question(
        text="\n".join(lines), options=opts, correct_index=cidx,
        priority="urgent", tags=["weak"] if is_error else [],
    )
    qid = await db.add_question(obj)
    await update.message.reply_text(
        f"✅ *تم حفظ الاستطلاع!* #️⃣{qid}",
        parse_mode=ParseMode.MARKDOWN,
    )

# ═══════════════════════════════════════════════════
#  أوامر إضافية
# ═══════════════════════════════════════════════════
async def wrong_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    rep = update.message.reply_to_message
    if not rep:
        await update.message.reply_text("❌ يجب الرد على رسالة تحتوي سؤالاً."); return
    raw = rep.text or rep.caption or ""
    if not raw:
        await update.message.reply_text("❌ الرسالة لا تحتوي على نص."); return
    q_text, opts, cidx, expl = extract_options(clean_text(raw))
    obj = Question(
        text=q_text, options=opts, correct_index=cidx, explanation=expl,
        priority="urgent", tags=["weak"], auto_captured=True,
    )
    qid = await db.add_question(obj)
    await update.message.reply_text(f"✅ *تم حفظ السؤال كـ ضعيف!* #️⃣{qid}", parse_mode=ParseMode.MARKDOWN)


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    if not context.args:
        await update.message.reply_text("اكتب: `/search كلمة`", parse_mode=ParseMode.MARKDOWN); return
    results = await db.search(" ".join(context.args))
    if not results:
        await update.message.reply_text("❌ لا نتائج."); return
    lines = [f"🔍 *نتائج:*\n"]
    for item in results[:15]:
        short = item.text.replace("\n", " ")[:70]
        lines.append(f"*#{item.id}* — {short}{'…' if len(item.text) > 70 else ''}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("اكتب: `/delete رقم`", parse_mode=ParseMode.MARKDOWN); return
    ok = await db.delete_question(int(context.args[0]))
    await update.message.reply_text(
        f"🗑️ تم حذف #{context.args[0]}." if ok else "❌ السؤال غير موجود."
    )


async def tag_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.message.reply_text("اكتب: `/tag رقم وسم`", parse_mode=ParseMode.MARKDOWN); return
    qid = int(context.args[0])
    tag = context.args[1].strip()
    obj = await db.get_question(qid)
    if not obj:
        await update.message.reply_text("❌ غير موجود."); return
    tags = list(obj.tags or [])
    if tag not in tags:
        tags.append(tag)
        obj.tags = tags
        await db.update_question(obj)
    await update.message.reply_text(f"🏷️ تمت إضافة *{tag}* للسؤال #{qid}.", parse_mode=ParseMode.MARKDOWN)


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    all_qs = await _cache.get()
    if not all_qs:
        await update.message.reply_text("📭 لا توجد أسئلة."); return
    last  = sorted(all_qs, key=lambda x: x.id, reverse=True)[:10]
    lines = ["📋 *آخر 10 أسئلة:*\n"]
    for item in last:
        short = item.text.replace("\n", " ")[:70]
        lines.append(f"*#{item.id}*{'🤖' if item.auto_captured else ''} — {short}{'…' if len(item.text)>70 else ''}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def weak_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    weak = await db.get_weakest(5)
    if not weak:
        await update.message.reply_text("❗ لا توجد أسئلة ضعيفة بعد."); return
    lines = ["❗ *أضعف 5 أسئلة:*\n"]
    for item in weak:
        pct = round((item.wrong_count or 0) / max(item.total_reviews or 1, 1) * 100)
        short = item.text.replace("\n", " ")[:60]
        lines.append(f"*#{item.id}* ❌{pct}% — {short}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)

# ═══════════════════════════════════════════════════
#  التقرير اليومي التلقائي
# ═══════════════════════════════════════════════════
async def send_daily_report(context: ContextTypes.DEFAULT_TYPE):
    try:
        stats  = await db.get_stats()
        all_qs = await _cache.get()
        pred   = predict_score(all_qs)
        dates  = [d for q in all_qs for d in (q.review_dates or [])]
        streak = calculate_streak(dates)
        lv     = get_level_info(sum(q.total_reviews or 0 for q in all_qs))
        text   = (
            f"🌅 *تقرير الصباح — {datetime.now(timezone.utc).strftime('%Y/%m/%d')}*\n\n"
            f"{lv['badge']} المستوى: *{lv['level']}*\n"
            f"🔥 السلسلة: *{streak}* يوم\n"
            f"⏰ مستحقة اليوم: *{stats['due']}*\n"
            f"📌 إجمالي البنك: *{stats['total']}*\n"
            f"🎯 درجة متوقعة: *{pred['overall']}%*\n\n"
            f"{'🚀 لديك مراجعات مستحقة، انطلق!' if stats['due'] > 0 else '✅ ليس لديك مراجعات اليوم، أضف أسئلة جديدة!'}"
        )
        await context.bot.send_message(
            chat_id=ALLOWED_USER_ID, text=text,
            parse_mode=ParseMode.MARKDOWN, reply_markup=main_keyboard(),
        )
    except Exception as e:
        logger.error(f"daily_report error: {e}")

# ═══════════════════════════════════════════════════
#  معالج الأخطاء المحسن
# ═══════════════════════════════════════════════════
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling an update:", exc_info=context.error)
    try:
        # إبلاغ المستخدم بحدوث خطأ
        if update and hasattr(update, "effective_chat") and update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ عذراً، حدث خطأ غير متوقع. تم إبلاغ المطور."
            )
        # إرسال تفاصيل الخطأ للمطور (صاحب البوت)
        if ALLOWED_USER_ID:
            import traceback
            tb = "".join(traceback.format_exception(None, context.error, context.error.__traceback__))
            # تقييد طول الرسالة
            if len(tb) > 3500:
                tb = tb[:3500] + "\n… (مقطوع)"
            await context.bot.send_message(
                chat_id=ALLOWED_USER_ID,
                text=f"⚠️ *خطأ في البوت:*\n```\n{tb}\n```",
                parse_mode=ParseMode.MARKDOWN
            )
    except Exception as e:
        logger.error(f"فشل معالج الأخطاء نفسه: {e}")

# ═══════════════════════════════════════════════════
#  main
# ═══════════════════════════════════════════════════
async def main():
    await init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # ── ConversationHandler ───────────────────────
    app.add_handler(add_conv)

    # ── أوامر ─────────────────────────────────────
    for cmd, fn in [
        ("start",  start),      ("help",   help_cmd),
        ("ping",   ping_cmd),   ("wrong",  wrong_cmd),
        ("search", search_cmd), ("delete", delete_cmd),
        ("tag",    tag_cmd),    ("list",   list_cmd),
        ("weak",   weak_cmd),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    # ── Callbacks (من الأكثر تخصصاً للأعم) ────────
    for pattern, fn in [
        (r"^opt_\d+_\d+$",    quiz_option),
        (r"^skip_\d+$",       quiz_skip),
        (r"^tag_.+$",         quiz_tag_selected),
        (r"^next_question$",  _next_question),
        (r"^end_quiz$",       quiz_end),
        (r"^menu_quiz_all$",  menu_quiz_all),
        (r"^menu_quiz_due$",  menu_quiz_due),
        (r"^menu_quiz_weak$", menu_quiz_weak),
        (r"^menu_quiz_tag$",  menu_quiz_tag),
        (r"^menu_list$",      menu_list),
        (r"^menu_search$",    menu_search),
        (r"^menu_stats$",     menu_stats),
        (r"^menu_level$",     menu_level),
        (r"^menu_export$",    menu_export),
        (r"^menu_clear$",     menu_clear),
        (r"^menu_back$",      menu_back),
        (r"^clear_(yes|no)$", clear_decision),
        (r"^menu_add$",       add_start),
    ]:
        app.add_handler(CallbackQueryHandler(fn, pattern=pattern))

    # ── رسائل ─────────────────────────────────────
    app.add_handler(MessageHandler(filters.POLL,                              handle_poll))
    app.add_handler(MessageHandler(filters.PHOTO,                             handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,           handle_message))

    # ── معالج الأخطاء ────────────────────────────
    app.add_error_handler(error_handler)

    # ── التقرير اليومي ────────────────────────────
    # نضبط الوقت بحيث يكون ثابتاً كل يوم
    app.job_queue.run_daily(
        send_daily_report,
        time=datetime.now(timezone.utc).replace(
            hour=DAILY_HOUR, minute=DAILY_MINUTE, second=0, microsecond=0
        ).timetz(),
        name="daily_report"
    )

    logger.info("🚀 Quiz Master Pro 2026 — Started")
    await app.run_polling(drop_pending_updates=True)

        if __name__ == "__main__":
    asyncio.run(main())