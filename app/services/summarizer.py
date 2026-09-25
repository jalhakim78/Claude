"""تلخيص النص وتوليد المنشورات باستخدام Claude."""

import re
import unicodedata
from dataclasses import dataclass
from typing import TypeVar

import anthropic
from pydantic import BaseModel

from app.config import settings
from app.schemas import Quote, VideoSummary
from app.tones import LANGUAGE_NAMES, THREAD_CHAR_LIMIT, TONES, Tone

BASE_PROMPT = """أنت محرر محتوى محترف متخصص في صناعة المحتوى لمنصات التواصل الاجتماعي.
ستتلقى نصًا مفرّغًا من فيديو يوتيوب، وقد يكون بأي لغة.

المطلوب:
- title: عنوان قصير وواضح يعبّر عن محتوى الفيديو.
- summary: ملخص من فقرة أو فقرتين يغطي الفكرة الأساسية.
- key_points: أهم النقاط (من 3 إلى 7 نقاط موجزة).
{posts_task}
- quotes: أقوى 3 اقتباسات قيلت في الفيديو (انظر قسم quotes أدناه).

قواعد عامة:
- التزم بالمعلومات الواردة في النص فقط ولا تخترع حقائق أو أرقامًا.
- لا تضع روابط في المنشورات.

<output_language>
اكتب الحقول title وsummary وkey_points وposts وquotes[].translation باللغة: {language}.
التزم بهذه اللغة حصرًا مهما كانت لغة الفيديو الأصلية؛ ترجم المعنى بدقة وبصياغة طبيعية
كأنك تكتب بها أصلًا، ولا تُبقِ جملًا بلغة الفيديو. يُستثنى فقط أسماء الأعلام والعلامات
التجارية والمصطلحات التقنية الشائعة، وكذلك quotes[].text (انظر قسم quotes).
</output_language>

<tone>
{tone_guide}
</tone>

<length>
{length_rules}
</length>

<post_count>
{count_rule}
</post_count>

<quotes>
- اختر أقوى 3 عبارات قالها المتحدث في الفيديو: عبارات مؤثرة أو ملهمة أو تلخّص فكرة مهمة،
  وتصلح للاقتباس والمشاركة وحدها. جملة أو جملتان لكل اقتباس.
- text: انقل العبارة حرفيًا كما وردت في النص المفرّغ وبلغته الأصلية، كلمةً بكلمة،
  دون أي تعديل أو تصحيح أو اختصار أو ترجمة. يمكنك فقط إضافة علامات الترقيم.
- translation: ترجمة الاقتباس إلى {language} إن كانت لغة الفيديو مختلفة عنها، وإلا اتركه فارغًا "".
- إن لم يحتوِ النص على 3 عبارات تستحق الاقتباس، أعد عددًا أقل ولا تخترع.
</quotes>"""

SINGLE_POSTS_TASK = """- posts: {n} منشورات مستقلة جاهزة للنشر على {platform}. كل منشور يُفهم وحده دون مشاهدة
  الفيديو، ويتناول نقطة أو زاوية مختلفة عن غيره؛ لا تكرّر الفكرة نفسها."""

THREAD_TASK = """- posts: ثريد على X (سلسلة تغريدات متصلة) مكوّن من {n} تغريدات بالترتيب، يروي محتوى الفيديو:
  - التغريدة الأولى خطّاف قوي يشدّ القارئ ويَعِده بقيمة، مع رمز 🧵 للإشارة إلى أنه ثريد.
  - كل تغريدة تحمل فكرة واحدة وتمهّد لما بعدها، والتسلسل منطقي من البداية للنهاية.
  - التغريدة الأخيرة خلاصة ودعوة للتفاعل (متابعة، إعادة نشر، أو سؤال).
  - لا تكتب ترقيم التغريدات (مثل 1/6) بنفسك؛ سيُضاف تلقائيًا.
  - الوسوم في التغريدة الأخيرة فقط، ووسم أو وسمان كحد أقصى."""

MORE_POSTS_PROMPT = """أنت محرر محتوى محترف. بناءً على ملخص فيديو يوتيوب أدناه، اكتب {missing}
منشورات جديدة إضافية بالضبط، للنشر على {platform}.

- اللغة: {language} حصرًا.
- يجب أن تختلف في فكرتها وصياغتها عن المنشورات الموجودة.
- التزم بالمعلومات الواردة في الملخص فقط.
- الحد الأقصى المطلق لطول كل منشور: {budget} حرفًا شاملًا المسافات والرموز والوسوم.

<tone>
{tone_guide}
</tone>"""

SHORTEN_PROMPT = """أنت محرر محتوى. ستتلقى نصوصًا تجاوزت الحد المسموح من الأحرف.
أعد صياغة كل نص ليصبح أقصر من حدّه المذكور، مع الحفاظ على المعنى واللغة والأسلوب والوسوم
المهمة. أعد النصوص في items بالترتيب نفسه وبالعدد نفسه، دون أي ترقيم أو إضافات."""

# عدد المحاولات الإضافية لاستكمال المنشورات إن أعاد النموذج عددًا أقل من المطلوب
MAX_TOP_UP_ATTEMPTS = 2
# ترقيم الثريد يُضاف في سطر مستقل: "\n\n10/10"
THREAD_NUMBERING_RESERVE = 7
MAX_QUOTES = 3

_client = anthropic.Anthropic()

T = TypeVar("T", bound=BaseModel)


class SummarizerError(Exception):
    """خطأ أثناء التلخيص."""


class _MorePosts(BaseModel):
    posts: list[str]


class _Shortened(BaseModel):
    items: list[str]


@dataclass
class GenerationOptions:
    language: str = "ar"
    tone_id: str = "x"
    num_posts: int = 3
    thread: bool = False
    # نص العلامة المائية للخطة المجانية؛ None للخطة المدفوعة
    watermark: str | None = None

    @property
    def tone(self) -> Tone:
        return TONES[self.tone_id]

    @property
    def platform(self) -> str:
        return "X" if self.thread else self.tone.platform

    @property
    def char_limit(self) -> int:
        return THREAD_CHAR_LIMIT if self.thread else self.tone.char_limit

    @property
    def watermark_suffix(self) -> str:
        return f"\n\n{self.watermark}" if self.watermark else ""

    def budget(self, index: int, total: int) -> int:
        """أقصى طول مسموح للنص الذي يكتبه النموذج، بعد حجز مساحة للترقيم والعلامة المائية."""
        budget = self.char_limit
        if self.thread:
            budget -= THREAD_NUMBERING_RESERVE
            if index == total - 1:  # العلامة المائية تُضاف لآخر تغريدة فقط
                budget -= len(self.watermark_suffix)
        else:
            budget -= len(self.watermark_suffix)
        return budget


def watermark_text(language: str) -> str:
    if language == "en":
        return f"⚡ Summarized with {settings.site_name}: {settings.public_base_url}"
    return f"⚡ تم التلخيص عبر {settings.site_name}: {settings.public_base_url}"


def build_system_prompt(opts: GenerationOptions) -> str:
    n = opts.num_posts
    if opts.thread:
        posts_task = THREAD_TASK.format(n=n)
        count_rule = f"عدد تغريدات الثريد المطلوب هو {n} بالضبط."
        body, last = opts.budget(0, n), opts.budget(n - 1, n)
        length_rules = f"الحد الأقصى المطلق لكل تغريدة: {body} حرفًا شاملًا المسافات والرموز والوسوم."
        if last != body:
            length_rules += f"\nالتغريدة الأخيرة تحديدًا: {last} حرفًا كحد أقصى."
    else:
        posts_task = SINGLE_POSTS_TASK.format(n=n, platform=opts.platform)
        count_rule = f"عدد المنشورات المطلوب هو {n} بالضبط: لا أكثر ولا أقل."
        length_rules = (
            f"الحد الأقصى المطلق لكل منشور: {opts.budget(0, n)} حرفًا شاملًا المسافات والرموز والوسوم."
            f"\n{opts.tone.length_hint}"
        )
    return BASE_PROMPT.format(
        posts_task=posts_task,
        language=LANGUAGE_NAMES[opts.language],
        tone_guide=opts.tone.guide,
        length_rules=length_rules,
        count_rule=count_rule,
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


def _top_up(posts: list[str], result: VideoSummary, opts: GenerationOptions) -> list[str]:
    """يطلب منشورات إضافية حتى يصل العدد إلى المطلوب (للمنشورات المستقلة فقط)."""
    for _ in range(MAX_TOP_UP_ATTEMPTS):
        missing = opts.num_posts - len(posts)
        if missing <= 0:
            break
        system = MORE_POSTS_PROMPT.format(
            missing=missing,
            platform=opts.platform,
            language=LANGUAGE_NAMES[opts.language],
            tone_guide=opts.tone.guide,
            budget=opts.budget(0, opts.num_posts),
        )
        existing = "\n".join(f"- {p}" for p in posts)
        user = (
            f"<summary>\n{result.summary}\n</summary>\n\n"
            f"<key_points>\n" + "\n".join(f"- {k}" for k in result.key_points) + "\n</key_points>\n\n"
            f"<existing_posts>\n{existing}\n</existing_posts>"
        )
        posts = _clean(posts + _call(system, user, _MorePosts).posts)
    return posts[: opts.num_posts]


def _fit_thread(posts: list[str], n: int) -> list[str]:
    """إن أعاد النموذج تغريدات أكثر، نحذف من المنتصف ونُبقي الخاتمة."""
    return posts if len(posts) <= n else posts[: n - 1] + [posts[-1]]


def _enforce_lengths(posts: list[str], opts: GenerationOptions) -> list[str]:
    """يطلب من النموذج اختصار أي منشور تجاوز حدّه (محاولة واحدة)."""
    total = len(posts)
    over = [i for i, p in enumerate(posts) if len(p) > opts.budget(i, total)]
    if not over:
        return posts

    user = "\n\n".join(
        f'<item index="{k}" max_chars="{opts.budget(i, total)}">\n{posts[i]}\n</item>'
        for k, i in enumerate(over)
    )
    shortened = _call(SHORTEN_PROMPT, user, _Shortened).items
    if len(shortened) != len(over):
        return posts

    posts = list(posts)
    for i, text in zip(over, shortened):
        text = text.strip()
        if text and len(text) < len(posts[i]):
            posts[i] = text
    return posts


def _decorate(posts: list[str], opts: GenerationOptions) -> list[str]:
    """يضيف ترقيم الثريد والعلامة المائية."""
    total = len(posts)
    if not opts.thread:
        return [p + opts.watermark_suffix for p in posts]
    return [
        p + (opts.watermark_suffix if i == total - 1 else "") + f"\n\n{i + 1}/{total}"
        for i, p in enumerate(posts)
    ]


_TASHKEEL = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_ARABIC_VARIANTS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه"})


def _normalize(text: str) -> str:
    """تطبيع للمقارنة: يتجاهل الترقيم والتشكيل وحالة الأحرف واختلاف رسم الهمزات."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = _TASHKEEL.sub("", text).translate(_ARABIC_VARIANTS)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def verify_quotes(quotes: list[Quote], transcript: str) -> list[Quote]:
    """يُبقي فقط الاقتباسات الموجودة حرفيًا في نص الفيديو."""
    source = _normalize(transcript)
    verified = []
    for quote in quotes:
        text = quote.text.strip().strip("\"'«»“”")
        normalized = _normalize(text)
        if len(normalized) >= 8 and normalized in source:
            translation = quote.translation.strip()
            if _normalize(translation) == normalized:
                translation = ""
            verified.append(Quote(text=text, translation=translation))
    return verified[:MAX_QUOTES]


def summarize(transcript: str, opts: GenerationOptions) -> VideoSummary:
    user = f"<transcript>\n{transcript}\n</transcript>"
    result = _call(build_system_prompt(opts), user, VideoSummary)

    posts = _clean(result.posts)
    if opts.thread:
        posts = _fit_thread(posts, opts.num_posts)
    else:
        posts = _top_up(posts, result, opts)
    posts = _enforce_lengths(posts, opts)

    result.posts = _decorate(posts, opts)
    result.quotes = verify_quotes(result.quotes, transcript)
    return result
