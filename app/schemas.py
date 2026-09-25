"""نماذج البيانات (طلبات واستجابات الـ API)."""

from pydantic import BaseModel, Field

from app.tones import DEFAULT_TONE, LanguageId, ToneId

MIN_POSTS, MAX_POSTS = 1, 10


class SummarizeRequest(BaseModel):
    url: str = Field(..., description="رابط فيديو يوتيوب")
    language: LanguageId = Field("ar", description="لغة الناتج بغض النظر عن لغة الفيديو")
    tone: ToneId = Field(DEFAULT_TONE, description="أسلوب ونبرة المنشورات")
    num_posts: int = Field(3, ge=MIN_POSTS, le=MAX_POSTS, description="عدد المنشورات")


class VideoSummary(BaseModel):
    """الشكل الذي يُطلب من Claude إرجاعه (Structured Output)."""

    title: str
    summary: str
    key_points: list[str]
    posts: list[str]


class SummarizeResponse(VideoSummary):
    video_id: str
    language: LanguageId
    tone: ToneId
    char_limit: int
    requested_posts: int


class TranscriptSegmentOut(BaseModel):
    text: str
    start: float
    duration: float


class TranscriptResponse(BaseModel):
    video_id: str
    language: str
    language_code: str
    is_generated: bool
    is_translated: bool
    text: str
    segments: list[TranscriptSegmentOut]
