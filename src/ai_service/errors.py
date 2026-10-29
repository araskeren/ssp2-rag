"""Envelope error ala NestJS/notification-service: {statusCode, error, message, details[]}."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ai_service.logging import get_logger

log = get_logger(__name__)

HTTP_ERROR_NAMES: dict[int, str] = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    413: "Payload Too Large",
    415: "Unsupported Media Type",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}


class ApiError(Exception):
    """Error yang sengaja dilempar handler; dirender ke envelope standar."""

    def __init__(
        self,
        status_code: int,
        message: str,
        details: list[str] | None = None,
        *,
        error: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.details = details or []
        self.error = error or HTTP_ERROR_NAMES.get(status_code, "Error")
        self.headers = dict(headers or {})


def error_body(
    status_code: int, message: str, details: list[str] | None = None, error: str | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "statusCode": status_code,
        "error": error or HTTP_ERROR_NAMES.get(status_code, "Error"),
        "message": message,
    }
    if details:
        body["details"] = details
    return body


def error_response(
    status_code: int,
    message: str,
    details: list[str] | None = None,
    *,
    error: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_body(status_code, message, details, error),
        headers=dict(headers or {}),
    )


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(
            exc.status_code, exc.message, exc.details, error=exc.error, headers=exc.headers
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = (
            exc.detail
            if isinstance(exc.detail, str)
            else HTTP_ERROR_NAMES.get(exc.status_code, "Error")
        )
        return error_response(exc.status_code, detail, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = []
        for err in exc.errors():
            loc = ".".join(str(part) for part in err.get("loc", ()) if part != "body")
            details.append(f"{loc or 'body'}: {err.get('msg', 'tidak valid')}")
        return error_response(400, "validasi request gagal", details)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("error tidak tertangani", error=str(exc))
        return error_response(500, "terjadi kesalahan internal")
