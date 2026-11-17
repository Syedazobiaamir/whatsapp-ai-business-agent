"""Shared schema pieces."""
from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    """Base for response schemas: can be built straight from a SQLAlchemy object."""
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    """A page of results: what list endpoints return."""
    items: list[T]
    total: int    # total matching rows (for "Showing 1-20 of 135")
    page: int
    size: int


class Message(BaseModel):
    detail: str
