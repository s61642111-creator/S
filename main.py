import logging, json, os, re, asyncio
async def is_auth(u: Update): return True
async def reject(u: Update): pass
import nest_asyncio
nest_asyncio.apply()
from datetime import datetime, timedelta, timezone
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InputFile, WebAppInfo,
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, ContextTypes, filters,
)
from config.settings import settings
from core.database import db
from core.models import Question
from core.quiz_engine import engine, get_next_question, get_level_info, calculate_streak
from core.analytics_engine import analytics

logging.basicConfig(format="%(asctime)s [BOT] %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger("bot")

ADD_TEXT, ADD_PRIO, ADD_TAGS = range(3)

def _now(): return datetime.now(timezone.utc)

def clean(raw: str) -> str:
    lines, out = raw.splitlines(), []
    for line in lines:
        s = line.strip()
        if not s: out.append(line); continue
        if any(ch in s for ch in ["⏳","⌛","⏱","⏰"]): continue
        low = s.lower()
        if any(kw in low for kw in ["ثانية","ثوان","الوقت المتبقي","time left","sec"]):
            if len(s) <= 30: continue
        if re.fullmatch(r"[0-9]{1,2}[:\.][0-9]{1,2}", s): continue
        out.append(line)
    return "\n".join(out)

def is_fwd(msg) -> bool:
    return bool(getattr(msg,"forward_date",None) or getattr(msg,"forward_from",None)
                or getattr(msg,"forward_from_chat",None) or getattr(msg,"forward_origin",None))

def prio_txt(p): return {"urgent":"🔥 عاجل","normal":"⚡ متوسط","low":"📖 عادي"}.get(p,p)

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 إضافة سؤال يدوي", callback_data="menu_add")],
        [InlineKeyboardButton("🧠 مراجعة عامة",     callback_data="menu_quiz_all")],
        [InlineKeyboardButton("📆 مراجعة اليوم",    callback_data="menu_quiz_due"),
         InlineKeyboardButton("❗ نقاط الضعف",      callback_data="menu_quiz_weak")],
        [InlineKeyboardButton("🏷️ مراجعة حسب المادة", callback_data="menu_quiz_tag")],
        [InlineKeyboardButton("📋 آخر الأسئلة",    callback_data="menu_list"),
         InlineKeyboardButton("🔍 بحث",            callback_data="menu_search")],
        [InlineKeyboardButton("📊 الإحصائيات",     callback_data="menu_stats"),
         InlineKeyboardButton("🏆 مستواك",         callback_data="menu_level")],
        [InlineKeyboardButton("🌐 فتح Mini App",
            web_app=WebAppInfo(url=settings.WEBAPP_URL))],
        [InlineKeyboardButton("📤 تصدير",          callback_data="menu_export"),
         InlineKeyboardButton("🗑️ مسح الكل",      callback_data="menu_clear")],
    ])

def quiz_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Good",  callback_data="quiz_good"),
         InlineKeyboardButton("🌟 Easy",  callback_data="quiz_easy")],
        [InlineKeyboardButton("⚡ Hard",  callback_data="quiz_hard"),
         InlineKeyboardButton("🔄 Again", callback_data="quiz_again")],
        [InlineKeyboardButton("⏹ إنهاء", callback_data="quiz_end")],
    ])

# ── Start ──────────────────────────────────────────────────────────
async def start(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_auth(u): await reject(u); return
    all_q = await db.all_questions()
    stats = await db.get_stats()
    all_dates = []
    for q in all_q: all_dates.extend(q.review_dates)
    streak    = calculate_streak(all_dates)
    total_rev = sum(q.total_reviews for q in all_q)
    lvl = get_level_info(total_rev)
    txt = (
        f"🤖 *Quiz Master Pro v2*\n\n"
        f"{lvl['badge']} المستوى *{lvl['level']}* | XP: {lvl['xp']}/10\n"
        f"🔥 Streak: *{streak}* يوم | ⏰ مستحقة: *{stats['due']}*\n"
        f"🤖 التقطها الصائد: *{stats['auto_captured']}*\n\n"
        f"📌 حوّل أي كويز للبوت أو افتح Mini App\n"
        f"💡 /help للشرح الكامل"
    )
    if u.message:
        await u.message.reply_text(txt, reply_markup=main_kb(), parse_mode="Markdown")
    else:
        await u.callback_query.edit_message_text(txt, reply_markup=main_kb(), parse_mode="Markdown")

async def help_cmd(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_auth(u): await reject(u); return
    txt = (
        "🛟 *شرح Quiz Master Pro v2*\n\n"
        "*📥 طرق الإضافة:*\n"
        "• Forward كويز نصي → ينظّف المؤقت ويحفظ\n"
        "• Forward Quiz Poll → يحوّله لنص ويحفظ\n"
        "• 📝 إضافة يدوية\n"
        "• 🤖 الصائد الصامت (userbot.py منفصل)\n\n"
        "*🧠 SM-2 Algorithm:*\n"
        "Again → يُعاد غداً\n"
        "Hard  → فترة قصيرة\n"
        "Good  → فترة معقولة\n"
        "Easy  → فترة أطول\n\n"
        "*🌐 Mini App:*\n"
        "• بطاقات سحب (Swiper)\n"
        "• يعمل offline (PWA + Service Worker)\n"
        "• تحليلات + تنبؤ بالدرجات\n\n"
        "*📌 أوامر:*\n"
        "/search كلمة | /delete رقم | /list\n"
        "/tag رقم وسم | /weak | /today | /ping"
    )
    await u.message.reply_text(txt, parse_mode="Markdown")

async def ping_cmd(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_auth(u): await reject(u); return
    await u.message.reply_text("✅ Quiz Master Pro v2 — شغال 🚀")

# ── استقبال الرسائل ────────────────────────────────────────────────
async def handle_poll(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_auth(u): await reject(u); return
    msg  = u.message
    poll = msg.poll
    if not poll: return
    labels = ["أ","ب","ج","د","هـ","و"]
    lines  = [poll.question]
    opts   = []
    for i, opt in enumerate(poll.options):
        lbl = labels[i] if i < len(labels) else str(i+1)
        lines.append(f"{lbl}) {opt.text}")
        opts.append(opt.text)
    cidx = 0
    if poll.correct_option_id is not None:
        cid  = poll.correct_option_id
        cidx = cid
        if 0 <= cid < len(poll.options):
            cl = labels[cid] if cid < len(labels) else str(cid+1)
            lines.append(f"\n✅ الإجابة الصحيحة: {cl}) {poll.options[cid].text}")
    q = Question(id=0, text="\n".join(lines), options=opts,
                 correct_index=cidx, priority="urgent")
    qid = await db.add_question(q)
    await msg.reply_text(
        f"✅ *تم حفظ Quiz Poll #{qid}!*\n💡 `/tag {qid} قدرات`",
        parse_mode="Markdown"
    )

async def handle_message(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_auth(u): await reject(u); return
    msg = u.message
    if not msg: return
    raw = msg.text or msg.caption
    if not raw: return
    fwd      = is_fwd(msg)
    cleaned  = clean(raw)
    priority = "urgent" if fwd else "low"
    src      = "محوَّل من الكويز" if fwd else "يدوي"
    q = Question(id=0, text=cleaned, priority=priority)
    qid = await db.add_question(q)
    await msg.reply_text(
        f"✅ *تم حفظ السؤال {src}!*\n🆔 #{qid} | {prio_txt(priority)}\n"
        f"💡 `/tag {qid} قدرات`",
        parse_mode="Markdown"
    )

# ── إضافة يدوية (Conversation) ────────────────────────────────────
async def menu_add(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_auth(u): await reject(u); return ConversationHandler.END
    await u.callback_query.answer()
    await u.callback_query.edit_message_text(
        "📝 *إضافة سؤال يدوي*\n\nأرسل نص السؤال + الخيارات:",
        parse_mode="Markdown"
    )
    return ADD_TEXT

async def add_text(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["q_text"] = u.message.text
    await u.message.reply_text("اختر مستوى الأهمية:", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 عاجل",  callback_data="prio_urgent")],
        [InlineKeyboardButton("⚡ متوسط", callback_data="prio_normal")],
        [InlineKeyboardButton("📖 عادي",  callback_data="prio_low")],
    ]))
    return ADD_PRIO

async def add_prio(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await u.callback_query.answer()
    ctx.user_data["q_prio"] = u.callback_query.data.replace("prio_","")
    await u.callback_query.edit_message_text(
        "🏷️ أرسل وسم المادة (مثال: `قدرات`)\nأو /skip بدون وسم.",
        parse_mode="Markdown"
    )
    return ADD_TAGS

async def add_tags(u: Update, ctx: ContextTypes.DEFAULT_TYPE):

    raw = u.message.text.strip()
    tags = [raw] if raw and not raw.startswith("/") else []
    q = Question(id=0, text=ctx.user_data.get("q_text",""),
                 priority=ctx.user_data.get("q_prio","normal"), tags=tags)
    qid = await db.add_question(q)
    await u.message.reply_text(
        f"✅ *تم الحفظ!* #️⃣{qid} | وسوم: {', '.join(tags) or 'بدون'}",
        parse_mode="Markdown", reply_markup=main_kb()
    )
    return ConversationHandler.END

# ── أوامر ─────────────────────────────────────────────────────────
async def tag_cmd(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    
    if not ctx.args or len(ctx.args) < 2:
        await u.message.reply_text("اكتب: `/tag رقم وسم`", parse_mode="Markdown"); return
    try: qid = int(ctx.args[0])
    except: await u.message.reply_text("❌ رقم غير صحيح."); return
    tag = ctx.args[1].strip()
    q = await db.get_question(qid)
    if not q: await u.message.reply_text("❌ غير موجود."); return
    if tag not in q.tags: q.tags.append(tag); await db.update_question(q)
    await u.message.reply_text(f"🏷️ تمت إضافة الوسم *{tag}* للسؤال #{qid}.", parse_mode="Markdown")

async def search_cmd(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_auth(u): await reject(u); return

    term = " ".join(ctx.args)
    res  = await db.search(term)
    if not res: await u.message.reply_text("❌ لا نتائج."); return
    lines = [f"🔍 *نتائج:* `{term}`\n"]
    for q in res[:15]:
        sn = q.text.replace("\n"," ")[:70] + ("..." if len(q.text)>70 else "")
        lines.append(f"*#{q.id}*{'🤖' if q.auto_captured else ''} — {sn}")
    await u.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def delete_cmd(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_auth(u): await reject(u); return
    if not ctx.args: await u.message.reply_text("اكتب: `/delete رقم`", parse_mode="Markdown"); return
    try: qid = int(ctx.args[0])
    except: await u.message.reply_text("❌ رقم غير صحيح."); return
    ok = await db.delete_question(qid)
    await u.message.reply_text(f"🗑️ تم حذف #{qid}." if ok else "❌ غير موجود.")

async def cmd_list(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_auth(u): await reject(u); return
    qs = await db.all_questions()
    if not qs: await u.message.reply_text("📭 لا توجد أسئلة."); return
    last  = sorted(qs, key=lambda q: q.id, reverse=True)[:10]
    lines = ["📋 *آخر 10 أسئلة:*\n"]
    for q in last:
        sn = q.text.replace("\n"," ")[:70] + ("..." if len(q.text)>70 else "")
        lines.append(f"*#{q.id}*{'🤖' if q.auto_captured else ''}{' ['+','.join(q.tags)+']' if q.tags else ''} — {sn}")
    await u.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def weak_cmd(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_auth(u): await reject(u); return
    weak = await db.get_weakest(5)
    if not weak: await u.message.reply_text("❗ لا يوجد بعد."); return
    lines = ["❗ *أضعف 5 أسئلة:*\n"]
    for q in weak:
        sn = q.text.replace("\n"," ")[:60] + ("..." if len(q.text)>60 else "")
        lines.append(f"*#{q.id}* ease:{q.ease_factor:.1f} | خطأ:{q.wrong_count}x\n  {sn}")
    await u.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def today_cmd(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_auth(u): await reject(u); return
    stats  = await db.get_stats()
    all_q  = await db.all_questions()
    dates  = []
    for q in all_q: dates.extend(q.review_dates)
    streak = calculate_streak(dates)
    total  = sum(q.total_reviews for q in all_q)
    lvl    = get_level_info(total)
    pred   = analytics.predict_score(all_q)
    txt = (
        f"📅 *ملخص اليوم:*\n\n"
        f"{lvl['badge']} المستوى {lvl['level']} | XP: {lvl['xp']}/10\n"
        f"🔥 Streak: *{streak}* يوم\n"
        f"📌 إجمالي: *{stats['total']}* | ⏰ مستحقة: *{stats['due']}*\n"
        f"🤖 التقطها الصائد: *{stats['auto_captured']}*\n\n"
        f"📊 *درجتك المتوقعة: {pred['overall']}%*\n"
        f"الثقة: {pred['confidence']}"
    )
    await u.message.reply_text(txt, parse_mode="Markdown")

# ── Callbacks ─────────────────────────────────────────────────────
async def menu_list(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    qs = await db.all_questions()
    if not qs: await q.edit_message_text("📭 لا توجد أسئلة.", reply_markup=main_kb()); return
    last  = sorted(qs, key=lambda x: x.id, reverse=True)[:10]
    lines = ["📋 *آخر 10 أسئلة:*\n"]
    for x in last:
        sn = x.text.replace("\n"," ")[:70] + ("..." if len(x.text)>70 else "")
        lines.append(f"*#{x.id}*{'🤖' if x.auto_captured else ''} — {sn}")
    await q.edit_message_text("\n".join(lines), parse_mode="Markdown", reply_markup=main_kb())

async def menu_stats(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    stats = await db.get_stats()
    all_q = await db.all_questions()
    pred  = analytics.predict_score(all_q)
    tags  = await db.get_all_tags()
    txt = (
        f"📊 *إحصائيات شاملة:*\n\n"
        f"📌 إجمالي: *{stats['total']}* | ⏰ مستحقة: *{stats['due']}*\n"
        f"🔥 عاجل: {stats['by_priority'].get('urgent',0)} | "
        f"⚡ متوسط: {stats['by_priority'].get('normal',0)} | "
        f"📖 عادي: {stats['by_priority'].get('low',0)}\n"
        f"🤖 صائد تلقائي: *{stats['auto_captured']}*\n"
        f"📈 متوسط ease: *{stats['avg_ease']}*\n\n"
        f"📊 درجة متوقعة: *{pred['overall']}%* ({pred['confidence']})\n"
        f"🏷️ الوسوم: {chr(10).join(tags) if tags else 'لا يوجد'}"
    )
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=main_kb())

async def menu_level(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    all_q = await db.all_questions()
    total = sum(x.total_reviews for x in all_q)
    dates = []
    for x in all_q: dates.extend(x.review_dates)
    streak = calculate_streak(dates)
    lvl    = get_level_info(total)
    bar    = "█" * lvl["xp"] + "░" * (10 - lvl["xp"])
    txt = (
        f"{lvl['badge']} *مستواك: {lvl['level']}*\n\n"
        f"XP: [{bar}] {lvl['xp']}/10\n"
        f"🔁 إجمالي المراجعات: *{total}*\n"
        f"🔥 Streak: *{streak}* يوم"
    )
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=main_kb())

async def menu_export(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    qs = await db.all_questions()
    if not qs: await q.edit_message_text("📂 لا توجد بيانات.", reply_markup=main_kb()); return
    await q.edit_message_text("📤 جاري الإرسال…")
    import tempfile, json as _json
    data = [{"id":x.id,"text":x.text,"options":x.options,"correct_index":x.correct_index,
             "tags":x.tags,"priority":x.priority,"ease_factor":x.ease_factor,
             "total_reviews":x.total_reviews,"auto_captured":x.auto_captured}
            for x in qs]
    with tempfile.NamedTemporaryFile(mode="w",suffix=".json",delete=False,encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=2)
        tmp = f.name
    with open(tmp,"rb") as f:
        await ctx.bot.send_document(
            chat_id=q.message.chat_id, document=InputFile(f, filename="quiz_backup.json"),
            caption=f"📦 نسخة احتياطية — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"
        )
    os.unlink(tmp)
    await ctx.bot.send_message(chat_id=q.message.chat_id, text="اختر:", reply_markup=main_kb())

async def menu_clear(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    await q.edit_message_text("⚠️ *هل تريد مسح جميع الأسئلة؟*", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ نعم، امسح", callback_data="clear_yes")],
            [InlineKeyboardButton("❌ إلغاء",      callback_data="clear_no")],
        ]))

async def clear_decision(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    if q.data == "clear_yes":
        await db.clear_all()
        await q.edit_message_text("🗑️ تم المسح.")
    else:
        await q.edit_message_text("تم الإلغاء.")
    await q.message.reply_text("اختر:", reply_markup=main_kb())

# ── Quiz ───────────────────────────────────────────────────────────
async def _quiz_mode(u: Update, ctx: ContextTypes.DEFAULT_TYPE, mode: str):
    q = u.callback_query; await q.answer()
    all_q = await db.all_questions()
    tag   = ctx.user_data.get("quiz_tag")
    nxt   = get_next_question(all_q, mode=mode, tag=tag)
    if not nxt:
        msgs = {"due":"📆 لا توجد أسئلة مستحقة.","weak":"❗ لا توجد أسئلة ضعيفة.","all":"📭 لا توجد أسئلة."}
        await q.edit_message_text(msgs.get(mode,"📭"), reply_markup=main_kb()); return
    ctx.user_data["quiz_id"]   = nxt.id
    ctx.user_data["quiz_mode"] = mode
    await _send_quiz(q, nxt)

async def menu_quiz_tag(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    tags = await db.get_all_tags()
    if not tags:
        await q.edit_message_text("🏷️ لا توجد وسوم بعد.", reply_markup=main_kb()); return
    btns = [[InlineKeyboardButton(f"🏷️ {t}", callback_data=f"qt_{t}")] for t in tags]
    btns.append([InlineKeyboardButton("❌ إلغاء", callback_data="menu_back")])
    await q.edit_message_text("🏷️ *اختر المادة:*", parse_mode="Markdown",
                              reply_markup=InlineKeyboardMarkup(btns))

async def quiz_tag_selected(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    tag   = q.data.replace("qt_","")
    all_q = await db.all_questions()
    nxt   = get_next_question(all_q, mode="all", tag=tag)
    if not nxt:
        await q.edit_message_text(f"📭 لا توجد أسئلة بوسم *{tag}*.", parse_mode="Markdown", reply_markup=main_kb()); return
    ctx.user_data["quiz_id"]   = nxt.id
    ctx.user_data["quiz_mode"] = "all"
    ctx.user_data["quiz_tag"]  = tag
    await _send_quiz(q, nxt)

async def _send_quiz(q, x: Question):
    """سؤال تفاعلي بـ 4 أزرار + شرح عند الخطأ"""
    tags_s = f" [{', '.join(x.tags)}]" if x.tags else ""
    auto_s = " 🤖" if x.auto_captured else ""
    
    # أزرار الخيارات
    keyboard = []
    labels = ['أ', 'ب', 'ج', 'د']
    for i in range(min(4, len(x.options))):
        keyboard.append([InlineKeyboardButton(
            f"{labels[i]}) {x.options[i][:50]}...", 
            callback_data=f"qopt_{x.id}_{i}"
        )])
    
    keyboard.append([InlineKeyboardButton("⏭ تخطي", callback_data="quiz_skip")])
    
    await q.edit_message_text(
        f"🧠 *مراجعة #{x.id}*{tags_s}{auto_s}\\n"
        f"🔥 {prio_txt(x.priority)}\\n"
        f"📊 ease:{x.ease_factor:.1f} | خطأ:{x.wrong_count}\\n\\n"
        f"{x.text}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
async def quiz_option(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """معالج الأزرار التفاعلية"""
    data = u.callback_query.data.split("_")
    if data[0] != "qopt": return
    
    qid, opt_idx = int(data[1]), int(data[2])
    x = await db.get_question(qid)
    if not x: return await u.callback_query.answer("❌ خطأ!")
    
    correct = opt_idx == x.correct_index
    quality = 5 if correct else 0
    
    updated = engine.review(x, quality)
    await db.update_question(updated)
    
    # النتيجة + الشرح
    labels = ['أ', 'ب', 'ج', 'د']
    result = f"{'✅ صحيح! 👏' if correct else '❌ خطأ! 📚'}"
    
    if not correct and x.explanation:
        result += f"\\n💡 *الشرح:* {x.explanation}"
    
    result += f"\\n📊 ease: {updated.ease_factor:.1f}"
    
    await u.callback_query.answer(result, show_alert=True)
    
    # التالي
    all_q = await db.all_questions()
    nxt = get_next_question(all_q)
    if nxt:
        await _send_quiz(u.callback_query, nxt)

    # Gamification feedback
    all_q = await db.all_questions()
    total = sum(z.total_reviews for z in all_q)
    lvl   = get_level_info(total)
    if quality >= 4 and total % 10 == 0:
        await q.message.reply_text(f"🎉 *Level Up! المستوى {lvl['level']} {lvl['badge']}*", parse_mode="Markdown")
    elif quality >= 4 and updated.streak >= 5:
        await q.message.reply_text(f"🔥 *سلسلة {updated.streak} إجابات صحيحة!*", parse_mode="Markdown")

    nxt = get_next_question(all_q, mode=mode, tag=tag)
    if not nxt:
        end = {"due":"🎉 أنهيت كل أسئلة اليوم!","weak":"💪 خلصت نقاط الضعف!","all":"🎉 مراجعة ممتازة!"}
        await q.edit_message_text(end.get(mode,"✅ انتهت المراجعة."))
        await q.message.reply_text("اختر:", reply_markup=main_kb()); return
    ctx.user_data["quiz_id"] = nxt.id
    await _send_quiz(q, nxt)

# ── Daily Report ───────────────────────────────────────────────────
async def daily_report(ctx: ContextTypes.DEFAULT_TYPE):
    stats = await db.get_stats()
    all_q = await db.all_questions()
    total = sum(q.total_reviews for q in all_q)
    dates = []
    for q in all_q: dates.extend(q.review_dates)
    streak = calculate_streak(dates)
    lvl    = get_level_info(total)
    pred   = analytics.predict_score(all_q)
    if stats["total"] == 0: return
    txt = (
        f"☀️ *صباح الخير!*\n\n"
        f"{lvl['badge']} المستوى {lvl['level']} | XP: {lvl['xp']}/10\n"
        f"🔥 Streak: *{streak}* يوم\n"
        f"⏰ *مستحقة اليوم: {stats['due']}*\n"
        f"📊 درجة متوقعة: *{pred['overall']}%*\n\n"
        f"{'💪 خلّص مراجعة اليوم!' if stats['due']>0 else '✅ كل شيء مراجَع!'}"
    )
    await ctx.bot.send_message(
        chat_id=settings.ALLOWED_USER_ID, text=txt,
        parse_mode="Markdown", reply_markup=main_kb()
    )

# ── Main ───────────────────────────────────────────────────────────
async def menu_search(u: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = u.callback_query; await q.answer()
    await q.edit_message_text("🔍 أرسل: `/search كلمة`", parse_mode="Markdown", reply_markup=main_kb())

def main():
    if not settings.BOT_TOKEN:
        raise RuntimeError("❌ BOT_TOKEN غير مضبوط.")

    app = ApplicationBuilder().token(settings.BOT_TOKEN).build()

    app.job_queue.run_daily(
        daily_report,
        time=__import__("datetime").time(
            hour=settings.DAILY_REPORT_HOUR,
            minute=settings.DAILY_REPORT_MINUTE,
            tzinfo=timezone.utc
        ),
        name="daily_report",
    )

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_add, pattern="^menu_add$")],
        states={
            ADD_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.FORWARDED, add_text)],
            ADD_PRIO: [CallbackQueryHandler(add_prio, pattern="^prio_")],
            ADD_TAGS: [MessageHandler(filters.TEXT, add_tags), CommandHandler("skip", add_tags)],
        },
        fallbacks=[CommandHandler("start", start)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("help",   help_cmd))
    app.add_handler(CommandHandler("ping",   ping_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("list",   cmd_list))
    app.add_handler(CommandHandler("tag",    tag_cmd))
    app.add_handler(CommandHandler("weak",   weak_cmd))
    app.add_handler(CommandHandler("today",  today_cmd))

    app.add_handler(MessageHandler(filters.FORWARDED & filters.POLL, handle_poll))
    app.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, handle_message))

    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(quiz_handler,    pattern="^quiz_"))
    app.add_handler(CallbackQueryHandler(quiz_tag_selected, pattern="^qt_"))
    app.add_handler(CallbackQueryHandler(menu_list,       pattern="^menu_list$"))
    app.add_handler(CallbackQueryHandler(menu_search,     pattern="^menu_search$"))
    app.add_handler(CallbackQueryHandler(menu_stats,      pattern="^menu_stats$"))
    app.add_handler(CallbackQueryHandler(menu_level,      pattern="^menu_level$"))
    app.add_handler(CallbackQueryHandler(menu_export,     pattern="^menu_export$"))
    app.add_handler(CallbackQueryHandler(menu_clear,      pattern="^menu_clear$"))
    app.add_handler(CallbackQueryHandler(menu_quiz_tag,   pattern="^menu_quiz_tag$"))
    app.add_handler(CallbackQueryHandler(clear_decision,  pattern="^clear_(yes|no)$"))
    app.add_handler(CallbackQueryHandler(lambda u,c: _quiz_mode(u,c,"all"),  pattern="^menu_quiz_all$"))
    app.add_handler(CallbackQueryHandler(lambda u,c: _quiz_mode(u,c,"due"),  pattern="^menu_quiz_due$"))
    app.add_handler(CallbackQueryHandler(lambda u,c: _quiz_mode(u,c,"weak"), pattern="^menu_quiz_weak$"))
    app.add_handler(CallbackQueryHandler(lambda u,c: asyncio.create_task(start(u,c)), pattern="^menu_back$"))
    app.add_handler(CallbackQueryHandler(quiz_option, pattern="^qopt_"))

    app.run_polling(drop_pending_updates=True)
    logger.info("🚀 Quiz Master Pro v2 شغال!")

if __name__ == "__main__":
    import asyncio
    asyncio.run(db.init())
    main()
