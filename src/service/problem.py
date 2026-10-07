"""
RFC 9457 `application/problem+json` for every error the gateway answers, including a route that does not exist and a request that does not validate.
"""
import http

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

MEDIA_TYPE = "application/problem+json"


class Problem(Exception):
    def __init__(self, status, detail, **extra):
        self.status, self.detail, self.extra = status, detail, extra


def _response(status, detail, **extra):
    body = {"type": "about:blank", "title": http.HTTPStatus(status).phrase, "status": status, "detail": detail, **extra}
    return JSONResponse(body, status_code=status, media_type=MEDIA_TYPE)


def install(app):
    @app.exception_handler(Problem)
    async def problem(request: Request, error: Problem):
        return _response(error.status, error.detail, **error.extra)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return _response(error.status_code, str(error.detail))

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, error: RequestValidationError):
        problems = jsonable_encoder(error.errors())
        detail = "; ".join("%s: %s" % (".".join(str(part) for part in item["loc"]), item["msg"]) for item in problems)
        return _response(422, detail, errors=problems)
