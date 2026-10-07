"""
The `/v1` routes, each a thin layer over the gateway.
"""
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class Health(BaseModel):
    status: str


@router.get("/health", response_model=Health)
def health():
    return Health(status="ok")
