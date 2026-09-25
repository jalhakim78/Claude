"""تلخيص النص وتوليد منشورات X باستخدام Claude."""

import anthropic

from app.config import settings
from app.schemas import VideoSummary

SYSTEM_PROMPT = """أنت محرر محتوى محترف. ستتلقى نصًا مفرّغًا من فيديو يوتيوب.
المطلوب:
- title: عنوان قصير وواضح يعبّر عن محتوى الفيديو.
- summary: ملخص من فقرة أو فقرتين يغطي الفكرة الأساسية.
- key_points: أهم النقاط (من 3 إلى 7 نقاط موجزة).
- x_posts: منشورات جاهزة للنشر على منصة X، كل منشور لا يتجاوز 280 حرفًا،
  بأسلوب جذاب ومستقل بذاته، مع وسم أو وسمين مناسبين كحد أقصى.
التزم بالمعلومات الواردة في النص فقط ولا تخترع حقائق."""

_client = anthropic.Anthropic()

X_POST_LIMIT = 280


class SummarizerError(Exception):
    """خطأ أثناء التلخيص."""


def summarize(transcript: str, language: str = "ar", num_posts: int = 3) -> VideoSummary:
    lang_name = "العربية" if language == "ar" else "الإنجليزية"
    user_prompt = (
        f"اكتب الناتج باللغة {lang_name}، وولّد {num_posts} منشورات لـ X.\n\n"
        f"<transcript>\n{transcript}\n</transcript>"
    )

    try:
        response = _client.beta.messages.parse(
            model=settings.claude_model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            output_format=VideoSummary,
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

    result = response.parsed_output
    result.x_posts = [post[:X_POST_LIMIT] for post in result.x_posts[:num_posts]]
    return result
