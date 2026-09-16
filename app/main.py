"""FastAPI 应用：请求校验、压缩编译、比对与响应序列化。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.compare import compare_terms
from app.core.compiler import Compiler
from app.core.errors import SemanticError
from app.models import AnalyzeRequest, AnalyzeResponse
from app.serialization import (
    difference_to_payload,
    fraction_out,
)

app = FastAPI(
    title="旋律变奏卡比对 API",
    version="1.0.0",
    description="对离散乐句 DAG 做压缩求值并定位手抄变奏的第一处差异。",
)


@app.exception_handler(SemanticError)
async def semantic_error_handler(_: Request, exc: SemanticError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {
                    "loc": exc.loc,
                    "msg": exc.message,
                    "type": "semantic_error",
                }
            ]
        },
    )


@app.exception_handler(RecursionError)
async def recursion_error_handler(_: Request, __: RecursionError) -> JSONResponse:
    # 节点数已被限制在 2000、递归上限也已调高；走到这里说明表达式嵌套
    # 深度仍然过深，按“规模越界”整单拒绝，而不是抛出 500。
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {
                    "loc": "/nodes",
                    "msg": "表达式嵌套深度超过可处理上限",
                    "type": "depth_limit",
                }
            ]
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = []
    for err in exc.errors():
        loc = _json_pointer(err.get("loc", []))
        errors.append(
            {"loc": loc, "msg": err.get("msg", ""), "type": err.get("type", "")}
        )
    return JSONResponse(status_code=422, content={"detail": errors})


_DISCRIMINATOR_TAGS = {
    "phrase",
    "concat",
    "repeat",
    "transpose",
    "pitch_mirror",
    "reverse",
    "stretch",
    "note",
    "rest",
}


def _json_pointer(loc: tuple[Any, ...]) -> str:
    """把 Pydantic 的 loc 元组规范化为以请求体为根的 JSON Pointer。

    去掉 FastAPI 的 "body" 前缀，以及判别式联合在校验嵌套成员时插入的
    判别值标签段（如 /nodes/0/phrase/items/0/rest/duration/d 中的
    "phrase"/"rest"）。这些标签不是请求字段；末尾的真字段名（如 /op）保留。
    """
    raw = [p for p in loc if p != "body"]
    parts: list[str] = []
    last = len(raw) - 1
    for i, piece in enumerate(raw):
        text = str(piece)
        if i != last and text in _DISCRIMINATOR_TAGS:
            continue
        parts.append(text)
    return "/" + "/".join(parts) if parts else ""


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    compiler = Compiler(req.nodes)
    compiler.compile_all()
    a = compiler.term_for(req.expected_root, "/expected_root")
    b = compiler.term_for(req.handwritten_root, "/handwritten_root")

    diff = compare_terms(a, b)
    equal = diff is None
    return AnalyzeResponse(
        equal=equal,
        event_count=a.count,
        total_duration=fraction_out(a.duration),
        first_difference=difference_to_payload(diff) if diff else None,
    )
