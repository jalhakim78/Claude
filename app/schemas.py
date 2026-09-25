"""نماذج البيانات (طلبات واستجابات الـ API)."""

from pydantic import BaseModel, Field, model_validator

from app.tones import DEFAULT_TONE, LanguageId, ToneId

MIN_POSTS, MAX_POSTS = 1, 10


class SummarizeRequest(BaseModel):
    url: str = Field(..., description="رابط فيديو يوتيوب")
    language: LanguageId = Field("ar", description="لغة الناتج بغض النظر عن لغة الفيديو")
    tone: ToneId = Field(DEFAULT_TONE, description="أسلوب ونبرة المنشورات")
    num_posts: int = Field(3, ge=MIN_POSTS, le=MAX_POSTS, description="عدد المنشورات أو تغريدات الثريد")
    thread: bool = Field(False, description="توليد ثريد متصل لـ X بدل منشورات مستقلة")

    @model_validator(mode="after")
    def _thread_needs_two(self):
        if self.thread and self.num_posts < 2:
            raise ValueError("الثريد يحتاج تغريدتين على الأقل")
        return self


class Quote(BaseModel):
    text: str
    translation: str


class VideoSummary(BaseModel):
    """الشكل الذي يُطلب من Claude إرجاعه (Structured Output)."""

    title: str
    summary: str
    key_points: list[str]
    posts: list[str]
    quotes: list[Quote]


class UsageOut(BaseModel):
    plan: str
    used: int
    limit: int | None
    remaining: int | None


class SummarizeResponse(VideoSummary):
    video_id: str
    language: LanguageId
    tone: ToneId
    char_limit: int
    requested_posts: int
    thread: bool
    watermarked: bool
    usage: UsageOut


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
