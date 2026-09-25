# ملخّص يوتيوب ← منشورات X

تطبيق ويب بسيط مبني بـ **FastAPI** يأخذ رابط فيديو يوتيوب، يجلب النص المفرّغ (Transcript)،
ثم يستخدم **Claude** لإنتاج ملخص وأهم النقاط ومنشورات جاهزة للنشر على منصة X.

## هيكل المشروع

```
.
├── app/
│   ├── main.py              # تطبيق FastAPI والمسارات (Routes)
│   ├── config.py            # الإعدادات من متغيرات البيئة
│   ├── schemas.py           # نماذج Pydantic للطلبات والاستجابات
│   ├── services/
│   │   ├── youtube.py       # استخراج معرّف الفيديو وجلب النص
│   │   └── summarizer.py    # التلخيص وتوليد منشورات X عبر Claude
│   ├── templates/
│   │   └── index.html       # واجهة المستخدم
│   └── static/
│       ├── style.css
│       └── app.js
├── tests/                   # اختبارات pytest
├── requirements.txt
├── .env.example
└── README.md
```

## التشغيل

```bash
python -m venv .venv
source .venv/bin/activate        # على ويندوز: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # ثم ضع مفتاح ANTHROPIC_API_KEY
uvicorn app.main:app --reload
```

افتح المتصفح على: http://127.0.0.1:8000
وتوثيق الـ API التفاعلي على: http://127.0.0.1:8000/docs

## الـ API

`POST /api/summarize`

```json
{ "url": "https://youtu.be/VIDEO_ID", "language": "ar", "num_posts": 3 }
```

الاستجابة:

```json
{
  "video_id": "VIDEO_ID",
  "title": "...",
  "summary": "...",
  "key_points": ["..."],
  "x_posts": ["..."]
}
```

## الاختبارات

```bash
pytest
```

## ملاحظات

- يعتمد جلب النص على مكتبة `youtube-transcript-api`، لذا يجب أن يحتوي الفيديو على ترجمة (يدوية أو تلقائية).
  قد يحظر يوتيوب الطلبات القادمة من خوادم سحابية؛ عندها تحتاج لإعداد بروكسي (راجع توثيق المكتبة).
- النموذج الافتراضي `claude-opus-5`، ويمكن تغييره عبر `CLAUDE_MODEL` في ملف `.env`.
