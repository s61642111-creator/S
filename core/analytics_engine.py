import math
from typing import List
from core.models import Question
from core.quiz_engine import calculate_streak, get_level_info

class AnalyticsEngine:

    @staticmethod
    def predict_score(questions: List[Question]) -> dict:
        if not questions:
            return {"overall": 0.0, "by_tag": {}, "confidence": "منخفضة", "total_reviewed": 0}
        tag_data = {}
        untagged = {"correct": 0, "total": 0}
        for q in questions:
            if q.total_reviews == 0:
                continue
            if not q.tags:
                untagged["correct"] += q.correct_count
                untagged["total"]   += q.total_reviews
                continue
            for tag in q.tags:
                if tag not in tag_data:
                    tag_data[tag] = {"correct": 0, "total": 0}
                tag_data[tag]["correct"] += q.correct_count
                tag_data[tag]["total"]   += q.total_reviews

        by_tag = {}
        weighted_sum = total_weight = 0.0
        for tag, data in tag_data.items():
            if data["total"] > 0:
                rate   = data["correct"] / data["total"]
                weight = math.log1p(data["total"])
                by_tag[tag] = {"score": round(rate*100,1),
                               "correct": data["correct"],
                               "total": data["total"]}
                weighted_sum += rate * weight
                total_weight += weight

        if untagged["total"] > 0:
            rate   = untagged["correct"] / untagged["total"]
            weight = math.log1p(untagged["total"])
            weighted_sum += rate * weight
            total_weight += weight

        overall = round((weighted_sum/total_weight*100) if total_weight else 0, 1)
        total_q = sum(q.total_reviews for q in questions)
        confidence = "عالية" if total_q >= 200 else "متوسطة" if total_q >= 50 else "منخفضة"
        return {"overall": overall, "by_tag": by_tag,
                "confidence": confidence, "total_reviewed": total_q}

    @staticmethod
    def get_full_report(questions: List[Question]) -> dict:
        if not questions:
            return {"total_questions":0,"total_reviews":0,"streak_days":0,
                    "level":1,"badge":"🌱","xp":0,"strong_count":0,
                    "weak_count":0,"prediction":{},"auto_captured":0}
        total_reviews = sum(q.total_reviews for q in questions)
        lvl = get_level_info(total_reviews)
        all_dates = []
        for q in questions:
            all_dates.extend(q.review_dates)
        streak = calculate_streak(all_dates)
        strong = [q for q in questions if q.ease_factor >= 2.5 and q.correct_count >= 3]
        weak   = sorted(questions, key=lambda q: q.ease_factor)[:5]
        return {
            "total_questions": len(questions),
            "total_reviews":   total_reviews,
            "streak_days":     streak,
            "level":  lvl["level"],
            "badge":  lvl["badge"],
            "xp":     lvl["xp"],
            "strong_count": len(strong),
            "weak_count":   len(weak),
            "prediction":   AnalyticsEngine.predict_score(questions),
            "auto_captured": sum(1 for q in questions if q.auto_captured),
        }

analytics = AnalyticsEngine()
