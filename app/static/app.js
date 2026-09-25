const YT_RE = /(youtube\.com|youtu\.be|youtube-nocookie\.com)\/|^[\w-]{11}$/;

const $ = (id) => document.getElementById(id);
const form = $("summarize-form");
const urlInput = $("url");
const submitBtn = $("submit-btn");
const statusEl = $("status");
const loaderEl = $("loader");
const loaderTitle = $("loader-title");
const toastEl = $("toast");
const resultEl = $("result");
const numPostsEl = $("num-posts");
const threadEl = $("thread");
const postTemplate = $("post-template");
const quoteTemplate = $("quote-template");
const languageEl = $("language");
const modal = $("upgrade-modal");

const LANGUAGE_LABELS = { ar: "العربية", en: "English" };
const TONE_LABELS = Object.fromEntries(
  [...document.querySelectorAll('input[name="tone"]')].map((input) => [
    input.value,
    input.nextElementSibling.querySelector("strong").textContent,
  ])
);
const PREFS_KEY = "yt2x-prefs";

// حالة الحساب تأتي من الخادم مع الصفحة، وتتحدّث بعد كل تلخيص
let account = JSON.parse($("account-data").textContent);

// ---------- Helpers ----------

function showStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
  statusEl.hidden = false;
}

function setLoading(loading) {
  submitBtn.disabled = loading;
  submitBtn.classList.toggle("loading", loading);
  loaderEl.hidden = !loading;
}

let toastTimer;
function showToast(message) {
  toastEl.textContent = message;
  toastEl.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toastEl.hidden = true), 1800);
}

// navigator.clipboard يعمل فقط على https أو localhost؛
// عند فتح الصفحة من عنوان شبكة محلية نستخدم الطريقة القديمة كبديل.
function legacyCopy(text) {
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.cssText = "position:fixed;top:0;left:0;opacity:0";
  document.body.append(area);
  area.select();
  const ok = document.execCommand("copy");
  area.remove();
  if (!ok) throw new Error("copy failed");
}

async function copyText(text, button, toastMessage = "تم نسخ المنشور إلى الحافظة") {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      legacyCopy(text);
    }
  } catch {
    showStatus("تعذّر النسخ، انسخ النص يدويًا", true);
    return;
  }

  const label = button.querySelector("span") || button;
  const original = button.dataset.label ?? label.textContent;
  button.dataset.label = original;
  label.textContent = "تم النسخ ✓";
  button.classList.add("done");
  showToast(toastMessage);
  clearTimeout(button._resetTimer);
  button._resetTimer = setTimeout(() => {
    label.textContent = original;
    button.classList.remove("done");
  }, 1500);
}

// X تحسب الأحرف بنقاط Unicode، لذا نستخدم [...text] بدل text.length
const charCount = (text) => [...text].length;

function autoGrow(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = `${textarea.scrollHeight}px`;
}

// ---------- الحصة والخطة ----------

function isLocked() {
  return account.usage.remaining === 0;
}

function renderAccount() {
  const { plan, remaining, limit } = account.usage;
  const pill = $("plan-pill");
  const note = $("quota-note");

  pill.classList.toggle("pro", plan === "pro");
  pill.classList.toggle("empty", remaining === 0);
  if (plan === "pro") {
    pill.textContent = "⭐ الباقة الاحترافية";
    note.hidden = true;
  } else {
    pill.textContent = remaining === 0 ? "🔒 ترقية الحساب" : `مجاني · ${remaining}/${limit}`;
    note.hidden = false;
    note.classList.toggle("empty", remaining === 0);
    note.textContent =
      remaining === 0
        ? "انتهت محاولاتك المجانية — اشترك لفتح التلخيص غير المحدود"
        : `متبقٍّ لك ${remaining} من ${limit} محاولات تلخيص مجانية`;
  }

  const user = account.user;
  $("login-btn").hidden = !account.login_enabled || Boolean(user);
  $("account-menu").hidden = !user;
  $("account-email").textContent = user?.email ?? "";
  $("account-email").title = user?.email ?? "";

  const label = submitBtn.querySelector(".btn-label");
  submitBtn.classList.toggle("locked", isLocked());
  label.textContent = isLocked() ? "🔒 اشترك لمتابعة التلخيص" : "لخّص الفيديو";
}

function showUpgradeError(message) {
  const errorEl = $("upgrade-error");
  errorEl.textContent = message;
  errorEl.hidden = false;
}

function openUpgradeModal() {
  $("modal-limit").textContent = account.usage.limit ?? "";
  $("modal-price").textContent = account.price_label;
  $("upgrade-error").hidden = true;

  // الدفع يتطلب تسجيل الدخول ليرتبط الاشتراك بالبريد (عند تفعيل الدخول)
  const needsLogin = account.login_enabled && !account.user;
  $("login-first").hidden = !needsLogin;
  $("pay-options").hidden = needsLogin;

  const stripeOn = account.stripe_enabled && !needsLogin;
  const paypalOn = account.paypal?.enabled && !needsLogin;
  $("stripe-option").hidden = !stripeOn;
  $("paypal-option").hidden = !paypalOn;
  $("pay-divider").hidden = !(stripeOn && paypalOn);
  $("no-payment").hidden = needsLogin || stripeOn || paypalOn;

  if (!modal.open) modal.showModal();
  if (paypalOn) renderPayPalButtons();
}

// ---------- PayPal Smart Buttons ----------

let paypalRendered = false;

function loadPayPalSdk() {
  if (window.paypal) return Promise.resolve(window.paypal);
  return new Promise((resolve, reject) => {
    const { client_id, currency } = account.paypal;
    const script = document.createElement("script");
    script.src =
      "https://www.paypal.com/sdk/js?" +
      new URLSearchParams({
        "client-id": client_id,
        vault: "true",
        intent: "subscription",
        currency,
        components: "buttons",
      });
    script.dataset.namespace = "paypal";
    script.onload = () => (window.paypal ? resolve(window.paypal) : reject(new Error("sdk")));
    script.onerror = () => reject(new Error("sdk"));
    document.head.append(script);
  });
}

async function renderPayPalButtons() {
  if (paypalRendered) return;
  paypalRendered = true;
  const loadingEl = $("paypal-loading");

  try {
    const paypal = await loadPayPalSdk();
    await paypal
      .Buttons({
        style: { layout: "vertical", color: "gold", shape: "pill", label: "subscribe" },

        // الخادم ينشئ الاشتراك ويربطه بهذا الزائر، ثم يعيد معرّفه للأزرار
        createSubscription: async () => {
          $("upgrade-error").hidden = true;
          const response = await fetch("/api/paypal/subscription", { method: "POST" });
          const data = await response.json().catch(() => ({}));
          if (!response.ok || !data.id) {
            throw new Error(data.detail || "تعذّر بدء الاشتراك عبر PayPal");
          }
          return data.id;
        },

        // بعد الموافقة ننتقل لصفحة النجاح التي تتحقق من الاشتراك وتفعّل الخطة
        onApprove: (data) => {
          const params = new URLSearchParams({
            provider: "paypal",
            subscription_id: data.subscriptionID,
          });
          window.location.href = `/checkout?${params}`;
        },

        onCancel: () => showToast("تم إلغاء عملية الدفع"),
        onError: (err) => {
          console.error(err);
          showUpgradeError(err?.message || "حدث خطأ أثناء الدفع عبر PayPal، حاول مرة أخرى");
        },
      })
      .render("#paypal-buttons");
    loadingEl.hidden = true;
  } catch (err) {
    console.error(err);
    paypalRendered = false; // نسمح بإعادة المحاولة عند فتح النافذة مجددًا
    loadingEl.hidden = true;
    showUpgradeError("تعذّر تحميل أزرار PayPal. تحقق من اتصالك أو من إعداد PAYPAL_CLIENT_ID.");
  }
}

$("plan-pill").addEventListener("click", () => {
  if (account.usage.plan !== "pro") openUpgradeModal();
});

// إغلاق النافذة عند النقر خارجها
modal.addEventListener("click", (e) => {
  if (e.target === modal) modal.close();
});

$("upgrade-btn").addEventListener("click", async () => {
  const btn = $("upgrade-btn");
  $("upgrade-error").hidden = true;
  btn.disabled = true;
  btn.classList.add("loading");
  try {
    const response = await fetch("/api/checkout/session", { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (response.status === 401) {
      resetBtn();
      openLoginModal(openUpgradeModal);
      return;
    }
    if (!response.ok || !data.url) throw new Error(data.detail || "تعذّر بدء عملية الدفع");
    window.location.href = data.url; // الانتقال إلى صفحة الدفع في Stripe
  } catch (err) {
    showUpgradeError(err instanceof TypeError ? "تعذّر الاتصال بالخادم" : err.message);
    resetBtn();
  }

  function resetBtn() {
    btn.disabled = false;
    btn.classList.remove("loading");
  }
});

// ---------- تسجيل الدخول بالبريد ----------

const loginModal = $("login-modal");
const emailStep = $("login-email-step");
const codeStep = $("login-code-step");
let afterLogin = null;
let resendTimer = null;

async function postJSON(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : null;
    throw Object.assign(new Error(detail || `حدث خطأ غير متوقع (${response.status})`), {
      status: response.status,
    });
  }
  return data;
}

async function refreshAccount() {
  const response = await fetch("/api/me");
  if (response.ok) account = await response.json();
  renderAccount();
}

function showLoginError(message) {
  const el = $("login-error");
  el.textContent = message;
  el.hidden = !message;
}

function setBusy(form, busy) {
  const btn = form.querySelector(".primary-btn");
  btn.disabled = busy;
  btn.classList.toggle("loading", busy);
}

function showStep(step) {
  emailStep.hidden = step !== "email";
  codeStep.hidden = step !== "code";
  showLoginError("");
  (step === "email" ? $("login-email") : $("login-code")).focus();
}

function openLoginModal(then = null) {
  afterLogin = then;
  if (modal.open) modal.close();
  showStep("email");
  if (!loginModal.open) loginModal.showModal();
  $("login-email").focus();
}

function startResendCooldown(seconds = 60) {
  const btn = $("login-resend");
  clearInterval(resendTimer);
  let left = seconds;
  btn.disabled = true;
  const tick = () => {
    btn.textContent = left > 0 ? `إعادة الإرسال بعد ${left} ث` : "إعادة إرسال الرمز";
    btn.disabled = left > 0;
    if (left-- <= 0) clearInterval(resendTimer);
  };
  tick();
  resendTimer = setInterval(tick, 1000);
}

async function requestCode() {
  const email = $("login-email").value.trim();
  await postJSON("/api/auth/request-code", { email });
  $("login-sent-to").textContent = email;
  $("login-code").value = "";
  showStep("code");
  startResendCooldown();
}

emailStep.addEventListener("submit", async (e) => {
  e.preventDefault();
  setBusy(emailStep, true);
  try {
    await requestCode();
  } catch (err) {
    showLoginError(err instanceof TypeError ? "تعذّر الاتصال بالخادم" : err.message);
  } finally {
    setBusy(emailStep, false);
  }
});

codeStep.addEventListener("submit", async (e) => {
  e.preventDefault();
  setBusy(codeStep, true);
  try {
    await postJSON("/api/auth/verify", {
      email: $("login-email").value.trim(),
      code: $("login-code").value,
    });
    await refreshAccount();
    loginModal.close();
    showToast("تم تسجيل الدخول");
    const next = afterLogin;
    afterLogin = null;
    if (next) next();
  } catch (err) {
    showLoginError(err instanceof TypeError ? "تعذّر الاتصال بالخادم" : err.message);
    $("login-code").select();
  } finally {
    setBusy(codeStep, false);
  }
});

// إرسال تلقائي عند إدخال 6 أرقام (ومنها اللصق)
$("login-code").addEventListener("input", (e) => {
  e.target.value = e.target.value.replace(/\D/g, "").slice(0, 6);
  if (e.target.value.length === 6) codeStep.requestSubmit();
});

$("login-resend").addEventListener("click", async () => {
  try {
    await requestCode();
    showToast("أرسلنا رمزًا جديدًا");
  } catch (err) {
    showLoginError(err.message);
  }
});
$("login-change").addEventListener("click", () => showStep("email"));
loginModal.addEventListener("click", (e) => {
  if (e.target === loginModal) loginModal.close();
});

$("login-btn").addEventListener("click", () => openLoginModal());
$("login-first-btn").addEventListener("click", () => openLoginModal(openUpgradeModal));

$("logout-btn").addEventListener("click", async () => {
  try {
    await postJSON("/api/auth/logout");
  } catch {}
  await refreshAccount();
  showToast("تم تسجيل الخروج");
});

// ---------- عدد المنشورات ----------

const maxPosts = Number(numPostsEl.max) || 10;
const decBtn = document.querySelector('[data-step="-1"]');
const incBtn = document.querySelector('[data-step="1"]');

// الثريد يحتاج تغريدتين على الأقل
const minPosts = () => (threadEl.checked ? 2 : 1);

// يعيد العدد إن كان صحيحًا ضمن المدى، وإلا null
function readNumPosts() {
  const raw = numPostsEl.value.trim();
  if (!/^\d+$/.test(raw)) return null;
  const n = Number(raw);
  return n >= minPosts() && n <= maxPosts ? n : null;
}

function syncNumPosts() {
  const n = readNumPosts();
  numPostsEl.parentElement.classList.toggle("invalid", n === null);
  decBtn.disabled = n !== null && n <= minPosts();
  incBtn.disabled = n !== null && n >= maxPosts;
}

function setNumPosts(value) {
  numPostsEl.value = Math.min(Math.max(Math.round(value) || minPosts(), minPosts()), maxPosts);
  syncNumPosts();
}

[decBtn, incBtn].forEach((btn) =>
  btn.addEventListener("click", () =>
    setNumPosts((readNumPosts() ?? minPosts()) + Number(btn.dataset.step))
  )
);
numPostsEl.addEventListener("input", syncNumPosts);
// عند مغادرة الحقل نصحّح القيمة تلقائيًا إلى أقرب رقم مسموح
numPostsEl.addEventListener("blur", () => {
  if (readNumPosts() === null) setNumPosts(Number(numPostsEl.value));
});

// ---------- وضع الثريد ----------

function syncThreadMode() {
  const on = threadEl.checked;
  numPostsEl.min = minPosts();
  $("num-posts-label").textContent = on ? "عدد تغريدات الثريد" : "عدد المنشورات";
  $("num-posts-hint").textContent = on
    ? `من 2 إلى ${maxPosts} تغريدات`
    : `من 1 إلى ${maxPosts} منشورات`;
  if (on && Number(numPostsEl.value) < 2) setNumPosts(Math.min(5, maxPosts));
  syncNumPosts();
}

threadEl.addEventListener("change", syncThreadMode);

// ---------- حفظ التفضيلات (اختياري؛ الصفحة تعمل بدونه) ----------

function savePrefs() {
  try {
    localStorage.setItem(
      PREFS_KEY,
      JSON.stringify({
        language: languageEl.value,
        tone: form.elements.tone.value,
        numPosts: readNumPosts(),
        thread: threadEl.checked,
      })
    );
  } catch {}
}

function loadPrefs() {
  let prefs = {};
  try {
    prefs = JSON.parse(localStorage.getItem(PREFS_KEY)) || {};
  } catch {}
  if (prefs.language in LANGUAGE_LABELS) languageEl.value = prefs.language;
  if (prefs.tone in TONE_LABELS) form.elements.tone.value = prefs.tone;
  threadEl.checked = prefs.thread === true;
  setNumPosts(prefs.numPosts ?? Math.min(3, maxPosts));
  syncThreadMode();
}

loadPrefs();
form.addEventListener("change", savePrefs);

// ---------- Paste ----------

$("paste-btn").addEventListener("click", async () => {
  try {
    urlInput.value = (await navigator.clipboard.readText()).trim();
    urlInput.dispatchEvent(new Event("input"));
  } catch {
    urlInput.focus(); // المتصفح رفض الوصول للحافظة؛ يلصق المستخدم يدويًا
  }
});

urlInput.addEventListener("input", () =>
  urlInput.parentElement.classList.remove("invalid")
);

// ---------- Rendering ----------

function renderPost(text, limit, canShareToX) {
  const node = postTemplate.content.firstElementChild.cloneNode(true);
  const textEl = node.querySelector(".post-text");
  const countEl = node.querySelector(".count");
  const shareEl = node.querySelector(".share");

  const update = () => {
    const value = textEl.value.trim();
    const n = charCount(value);
    countEl.textContent = `${n} / ${limit}`;
    countEl.classList.toggle("warn", n > limit * 0.93 && n <= limit);
    countEl.classList.toggle("over", n > limit);
    if (canShareToX) {
      shareEl.href = `https://x.com/intent/post?text=${encodeURIComponent(value)}`;
    }
    autoGrow(textEl);
  };

  // منشورات LinkedIn أطول من حد X، لذا نكتفي بزر النسخ لها
  shareEl.hidden = !canShareToX;
  textEl.value = text;
  textEl.addEventListener("input", update);
  node.querySelector(".copy").addEventListener("click", (e) =>
    copyText(textEl.value.trim(), e.currentTarget)
  );
  // الارتفاع الصحيح يُحسب بعد إضافة العنصر للصفحة
  requestAnimationFrame(update);
  return node;
}

function renderQuote({ text, translation }) {
  const node = quoteTemplate.content.firstElementChild.cloneNode(true);
  node.querySelector(".quote-text").textContent = text;
  node.querySelector(".quote-translation").textContent = translation;
  node.querySelector(".copy-quote").addEventListener("click", (e) =>
    copyText(`«${text}»`, e.currentTarget, "تم نسخ الاقتباس")
  );
  return node;
}

const thumb = $("thumb");
thumb.addEventListener("error", () => {
  thumb.hidden = true;
  thumb.parentElement.classList.add("no-thumb");
});

function renderResult(data) {
  thumb.hidden = false;
  thumb.parentElement.classList.remove("no-thumb");
  thumb.src = `https://i.ytimg.com/vi/${data.video_id}/hqdefault.jpg`;
  $("title").textContent = data.title;
  $("summary").textContent = data.summary;
  $("key-points").replaceChildren(
    ...data.key_points.map((point) => {
      const li = document.createElement("li");
      li.textContent = point;
      return li;
    })
  );

  const postsEl = $("posts");
  postsEl.classList.toggle("is-thread", data.thread);
  postsEl.replaceChildren(
    ...data.posts.map((post) => renderPost(post, data.char_limit, data.char_limit <= 280))
  );

  const unit = data.thread ? "تغريدات" : "منشورات";
  const parts = [
    data.thread ? `🧵 ثريد من ${data.posts.length} تغريدات` : `${data.posts.length} منشورات`,
    TONE_LABELS[data.tone] ?? data.tone,
    LANGUAGE_LABELS[data.language] ?? data.language,
  ];
  $("posts-meta").textContent = parts.join(" · ");
  $("copy-all").querySelector("span").textContent = data.thread ? "نسخ الثريد" : "نسخ الكل";
  $("copy-all").dataset.label = $("copy-all").querySelector("span").textContent;

  const noteEl = $("posts-note");
  noteEl.hidden = data.posts.length === data.requested_posts;
  noteEl.textContent = `تم توليد ${data.posts.length} ${unit} بدل ${data.requested_posts} المطلوبة؛ المحتوى لم يكفِ لأفكار مختلفة أكثر.`;

  $("quotes").replaceChildren(...data.quotes.map(renderQuote));
  $("quotes-box").hidden = data.quotes.length === 0;

  resultEl.hidden = false;
  resultEl.scrollIntoView({ behavior: "smooth", block: "start" });
}

$("copy-all").addEventListener("click", (e) => {
  const separator = $("posts").classList.contains("is-thread") ? "\n\n" : "\n\n---\n\n";
  const all = [...document.querySelectorAll("#posts .post-text")]
    .map((el) => el.value.trim())
    .join(separator);
  copyText(all, e.currentTarget, "تم نسخ كل المنشورات");
});

// ---------- Submit ----------

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (account.require_login && !account.user) {
    openLoginModal();
    return;
  }
  if (isLocked()) {
    openUpgradeModal();
    return;
  }

  const url = urlInput.value.trim();
  if (!YT_RE.test(url)) {
    urlInput.parentElement.classList.add("invalid");
    showStatus("الصق رابط فيديو يوتيوب صحيحًا", true);
    urlInput.focus();
    return;
  }

  const numPosts = readNumPosts();
  if (numPosts === null) {
    syncNumPosts();
    showStatus(`العدد يجب أن يكون رقمًا صحيحًا من ${minPosts()} إلى ${maxPosts}`, true);
    numPostsEl.focus();
    return;
  }

  resultEl.hidden = true;
  statusEl.hidden = true;
  loaderTitle.textContent = "جارٍ جلب نص الفيديو…";
  setLoading(true);
  // لا يرسل الخادم مراحل التقدّم، لذا نغيّر الرسالة بعد ثوانٍ لإظهار أن العمل مستمر
  const stageTimer = setTimeout(
    () => (loaderTitle.textContent = "جارٍ التلخيص وكتابة المنشورات…"),
    3000
  );

  try {
    const response = await fetch("/api/summarize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        language: languageEl.value,
        tone: form.elements.tone.value,
        num_posts: numPosts,
        thread: threadEl.checked,
      }),
    });

    const data = await response.json().catch(() => ({}));
    if (response.status === 401) {
      throw Object.assign(new Error(data.detail), { login: true });
    }
    if (response.status === 402) {
      account.usage.remaining = 0;
      throw Object.assign(new Error(data.detail), { quota: true });
    }
    if (!response.ok) {
      const detail = typeof data.detail === "string" ? data.detail : null;
      throw new Error(detail || `حدث خطأ غير متوقع (${response.status})`);
    }

    account.usage = data.usage;
    setLoading(false);
    renderResult(data);
  } catch (err) {
    if (err.login) {
      await refreshAccount();
      openLoginModal();
    } else if (err.quota) {
      openUpgradeModal();
    } else {
      showStatus(err instanceof TypeError ? "تعذّر الاتصال بالخادم" : err.message, true);
    }
  } finally {
    clearTimeout(stageTimer);
    setLoading(false);
    renderAccount();
  }
});

// ---------- البداية ----------

renderAccount();
if (new URLSearchParams(location.search).get("checkout") === "cancelled") {
  showToast("تم إلغاء عملية الدفع");
  history.replaceState(null, "", location.pathname);
}
