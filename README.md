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

## جلب النص المفرّغ (Transcript)

يدعم أشكال الروابط: `watch?v=` و`youtu.be` و`shorts` و`embed` و`live` و`m.youtube.com`، أو المعرّف مباشرة.

ترتيب اختيار النص:
1. نص بإحدى اللغات المفضّلة (اليدوي أولًا ثم التلقائي).
2. إن لم يوجد: ترجمة آلية لأول نص متاح إلى اللغة المفضّلة الأولى.
3. وإلا: أول نص متاح بلغته الأصلية.

من سطر الأوامر:

```bash
python -m app.services.youtube "https://youtu.be/VIDEO_ID" --lang ar --lang en --timestamps
```

عبر الـ API:

```
GET /api/transcript?url=https://youtu.be/VIDEO_ID&lang=ar&lang=en
```

من الكود:

```python
from app.services.youtube import get_transcript

result = get_transcript("https://youtu.be/VIDEO_ID", ["ar", "en"])
print(result.language, result.is_generated, result.is_translated)
print(result.text)            # النص كاملًا
for seg in result.segments:   # المقاطع مع التوقيت
    print(seg.start, seg.text)
```

## خيارات التوليد

| الخيار | القيم | ملاحظات |
|---|---|---|
| `language` | `ar` / `en` | لغة الناتج بغض النظر عن لغة الفيديو (يُترجَم ويُلخَّص بها) |
| `tone` | `linkedin` / `x` / `marketing` | مهني/جاد (حتى 3000 حرف)، تفاعلي/جذاب (280)، حماسي/تسويقي (280) |
| `num_posts` | من 1 إلى 10 | إن أعاد النموذج عددًا أقل يُطلب منه استكمال الناقص (حتى محاولتين)، والزائد يُحذف |

تعليمات كل أسلوب موجودة في `app/tones.py`، ويمكن تعديلها أو إضافة أساليب جديدة من هناك،
وتظهر تلقائيًا في الواجهة.

## الـ API

`POST /api/summarize`

```json
{ "url": "https://youtu.be/VIDEO_ID", "language": "ar", "tone": "x", "num_posts": 3, "thread": false }
```

الاستجابة:

```json
{
  "video_id": "VIDEO_ID",
  "language": "ar",
  "tone": "x",
  "char_limit": 280,
  "requested_posts": 3,
  "title": "...",
  "summary": "...",
  "key_points": ["..."],
  "posts": ["..."],
  "quotes": [{ "text": "...", "translation": "..." }],
  "thread": false,
  "watermarked": true,
  "usage": { "plan": "free", "used": 1, "limit": 3, "remaining": 2 }
}
```

## الثريد والاقتباسات

- **ثريد لـ X** (`"thread": true`): سلسلة تغريدات متصلة، يُضاف ترقيمها (`1/6`) تلقائيًا في سطر مستقل.
  كل تغريدة تبقى ضمن 280 حرفًا شاملًا الترقيم والعلامة المائية؛ إن تجاوزت تغريدة حدّها يُطلب
  من النموذج اختصارها.
- **اقتباسات ذهبية**: أقوى 3 عبارات قيلت في الفيديو بلغته الأصلية مع ترجمتها. يتحقق الخادم من وجود
  كل اقتباس حرفيًا في نص الفيديو ويحذف أي اقتباس غير موجود فيه.

## الخطة المجانية والاشتراك (Stripe)

- كل زائر يُعرّف بكوكي عشوائي (`yt2x_vid`)، وتُحفظ حصته في SQLite (`DATABASE_PATH`).
- الخطة المجانية: `FREE_SUMMARY_LIMIT` محاولات (3 افتراضيًا)، تُحتسب المحاولة الناجحة فقط،
  ويُضاف لكل منشور سطر ترويجي فيه `SITE_NAME` و`PUBLIC_BASE_URL`. الخطة المدفوعة بلا حد وبلا علامة مائية.
- بعد انتهاء الحصة يرفض الخادم الطلبات (`402`) وتعرض الواجهة نافذة الترقية.

إعداد Stripe:

1. أنشئ منتجًا بسعر اشتراك شهري في لوحة Stripe، وضع `STRIPE_SECRET_KEY` و`STRIPE_PRICE_ID` في `.env`.
2. اضبط `PUBLIC_BASE_URL` على رابط موقعك (يُستخدم لروابط العودة من صفحة الدفع).
3. أضف Webhook يشير إلى `https://your-domain/api/stripe/webhook` مع الأحداث:
   `checkout.session.completed` و`checkout.session.async_payment_succeeded`
   و`customer.subscription.updated` و`customer.subscription.deleted`، وضع سرّه في `STRIPE_WEBHOOK_SECRET`.
   للتجربة محليًا: `stripe listen --forward-to localhost:8000/api/stripe/webhook`.

المسارات:

| المسار | الوظيفة |
|---|---|
| `POST /api/checkout/session` | ينشئ جلسة Stripe Checkout ويعيد رابطها |
| `GET /checkout?session_id=...` | صفحة العودة بعد الدفع: تتحقق من الجلسة وتفعّل الخطة المدفوعة |
| `POST /api/stripe/webhook` | يفعّل الخطة أو يلغيها حسب أحداث Stripe (المصدر الموثوق) |
| `GET /api/me` | خطة الزائر وعدد محاولاته المتبقية |

## الاختبارات

```bash
pytest
```

## ملاحظات

- يعتمد جلب النص على مكتبة `youtube-transcript-api`، لذا يجب أن يحتوي الفيديو على ترجمة (يدوية أو تلقائية).
  قد يحظر يوتيوب الطلبات القادمة من خوادم سحابية؛ عندها اضبط `YOUTUBE_PROXY_URL` في ملف `.env`.
- النموذج الافتراضي `claude-opus-5`، ويمكن تغييره عبر `CLAUDE_MODEL` في ملف `.env`.
