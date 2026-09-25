"""تلخيص النص وتوليد المنشورات باستخدام Claude."""

from typing import TypeVar

import anthropic
from pydantic import BaseModel

from app.config import settings
from app.schemas import VideoSummary
from app.tones import LANGUAGE_NAMES, TONES, Tone

BASE_PROMPT = """أنت محرر محتوى محترف متخصص في صناعة المحتوى لمنصات التواصل الاجتماعي.
ستتلقى نصًا مفرّغًا من فيديو يوتيوب، وقد يكون بأي لغة.

المطلوب:
- title: عنوان قصير وواضح يعبّر عن محتوى الفيديو.
- summary: ملخص من فقرة أو فقرتين يغطي الفكرة الأساسية.
- key_points: أهم النقاط (من 3 إلى 7 نقاط موجزة).
- posts: منشورات جاهزة للنشر على {platform}.

قواعد عامة:
- التزم بالمعلومات الواردة في النص فقط ولا تخترع حقائق أو أرقامًا.
- كل منشور مستقل بذاته ويُفهم دون الحاجة لمشاهدة الفيديو.
- لا تكرّر الفكرة نفسها: اجعل كل منشور يتناول نقطة أو زاوية مختلفة من الفيديو.
- لا تضع روابط في المنشورات.

<output_language>
اكتب جميع الحقول (title وsummary وkey_points وposts) باللغة: {language}.
التزم بهذه اللغة حصرًا مهما كانت لغة الفيديو الأصلية؛ ترجم المعنى بدقة وبصياغة طبيعية
كأنك تكتب بها أصلًا، ولا تُبقِ جملًا بلغة الفيديو. يُستثنى فقط أسماء الأعلام والعلامات
التجارية والمصطلحات التقنية الشائعة.
</output_language>

<tone>
{tone_guide}
</tone>

<post_count>
عدد المنشورات المطلوب هو {num_posts} بالضبط: لا أكثر ولا أقل.
</post_count>"""

MORE_POSTS_PROMPT = """أنت محرر محتوى محترف. بناءً على ملخص فيديو يوتيوب أدناه، اكتب {missing}
منشورات جديدة إضافية بالضبط، للنشر على {platform}.

- اللغة: {language} حصرًا.
- يجب أن تختلف في فكرتها وصياغتها عن المنشورات الموجودة.
- التزم بالمعلومات الواردة في الملخص فقط.

<tone>
{tone_guide}
</tone>"""

# عدد المحاولات الإضافية لاستكمال المنشورات إن أعاد النموذج عددًا أقل من المطلوب
MAX_TOP_UP_ATTEMPTS = 2

_client = anthropic.Anthropic()

T = TypeVar("T", bound=BaseModel)


class SummarizerError(Exception):
    """خطأ أثناء التلخيص."""


class _MorePosts(BaseModel):
    posts: list[str]


def build_system_prompt(language: str, tone: Tone, num_posts: int) -> str:
    return BASE_PROMPT.format(
        platform=tone.platform,
        language=LANGUAGE_NAMES[language],
        tone_guide=tone.guide,
        num_posts=num_posts,
    )


def _call(system: str, user: str, output_format: type[T]) -> T:
    try:
        response = _client.beta.messages.parse(
            model=settings.claude_model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=output_format,
            # عند رفض الطلب من مصنّفات الأمان يعيد الخادم المحاولة بنموذج بديل تلقائيًا
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.RateLimitError as exc:
        raise SummarizerError("تم تجاوز حد الطلبات، حاول لاحقًا") from exc
    except anthropic.APIStatusError as exc:
        raise SummarizerError(f"خطأ من Claude API ({exc.status_code})") from exc
    except anthropic.APIConnectionError as exc:
        raise SummarizerError("تعذّر الاتصال بـ Claude API") from exc

    if response.stop_reason == "refusal":
        raise SummarizerError("رفض النموذج معالجة هذا المحتوى")
    if response.stop_reason == "max_tokens" or response.parsed_output is None:
        raise SummarizerError("لم يكتمل الرد، جرّب فيديو أقصر")
    return response.parsed_output


def _clean(posts: list[str]) -> list[str]:
    """يحذف المنشورات الفارغة والمكرّرة مع الحفاظ على الترتيب."""
    seen: set[str] = set()
    result = []
    for post in posts:
        post = post.strip()
        if post and post not in seen:
            seen.add(post)
            result.append(post)
    return result


def _top_up(result: VideoSummary, language: str, tone: Tone, num_posts: int) -> list[str]:
    """يطلب منشورات إضافية حتى يصل العدد إلى المطلوب."""
    posts = _clean(result.posts)
    for _ in range(MAX_TOP_UP_ATTEMPTS):
        missing = num_posts - len(posts)
        if missing <= 0:
            break
        system = MORE_POSTS_PROMPT.format(
            missing=missing,
            platform=tone.platform,
            language=LANGUAGE_NAMES[language],
            tone_guide=tone.guide,
        )
        existing = "\n".join(f"- {p}" for p in posts)
        user = (
            f"<summary>\n{result.summary}\n</summary>\n\n"
            f"<key_points>\n" + "\n".join(f"- {k}" for k in result.key_points) + "\n</key_points>\n\n"
            f"<existing_posts>\n{existing}\n</existing_posts>"
        )
        posts = _clean(posts + _call(system, user, _MorePosts).posts)
    return posts[:num_posts]


def summarize(
    transcript: str, language: str = "ar", num_posts: int = 3, tone_id: str = "x"
) -> VideoSummary:
    tone = TONES[tone_id]
    system = build_system_prompt(language, tone, num_posts)
    user = f"<transcript>\n{transcript}\n</transcript>"

    result = _call(system, user, VideoSummary)
    result.posts = _top_up(result, language, tone, num_posts)
    return result
