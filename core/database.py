import json, os, aiosqlite
from typing import List, Optional
from datetime import datetime, timezone
from core.models import Question

DB_PATH = os.environ.get("DB_PATH", "data/quiz.db")

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS questions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    text          TEXT    NOT NULL,
    options       TEXT    DEFAULT '[]',
    correct_index INTEGER DEFAULT 0,
    explanation   TEXT    DEFAULT '',
    tags          TEXT    DEFAULT '[]',
    priority      TEXT    DEFAULT 'normal',
    source_channel TEXT   DEFAULT '',
    auto_captured INTEGER DEFAULT 0,
    media_type    TEXT,
    media_id      TEXT,
    ease_factor   REAL    DEFAULT 2.5,
    interval      REAL    DEFAULT 0,
    repetitions   INTEGER DEFAULT 0,
    next_review   TEXT,
    last_review   TEXT,
    total_reviews INTEGER DEFAULT 0,
    correct_count INTEGER DEFAULT 0,
    wrong_count   INTEGER DEFAULT 0,
    streak        INTEGER DEFAULT 0,
    created_at    TEXT,
    review_dates  TEXT    DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS sync_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,
    quality     INTEGER NOT NULL,
    timestamp   TEXT    NOT NULL,
    synced      INTEGER DEFAULT 0
);
"""

def _row(r) -> Question:
    return Question(
        id=r[0], text=r[1],
        options=json.loads(r[2] or "[]"),
        correct_index=r[3], explanation=r[4] or "",
        tags=json.loads(r[5] or "[]"),
        priority=r[6] or "normal",
        source_channel=r[7] or "",
        auto_captured=bool(r[8]),
        media_type=r[9], media_id=r[10],
        ease_factor=r[11], interval=r[12],
        repetitions=r[13], next_review=r[14] or "",
        last_review=r[15], total_reviews=r[16],
        correct_count=r[17], wrong_count=r[18],
        streak=r[19], created_at=r[20] or "",
        review_dates=json.loads(r[21] or "[]"),
    )

class Database:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)

    async def init(self):
        async with aiosqlite.connect(self.path) as d:
            await d.executescript(CREATE_SQL)
            await d.commit()

    async def add_question(self, q: Question) -> int:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as d:
            cur = await d.execute(
                """INSERT INTO questions
                   (text,options,correct_index,explanation,tags,priority,
                    source_channel,auto_captured,media_type,media_id,
                    ease_factor,interval,repetitions,next_review,last_review,
                    total_reviews,correct_count,wrong_count,streak,created_at,review_dates)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (q.text, json.dumps(q.options, ensure_ascii=False),
                 q.correct_index, q.explanation,
                 json.dumps(q.tags, ensure_ascii=False),
                 q.priority, q.source_channel, int(q.auto_captured),
                 q.media_type, q.media_id,
                 q.ease_factor, q.interval, q.repetitions,
                 q.next_review or now, q.last_review,
                 q.total_reviews, q.correct_count, q.wrong_count,
                 q.streak, now, json.dumps(q.review_dates, ensure_ascii=False))
            )
            await d.commit()
            return cur.lastrowid

    async def get_question(self, qid: int) -> Optional[Question]:
        async with aiosqlite.connect(self.path) as d:
            async with d.execute("SELECT * FROM questions WHERE id=?", (qid,)) as c:
                r = await c.fetchone()
                return _row(r) if r else None

    async def update_question(self, q: Question):
        async with aiosqlite.connect(self.path) as d:
            await d.execute(
                """UPDATE questions SET
                   ease_factor=?,interval=?,repetitions=?,next_review=?,last_review=?,
                   total_reviews=?,correct_count=?,wrong_count=?,streak=?,
                   priority=?,tags=?,review_dates=? WHERE id=?""",
                (q.ease_factor, q.interval, q.repetitions, q.next_review, q.last_review,
                 q.total_reviews, q.correct_count, q.wrong_count, q.streak,
                 q.priority, json.dumps(q.tags, ensure_ascii=False),
                 json.dumps(q.review_dates, ensure_ascii=False), q.id)
            )
            await d.commit()

    async def delete_question(self, qid: int) -> bool:
        async with aiosqlite.connect(self.path) as d:
            c = await d.execute("DELETE FROM questions WHERE id=?", (qid,))
            await d.commit()
            return c.rowcount > 0

    async def all_questions(self) -> List[Question]:
        async with aiosqlite.connect(self.path) as d:
            async with d.execute("SELECT * FROM questions ORDER BY id") as c:
                return [_row(r) for r in await c.fetchall()]

    async def get_due_questions(self, limit: int = 50) -> List[Question]:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.path) as d:
            async with d.execute(
                "SELECT * FROM questions WHERE next_review<=? ORDER BY next_review LIMIT ?",
                (now, limit)
            ) as c:
                return [_row(r) for r in await c.fetchall()]

    async def get_weakest(self, limit: int = 5) -> List[Question]:
        async with aiosqlite.connect(self.path) as d:
            async with d.execute(
                "SELECT * FROM questions ORDER BY ease_factor ASC, wrong_count DESC LIMIT ?",
                (limit,)
            ) as c:
                return [_row(r) for r in await c.fetchall()]

    async def search(self, term: str) -> List[Question]:
        async with aiosqlite.connect(self.path) as d:
            async with d.execute(
                "SELECT * FROM questions WHERE text LIKE ? LIMIT 20",
                (f"%{term}%",)
            ) as c:
                return [_row(r) for r in await c.fetchall()]

    async def get_by_tag(self, tag: str) -> List[Question]:
        return [q for q in await self.all_questions() if tag in q.tags]

    async def get_all_tags(self) -> List[str]:
        tags = set()
        for q in await self.all_questions():
            tags.update(q.tags)
        return sorted(tags)

    async def clear_all(self):
        async with aiosqlite.connect(self.path) as d:
            await d.execute("DELETE FROM questions")
            await d.execute("DELETE FROM sync_queue")
            await d.commit()

    async def get_stats(self) -> dict:
        all_q = await self.all_questions()
        now = datetime.now(timezone.utc).isoformat()
        total = len(all_q)
        due = sum(1 for q in all_q if q.next_review and q.next_review <= now)
        by_priority = {}
        total_reviews = sum(q.total_reviews for q in all_q)
        auto_count = sum(1 for q in all_q if q.auto_captured)
        avg_ease = sum(q.ease_factor for q in all_q)/total if total else 2.5
        for q in all_q:
            by_priority[q.priority] = by_priority.get(q.priority, 0) + 1
        return {"total": total, "due": due, "by_priority": by_priority,
                "total_reviews": total_reviews, "auto_captured": auto_count,
                "avg_ease": round(avg_ease, 2)}

    async def add_sync_item(self, question_id: int, quality: int, timestamp: str):
        async with aiosqlite.connect(self.path) as d:
            await d.execute(
                "INSERT INTO sync_queue (question_id,quality,timestamp) VALUES (?,?,?)",
                (question_id, quality, timestamp)
            )
            await d.commit()

    async def get_pending_sync(self) -> list:
        async with aiosqlite.connect(self.path) as d:
            async with d.execute(
                "SELECT id,question_id,quality,timestamp FROM sync_queue WHERE synced=0"
            ) as c:
                return [{"sync_id":r[0],"question_id":r[1],"quality":r[2],"timestamp":r[3]}
                        for r in await c.fetchall()]

    async def mark_synced(self, sync_id: int):
        async with aiosqlite.connect(self.path) as d:
            await d.execute("UPDATE sync_queue SET synced=1 WHERE id=?", (sync_id,))
            await d.commit()

db = Database()
