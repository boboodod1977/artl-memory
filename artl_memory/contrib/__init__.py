# -*- coding: utf-8 -*-
"""可选扩展：核心零依赖的承诺不变，这里的每一样都是"自带聪明"的插口件。"""

from .semantic import EmbeddingScorer, fastembed_scorer

__all__ = ["EmbeddingScorer", "fastembed_scorer"]
