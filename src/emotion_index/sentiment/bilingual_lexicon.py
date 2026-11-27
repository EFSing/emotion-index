"""Deterministic English and Chinese fallback for V2 post sentiment."""

import re

from emotion_index.domain.market import EmotionState, MarketPost, PostSentiment
from emotion_index.sentiment.scorer import BEARISH_TERMS, BULLISH_TERMS, TOKEN_PATTERN

MODEL_NAME = "bilingual_lexicon_v1"
SCORING_MODE = "DEGRADED_LEXICON"

POSITIVE_ZH = (
    "看多", "利好", "上涨", "反弹", "放量突破", "涨停", "大涨", "走强", "抄底", "牛市", "乐观", "踏空",
)
NEGATIVE_ZH = (
    "看空", "利空", "下跌", "跌停", "暴跌", "走弱", "割肉", "爆仓", "恐慌", "崩盘", "熊市", "清仓",
)
EXTREME_ZH = frozenset({"涨停", "大涨", "踏空", "跌停", "暴跌", "爆仓", "恐慌", "崩盘"})
EXTREME_EN = frozenset({"fomo", "moon", "crash", "panic", "capitulation"})
POSITIVE_EN = BULLISH_TERMS | {"fomo", "moon"}
NEGATIVE_EN = BEARISH_TERMS | {"capitulation"}
ZH_TERMS = re.compile("|".join(sorted(POSITIVE_ZH + NEGATIVE_ZH, key=len, reverse=True)))


def score_post(post: MarketPost) -> PostSentiment:
    """Use matched terms with a weaker score for a lone non-extreme signal."""
    text = post.post.text
    tokens = set(TOKEN_PATTERN.findall(text.lower()))
    terms = set(ZH_TERMS.findall(text))
    bullish = tokens & POSITIVE_EN | terms.intersection(POSITIVE_ZH)
    bearish = tokens & NEGATIVE_EN | terms.intersection(NEGATIVE_ZH)
    extreme = EXTREME_EN | EXTREME_ZH
    positive = len(bullish) + len(bullish & extreme)
    negative = len(bearish) + len(bearish & extreme)
    polarity = (positive - negative) / max(2, positive + negative)
    if polarity == 0:
        state = EmotionState.NEUTRAL
    elif polarity > 0:
        state = EmotionState.EUPHORIA_FOMO if polarity >= 0.75 and bullish & extreme else EmotionState.OPTIMISTIC
    else:
        state = EmotionState.PANIC_CAPITULATION if polarity <= -0.75 and bearish & extreme else EmotionState.ANXIOUS
    matches = sorted(bullish | bearish)
    return PostSentiment(
        post, round(polarity, 6), state, sector=post.sector,
        reason_short="词典命中 " + "、".join(matches[:8]) if matches else "未命中情绪词",
    )
