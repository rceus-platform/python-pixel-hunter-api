"""Consolidated router for all API endpoints."""

from fastapi import APIRouter

from app.api.endpoints import image, search

api_router = APIRouter()

api_router.include_router(search.router, prefix="/search", tags=["Search"])
api_router.include_router(image.router, tags=["Images"])
