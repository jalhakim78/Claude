import pytest

from app.schemas import Quote, VideoSummary
from app.services import summarizer
from app.services.summarizer import GenerationOptions, verify_quotes, watermark_text
from app.tones import TONES

WM = watermark_text("ar")


# ---------- التعليمات (Prompt) ----------

@pytest.mark.parametrize("tone_id", list(TONES))
@pytest.mark.parametrize("language,expected", [("ar", "العربية"), ("en", "English")])
def test_system_prompt_includes_options(tone_id, language, expected):
    opts = GenerationOptions(language=language, tone_id=tone_id, num_posts=7)
    prompt = summarizer.build_system_prompt(opts)
    assert expected in prompt
    assert TONES[tone_id].guide in prompt
    assert TONES[tone_id].platform in prompt
    assert "7 بالضبط" in prompt
    assert "مهما كانت لغة الفيديو" in prompt
    assert "حرفيًا" in prompt and "quotes" in prompt
    assert f"{TONES[tone_id].char_limit} حرفًا" in prompt


def test_thread_prompt():
    opts = GenerationOptions(tone_id="linkedin", num_posts=6, thread=True)
    prompt = summarizer.build_system_prompt(opts)
    assert "ثريد" in prompt and "6 تغريدات" in prompt
    assert "لا تكتب ترقيم" in prompt
    assert f"{280 - summarizer.THREAD_NUMBERING_RESERVE} حرفًا" in prompt


def test_prompt_budget_reserves_watermark():
    opts = GenerationOptions(num_posts=3, watermark=WM)
    assert opts.budget(0, 3) == 280 - len(WM) - 2
    assert f"{opts.budget(0, 3)} حرفًا" in summarizer.build_system_prompt(opts)


def test_thread_budget_reserves_watermark_on_last_tweet_only():
    opts = GenerationOptions(num_posts=4, thread=True, watermark=WM)
    assert opts.budget(0, 4) == 280 - summarizer.THREAD_NUMBERING_RESERVE
    assert opts.budget(3, 4) == 280 - summarizer.THREAD_NUMBERING_RESERVE - len(WM) - 2
    assert "التغريدة الأخيرة تحديدًا" in summarizer.build_system_prompt(opts)


# ---------- مسار التوليد ----------

def _summary(posts, quotes=()):
    return VideoSummary(title="t", summary="s", key_points=["k"], posts=posts, quotes=list(quotes))


def _run(monkeypatch, first, opts, transcript="نص", more=None, shorten=None):
    calls = []

    def fake_call(system, user, output_format):
        calls.append((output_format, system, user))
        if output_format is VideoSummary:
            return first
        if output_format is summarizer._MorePosts:
            return summarizer._MorePosts(posts=more.pop(0) if more else [])
        return summarizer._Shortened(items=shorten(user))

    monkeypatch.setattr(summarizer, "_call", fake_call)
    return summarizer.summarize(transcript, opts), calls


def test_extra_posts_are_trimmed(monkeypatch):
    result, _ = _run(monkeypatch, _summary(["a", "b", "c", "d"]), GenerationOptions(num_posts=2))
    assert result.posts == ["a", "b"]


def test_missing_posts_are_topped_up(monkeypatch):
    opts = GenerationOptions(language="en", num_posts=4, tone_id="linkedin")
    result, calls = _run(monkeypatch, _summary(["a", "a", " ", "b"]), opts, more=[["b", "c", "d"]])
    assert result.posts == ["a", "b", "c", "d"]
    _, system, user = calls[1]
    assert "2" in system and "English" in system and "LinkedIn" in system
    assert "- a" in user and "- b" in user


def test_top_up_gives_up_after_max_attempts(monkeypatch):
    result, calls = _run(monkeypatch, _summary(["a"]), GenerationOptions(num_posts=3), more=[["a"], ["a"]])
    assert result.posts == ["a"]
    assert len(calls) == 1 + summarizer.MAX_TOP_UP_ATTEMPTS


def test_watermark_added_to_every_post(monkeypatch):
    result, _ = _run(monkeypatch, _summary(["a", "b"]), GenerationOptions(num_posts=2, watermark=WM))
    assert result.posts == [f"a\n\n{WM}", f"b\n\n{WM}"]


def test_thread_numbering_and_watermark_on_last(monkeypatch):
    opts = GenerationOptions(num_posts=3, thread=True, watermark=WM)
    result, calls = _run(monkeypatch, _summary(["one", "two", "three"]), opts)
    assert result.posts == ["one\n\n1/3", "two\n\n2/3", f"three\n\n{WM}\n\n3/3"]
    assert len(calls) == 1  # لا طلبات استكمال للثريد


def test_thread_keeps_conclusion_when_too_long(monkeypatch):
    opts = GenerationOptions(num_posts=3, thread=True)
    result, _ = _run(monkeypatch, _summary(["hook", "a", "b", "c", "end"]), opts)
    assert result.posts == ["hook\n\n1/3", "a\n\n2/3", "end\n\n3/3"]


def test_thread_tweets_never_exceed_280_after_shortening(monkeypatch):
    opts = GenerationOptions(num_posts=2, thread=True, watermark=WM)
    def shorten(user):
        assert f'max_chars="{opts.budget(0, 2)}"' in user
        assert f'max_chars="{opts.budget(1, 2)}"' in user  # الأخيرة أقصر بسبب العلامة المائية
        return ["قصير", "قصير أيضًا"]

    result, calls = _run(monkeypatch, _summary(["ك" * 300, "م" * 300]), opts, shorten=shorten)
    assert all(len(p) <= 280 for p in result.posts)
    assert result.posts[-1].endswith(f"{WM}\n\n2/2")


def test_shortening_ignored_if_not_shorter(monkeypatch):
    opts = GenerationOptions(num_posts=1)
    long = "x" * 300
    result, _ = _run(monkeypatch, _summary([long]), opts, shorten=lambda u: ["y" * 400])
    assert result.posts == [long]


# ---------- الاقتباسات ----------

TRANSCRIPT = (
    "مرحبا بكم. النجاح لا يأتي صدفة بل هو نتيجة عمل متواصل. "
    "and remember the best time to start was yesterday the next best is now"
)


def test_verify_quotes_keeps_verbatim_only():
    quotes = [
        Quote(text="«النجاحُ لا يأتي صدفة، بل هو نتيجة عمل متواصل»", translation="Success is no accident."),
        Quote(text="The best time to start was yesterday. The next best is now!", translation="أفضل وقت..."),
        Quote(text="النجاح يأتي بالحظ", translation=""),  # غير موجود في النص
        Quote(text="مرحبا", translation=""),  # قصير جدًا
    ]
    result = verify_quotes(quotes, TRANSCRIPT)
    assert [q.text for q in result] == [
        "النجاحُ لا يأتي صدفة، بل هو نتيجة عمل متواصل",
        "The best time to start was yesterday. The next best is now!",
    ]


def test_verify_quotes_limits_to_three_and_drops_same_language_translation():
    t = "one two three four five six seven eight nine ten eleven twelve"
    quotes = [Quote(text=w, translation=w) for w in
              ["one two three", "four five six", "seven eight nine", "ten eleven twelve"]]
    result = verify_quotes(quotes, t)
    assert len(result) == 3 and all(q.translation == "" for q in result)


def test_summarize_filters_unverified_quotes(monkeypatch):
    first = _summary(["a"], [Quote(text="نتيجة عمل متواصل", translation=""),
                             Quote(text="كلام مخترع تمامًا", translation="")])
    result, _ = _run(monkeypatch, first, GenerationOptions(num_posts=1), transcript=TRANSCRIPT)
    assert [q.text for q in result.quotes] == ["نتيجة عمل متواصل"]
