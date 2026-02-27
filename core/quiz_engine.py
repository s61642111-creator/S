import random
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from core.models import Question

def _now() -> datetime:
    return datetime.now(timezone.utc)

class SM2Engine:
    MIN_EASE = 1.3

    @staticmethod
    def review(q: Question, quality: int) -> Question:
        if quality < 3:
            q.repetitions = 0
            q.interval = 1
            q.streak = 0
            q.wrong_count += 1
        else:
            q.repetitions += 1
            q.streak += 1
            if q.repetitions == 1:
                q.interval = 1
            elif q.repetitions == 2:
                q.interval = 6
            else:
                q.interval = round(q.interval * q.ease_factor, 1)
            q.correct_count += 1
            if quality == 5:
                q.interval = round(q.interval * 1.3, 1)

        q.ease_factor = max(
            SM2Engine.MIN_EASE,
            q.ease_factor + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)
        )

        now = _now()
        q.next_review = (now + timedelta(days=max(1, q.interval))).isoformat()
        q.last_review = now.isoformat()
        q.total_reviews += 1
        q.review_dates.append(now.date().isoformat())

        if q.wrong_count >= 3:
            q.priority = "urgent"
        elif q.correct_count >= 5 and q.priority == "urgent":
            q.priority = "normal"
        elif q.correct_count >= 10 and q.priority == "normal":
            q.priority = "low"
        return q

    @staticmethod
    def get_quality(answer: str) -> int:
        return {"again": 0, "hard": 3, "good": 4, "easy": 5}.get(answer, 3)


def get_next_question(questions: List[Question], mode: str = "all",
                      tag: Optional[str] = None) -> Optional[Question]:
    items = questions
    if tag:
        items = [q for q in items if tag in q.tags]
    if not items:
        return None
    now = _now().isoformat()
    if mode == "due":
        items = [q for q in items if q.next_review and q.next_review <= now]
        return items[0] if items else None
    elif mode == "weak":
        return sorted(items, key=lambda q: (q.ease_factor, -q.wrong_count))[0]
    min_r = min(q.total_reviews for q in items)
    candidates = [q for q in items if q.total_reviews == min_r]
    return random.choice(candidates)


def get_level_info(total_reviews: int) -> dict:
    level = min(total_reviews // 10 + 1, 100)
    xp = total_reviews % 10
    badges = {1:"🌱",5:"🌿",10:"⭐",20:"🌟",30:"💫",40:"🏆",50:"👑",75:"🔱",100:"🔥"}
    badge = "🌱"
    for t in sorted(badges.keys(), reverse=True):
        if level >= t:
            badge = badges[t]
            break
    return {"level": level, "xp": xp, "badge": badge}


def calculate_streak(review_dates: List[str]) -> int:
    if not review_dates:
        return 0
    dates = sorted(set(d[:10] for d in review_dates), reverse=True)
    today = _now().date()
    streak = 0
    for i, d in enumerate(dates):
        if d == (today - timedelta(days=i)).isoformat():
            streak += 1
        else:
            break
    return streak

engine = SM2Engine()
