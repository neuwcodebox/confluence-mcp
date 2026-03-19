from __future__ import annotations

from pydantic import BaseModel, Field


class PageSummary(BaseModel):
    page_id: str
    title: str
    url: str | None = None
    excerpt_markdown: str | None = None


class SearchResult(BaseModel):
    items: list[PageSummary]
    next_cursor: str | None = None


class PageContent(BaseModel):
    page_id: str
    title: str
    version: str | None = None
    body_markdown: str
    toc_markdown: str | None = None
    section: str | None = None
    truncated: bool = False
    cache_hit: bool = False
    last_modified: str | None = None
    author: str | None = None


class ChildPageListResult(BaseModel):
    parent_page_id: str
    parent_title: str | None = None
    items: list[PageSummary]
    next_cursor: str | None = None


class AncestorItem(BaseModel):
    page_id: str
    title: str


class AncestorResult(BaseModel):
    page_id: str
    breadcrumb: list[AncestorItem] = Field(default_factory=list)

