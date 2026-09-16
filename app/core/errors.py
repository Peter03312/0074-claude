"""语义错误与规模越界异常。

校验器（Pydantic）负责结构与字面量校验，错误定位以 JSON Pointer 路径形式
挂在 ``SemanticError.loc`` 上。所有错误最终由 FastAPI 统一转成 422。
"""

from __future__ import annotations


class SemanticError(Exception):
    """整单拒绝的语义错误（坏分数、非法连音、越界、规模限制等）。"""

    def __init__(self, message: str, loc: str):
        super().__init__(message)
        self.message = message
        self.loc = loc


def join(loc: str, part: str | int) -> str:
    """把一个 JSON 对象键或数组下标拼进 JSON Pointer 路径。"""
    if isinstance(part, int):
        return f"{loc}/{part}"
    return f"{loc}/{part}"
