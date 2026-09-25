"""نماذج البيانات (طلبات واستجابات الـ API)."""

from typing import Literal

from pydantic import BaseModel, Field


class SummarizeRequest(BaseModel):
    url: str = Field(..., description="رابط فيديو يوتيوب")
    language: Literal["ar", "en"] = "ar"
    num_posts: int = Field(3, ge=1, le=10)


class VideoSummary(BaseModel):
    """الشكل الذي يُطلب من Claude إرجاعه (Structured Output)."""

    title: str
    summary: str
    key_points: list[str]
    x_posts: list[str]


class SummarizeResponse(VideoSummary):
    video_id: str
