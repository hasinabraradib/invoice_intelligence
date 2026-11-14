"""One error format for every endpoint: {"error": {"code": ..., "message": ...}}."""

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

_CODES = {
    400: "bad_request",
    404: "not_found",
    409: "conflict",
    413: "too_large",
    415: "unsupported_media_type",
    401: "unauthorized",
    429: "rate_limited",
    422: "validation_error",
    503: "unavailable",
}


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[dict[str, Any]] | None = None  # field-level problems for 422


class ErrorResponse(BaseModel):
    error: ErrorDetail


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code, self.code, self.message = status_code, code, message


def not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


def _body(code: str, message: str, details=None) -> dict:
    return ErrorResponse(error=ErrorDetail(code=code, message=message, details=details)).model_dump(
        exclude_none=True
    )


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        retry_after = getattr(exc, "retry_after", None)
        headers = {"Retry-After": str(retry_after)} if retry_after else None
        return JSONResponse(
            _body(exc.code, exc.message), status_code=exc.status_code, headers=headers
        )

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        code = _CODES.get(exc.status_code, "error")
        return JSONResponse(
            _body(code, str(exc.detail)), status_code=exc.status_code, headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Locations and messages only; the rejected input is not echoed back.
        details = [{"loc": [str(p) for p in e["loc"]], "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse(
            _body("validation_error", "The request is not valid.", details), status_code=422
        )


# For OpenAPI docs: `responses=ERRORS` on a route documents the error shape.
ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Not found"},
    422: {"model": ErrorResponse, "description": "Invalid request"},
}
