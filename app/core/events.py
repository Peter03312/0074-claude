"""规范事件与来源链单步。

整个系统只使用两类规范事件：

* ``pitch`` 为 ``int`` 的起音事件（连音已合并为一次起音）；
* ``pitch`` 为 ``None`` 的休止事件（连续休止已合并）。

``ChainStep`` 表示来源链中经过的一个 DAG 节点；repeat 节点带零基
``iteration``，phrase 叶子带基础项 ``item_index``。
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True, slots=True)
class Event:
    pitch: int | None  # None 表示休止
    duration: Fraction

    @property
    def is_rest(self) -> bool:
        return self.pitch is None

    def same_music(self, other: Event) -> bool:
        return self.pitch == other.pitch and self.duration == other.duration


@dataclass(frozen=True, slots=True)
class ChainStep:
    node_id: int
    iteration: int | None = None
    item_index: int | None = None
