from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime, timezone

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

@dataclass
class Question:
    id: int
    text: str
    options: List[str] = field(default_factory=list)
    correct_index: int = 0
    explanation: str = ""
    tags: List[str] = field(default_factory=list)
    priority: str = "normal"
    source_channel: str = ""
    auto_captured: bool = False
    media_type: Optional[str] = None
    media_id: Optional[str] = None
    ease_factor: float = 2.5
    interval: float = 0
    repetitions: int = 0
    next_review: str = field(default_factory=_now)
    last_review: Optional[str] = None
    total_reviews: int = 0
    correct_count: int = 0
    wrong_count: int = 0
    streak: int = 0
    created_at: str = field(default_factory=_now)
    review_dates: List[str] = field(default_factory=list)
