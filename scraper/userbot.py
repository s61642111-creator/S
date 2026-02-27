"""
الصائد الصامت — UserBot بـ Telethon
شغّله منفصلاً: python -m scraper.userbot
"""
import asyncio, logging, re, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telethon import TelegramClient, events
from config.settings import settings
from core.database import db
from core.models import Question

logging.basicConfig(level=logging.INFO, format="%(asctime)s [USERBOT] %(message)s")
logger = logging.getLogger("userbot")

client = TelegramClient(settings.SESSION_NAME, settings.API_ID, settings.API_HASH)
pending_polls: dict = {}

def clean_text(raw: str) -> str:
    lines = raw.splitlines()
    cleaned = []
    for line in lines:
        s = line.strip()
        if not s: continue
        if any(ch in s for ch in ["⏳","⌛","⏱","⏰"]): continue
        low = s.lower()
        if any(kw in low for kw in ["ثانية","ثوان","الوقت المتبقي","time left","sec"]):
            if len(s) <= 30: continue
        if re.fullmatch(r"[0-9]{1,2}[:\.][0-9]{1,2}", s): continue
        cleaned.append(line)
    return "\n".join(cleaned)

async def notify_bot(text: str, qid: int):
    try:
        await client.send_message(
            settings.ALLOWED_USER_ID,
            f"🤖 *الصائد التقط سؤالاً خطأ!*\n🆔 #{qid}\n```\n{text[:200]}\n```",
            parse_mode="markdown"
        )
    except Exception as e:
        logger.error(f"Notify error: {e}")

@client.on(events.NewMessage(chats=settings.WATCHED_CHANNELS or None))
async def on_new_message(event):
    msg = event.message
    if msg.poll:
        poll = msg.poll.poll
        labels = ["أ","ب","ج","د","هـ","و"]
        lines  = [poll.question.text]
        opts   = []
        for i, a in enumerate(poll.answers):
            label = labels[i] if i < len(labels) else str(i+1)
            lines.append(f"{label}) {a.text.text}")
            opts.append(a.text.text)
        correct_id = getattr(msg.poll.results, "correct_option_id", None)
        if correct_id is not None and 0 <= correct_id < len(poll.answers):
            cl = labels[correct_id] if correct_id < len(labels) else str(correct_id+1)
            lines.append(f"\n✅ الإجابة: {cl}) {poll.answers[correct_id].text.text}")
        pending_polls[msg.id] = {
            "text": "\n".join(lines), "options": opts,
            "correct_index": correct_id or 0
        }
        return

    text = msg.text or msg.message or ""
    if not text or len(text) < 20: return
    if not re.search(r"^[أبجدهو]\)", text, re.MULTILINE): return
    cleaned = clean_text(text)
    channel = ""
    try:
        chat = await event.get_chat()
        channel = getattr(chat,"username","") or getattr(chat,"title","")
    except: pass
    q = Question(id=0, text=cleaned, source_channel=channel,
                 auto_captured=True, priority="normal")
    qid = await db.add_question(q)
    logger.info(f"📥 سؤال نصي #{qid} من {channel}")

@client.on(events.MessageEdited(chats=settings.WATCHED_CHANNELS or None))
async def on_poll_answered(event):
    msg = event.message
    if not msg.poll: return
    poll_data = pending_polls.get(msg.id)
    if not poll_data: return
    results = msg.poll.results
    if not results or not results.results: return
    chosen  = next((r for r in results.results if getattr(r,"chosen",False)), None)
    if not chosen: return
    correct_id = getattr(results, "correct_option_id", None)
    chosen_id  = getattr(chosen, "option", None)
    if correct_id is not None and chosen_id != correct_id:
        channel = ""
        try:
            chat = await event.get_chat()
            channel = getattr(chat,"username","") or getattr(chat,"title","")
        except: pass
        q = Question(
            id=0, text=poll_data["text"],
            options=poll_data["options"],
            correct_index=poll_data.get("correct_index",0),
            source_channel=channel,
            auto_captured=True, priority="urgent"
        )
        qid = await db.add_question(q)
        await notify_bot(poll_data["text"], qid)
        logger.info(f"❗ سؤال خاطئ #{qid} محفوظ")
        del pending_polls[msg.id]

async def main():
    logger.info("🚀 الصائد الصامت يبدأ...")
    await db.init()
    await client.start(phone=settings.PHONE_NUMBER)
    logger.info(f"✅ متصل — يراقب: {settings.WATCHED_CHANNELS or 'كل القنوات'}")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
