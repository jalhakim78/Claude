# ملخّص يوتيوب ← منشورات X

تطبيق ويب بسيط مبني بـ **FastAPI** يأخذ رابط فيديو يوتيوب، يجلب النص المفرّغ (Transcript)،
ثم يستخدم **Claude** لإنتاج ملخص وأهم النقاط ومنشورات جاهزة للنشر على منصة X.

## هيكل المشروع

```
.
├── app/
│   ├── main.py              # تطبيق FastAPI والمسارات (Routes)
│   ├── config.py            # الإعدادات من متغيرات البيئة
│   ├── auth.py              # تسجيل الدخول بالبريد (رمز لمرة واحدة)
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
├── Procfile                 # أمر تشغيل الخادم
├── render.yaml              # إعدادات الرفع على Render
├── requirements.txt
├── .env.example
└── README.md
```

## التشغيل

```bash
python -m venv .venv
source .venv/bin/activate        # على ويندوز: .venv\Scripts\activate
pip install -r requirements-dev.txt   # للتشغيل فقط بدون أدوات الاختبار: requirements.txt

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

## تسجيل الدخول بالبريد الإلكتروني

دخول دون كلمة مرور: يُدخل المستخدم بريده، فيصله رمز من 6 أرقام صالح 10 دقائق.

- **يُفعَّل تلقائيًا** عند ضبط `SMTP_HOST` و`EMAIL_FROM` (وبيانات الدخول `SMTP_USER`/`SMTP_PASSWORD`).
  للتجربة محليًا دون خادم بريد: `DEV_EMAIL_LOG=true` يطبع الرمز في سجل الخادم.
- **الاشتراك مرتبط بالبريد**: الدفع يتطلب تسجيل الدخول، فيستعيد المشترك باقته من أي جهاز بتسجيل الدخول.
- **الحصة لا تُصفَّر بتسجيل الدخول**: يُدمج استخدام المتصفح مع الحساب ويُحتسب الأكبر.
  مع `REQUIRE_LOGIN=true` لا يمكن التلخيص دون دخول، فلا يصفّر مسح الكوكيز الحصة.
- **الأمان**: الرموز والجلسات تُحفظ مجزّأة (SHA-256)؛ 5 محاولات خاطئة تُبطل الرمز؛ رمز جديد كل دقيقة
  وحتى 5 رموز في الساعة لكل بريد؛ الجلسة في كوكي `HttpOnly` منفصل، والخروج يحذفها من الخادم.

| المسار | الوظيفة |
|---|---|
| `POST /api/auth/request-code` | يرسل رمز الدخول إلى البريد |
| `POST /api/auth/verify` | يتحقق من الرمز ويفتح جلسة |
| `POST /api/auth/logout` | يُنهي الجلسة |

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

إعداد PayPal (أزرار PayPal الذكية، اشتراك شهري):

1. في [developer.paypal.com](https://developer.paypal.com) > **Apps & Credentials** أنشئ تطبيق REST
   (Live للدفعات الحقيقية) وخذ منه `PAYPAL_CLIENT_ID` و`PAYPAL_CLIENT_SECRET`. يجب أن يكون المعرّفان
   من التطبيق نفسه، وأن يطابق `PAYPAL_ENV` نوعه (`live` أو `sandbox`).
2. أنشئ خطة اشتراك شهرية (Subscriptions > Plans) بنفس عملة `PAYPAL_CURRENCY`، وضع معرّفها (`P-...`) في `PAYPAL_PLAN_ID`.
3. أضف Webhook في إعدادات التطبيق يشير إلى `https://your-domain/api/paypal/webhook` مع أحداث
   `BILLING.SUBSCRIPTION.ACTIVATED` و`CANCELLED` و`SUSPENDED` و`EXPIRED`، وضع معرّفه في `PAYPAL_WEBHOOK_ID`.

تظهر في نافذة الترقية البوابة المُعدّة فقط (أو كلتاهما).

المسارات:

| المسار | الوظيفة |
|---|---|
| `POST /api/checkout/session` | ينشئ جلسة Stripe Checkout ويعيد رابطها |
| `POST /api/paypal/subscription` | ينشئ اشتراك PayPal مربوطًا بالزائر ويعيد معرّفه لأزرار PayPal |
| `GET /checkout?session_id=...` | صفحة نجاح Stripe: تتحقق من الجلسة وتفعّل الخطة المدفوعة |
| `GET /checkout?provider=paypal&subscription_id=...` | صفحة نجاح PayPal: تتحقق من الاشتراك وتفعّل الخطة المدفوعة |
| `POST /api/stripe/webhook` | يفعّل الخطة أو يلغيها حسب أحداث Stripe |
| `POST /api/paypal/webhook` | يفعّل الخطة أو يلغيها حسب أحداث PayPal (بعد التحقق من التوقيع) |
| `GET /api/me` | خطة الزائر وعدد محاولاته المتبقية |

## الرفع على Render

المشروع جاهز للرفع عبر ملف `render.yaml`:

1. ارفع المستودع إلى GitHub.
2. في [لوحة Render](https://dashboard.render.com) اختر **New > Blueprint** واربط المستودع.
3. سيطلب Render القيم السرية: `ANTHROPIC_API_KEY` ومفاتيح Stripe و`PAYPAL_CLIENT_SECRET` و`PAYPAL_PLAN_ID`
   و`PAYPAL_WEBHOOK_ID` وإعدادات البريد (`SMTP_HOST` و`SMTP_USER` و`SMTP_PASSWORD` و`EMAIL_FROM`)، و`PUBLIC_BASE_URL` (اختياري؛ إن تُرك
   فارغًا يُستخدم رابط `onrender.com` تلقائيًا في العلامة المائية وروابط الدفع).
4. بعد أول رفع، أضف Webhook في Stripe يشير إلى `https://<رابطك>/api/stripe/webhook`، وآخر في PayPal
   يشير إلى `https://<رابطك>/api/paypal/webhook`.

أمر التشغيل موجود في `render.yaml` (`startCommand`) وفي `Procfile` بالصيغة نفسها. Render يعتمد على
`render.yaml` أو إعدادات اللوحة، أما `Procfile` فتقرؤه منصات مثل Heroku وRailway.

ملاحظات:
- الخدمة على خطة `starter` لأن قاعدة البيانات (الحصص والاشتراكات) تُحفظ على قرص دائم في `/var/data`،
  والأقراص الدائمة غير متاحة في الخطة المجانية. القرص يعني أيضًا نسخة واحدة من الخدمة، وتوقفًا لثوانٍ عند كل رفع.
- لم نستخدم Vercel لأنه يشغّل التطبيق كدوال مؤقتة لا تحتفظ بالملفات (فتضيع قاعدة البيانات)،
  ولها حد زمني قد لا يكفي لتلخيص فيديو طويل.
- إن حظر يوتيوب عناوين Render، اضبط `YOUTUBE_PROXY_URL`.

## الاختبارات

```bash
pip install -r requirements-dev.txt
pytest
```

## ملاحظات

- يعتمد جلب النص على مكتبة `youtube-transcript-api`، لذا يجب أن يحتوي الفيديو على ترجمة (يدوية أو تلقائية).
  قد يحظر يوتيوب الطلبات القادمة من خوادم سحابية؛ عندها اضبط `YOUTUBE_PROXY_URL` في ملف `.env`.
- النموذج الافتراضي `claude-opus-5`، ويمكن تغييره عبر `CLAUDE_MODEL` في ملف `.env`.
