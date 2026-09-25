const X_LIMIT = 280;
const YT_RE = /(youtube\.com|youtu\.be|youtube-nocookie\.com)\/|^[\w-]{11}$/;

const $ = (id) => document.getElementById(id);
const form = $("summarize-form");
const urlInput = $("url");
const submitBtn = $("submit-btn");
const statusEl = $("status");
const resultEl = $("result");
const numPostsEl = $("num-posts");
const postTemplate = $("post-template");

// ---------- Helpers ----------

function showStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
  statusEl.hidden = false;
}

function setLoading(loading) {
  submitBtn.disabled = loading;
  submitBtn.classList.toggle("loading", loading);
}

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
    const original = button.textContent;
    button.textContent = "تم النسخ ✓";
    button.classList.add("done");
    setTimeout(() => {
      button.textContent = original;
      button.classList.remove("done");
    }, 1500);
  } catch {
    showStatus("تعذّر النسخ، انسخ النص يدويًا", true);
  }
}

// X تحسب الأحرف بنقاط Unicode، لذا نستخدم [...text] بدل text.length
const charCount = (text) => [...text].length;

// ---------- Stepper ----------

const maxPosts = Number(numPostsEl.dataset.max) || 5;

function setNumPosts(value) {
  const n = Math.min(Math.max(value, 1), maxPosts);
  numPostsEl.value = numPostsEl.textContent = n;
  document.querySelector('[data-step="-1"]').disabled = n <= 1;
  document.querySelector('[data-step="1"]').disabled = n >= maxPosts;
}

document.querySelectorAll("[data-step]").forEach((btn) =>
  btn.addEventListener("click", () =>
    setNumPosts(Number(numPostsEl.textContent) + Number(btn.dataset.step))
  )
);
setNumPosts(Math.min(3, maxPosts));

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

function renderPost(text) {
  const node = postTemplate.content.firstElementChild.cloneNode(true);
  const textEl = node.querySelector(".post-text");
  const countEl = node.querySelector(".count");
  const shareEl = node.querySelector(".share");

  const update = () => {
    const value = textEl.innerText.trim();
    const n = charCount(value);
    countEl.textContent = `${n} / ${X_LIMIT}`;
    countEl.classList.toggle("warn", n > X_LIMIT - 20 && n <= X_LIMIT);
    countEl.classList.toggle("over", n > X_LIMIT);
    shareEl.href = `https://x.com/intent/post?text=${encodeURIComponent(value)}`;
  };

  textEl.textContent = text;
  textEl.addEventListener("input", update);
  node.querySelector(".copy").addEventListener("click", (e) =>
    copyText(textEl.innerText.trim(), e.currentTarget)
  );
  update();
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
  $("posts").replaceChildren(...data.x_posts.map(renderPost));
  resultEl.hidden = false;
  resultEl.scrollIntoView({ behavior: "smooth", block: "start" });
}

$("copy-all").addEventListener("click", (e) => {
  const all = [...document.querySelectorAll("#posts .post-text")]
    .map((el) => el.innerText.trim())
    .join("\n\n---\n\n");
  copyText(all, e.currentTarget);
});

// ---------- Submit ----------

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = urlInput.value.trim();

  if (!YT_RE.test(url)) {
    urlInput.parentElement.classList.add("invalid");
    showStatus("الصق رابط فيديو يوتيوب صحيحًا", true);
    urlInput.focus();
    return;
  }

  resultEl.hidden = true;
  setLoading(true);
  showStatus("جارٍ جلب نص الفيديو…");
  // لا يرسل الخادم مراحل التقدّم، لذا نغيّر الرسالة بعد ثوانٍ لإظهار أن العمل مستمر
  const stageTimer = setTimeout(
    () => showStatus("جارٍ التلخيص وكتابة المنشورات… قد يستغرق ذلك دقيقة."),
    3000
  );

  try {
    const response = await fetch("/api/summarize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        language: form.elements.language.value,
        num_posts: Number(numPostsEl.textContent),
      }),
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof data.detail === "string" ? data.detail : null;
      throw new Error(detail || `حدث خطأ غير متوقع (${response.status})`);
    }

    statusEl.hidden = true;
    renderResult(data);
  } catch (err) {
    const message = err instanceof TypeError ? "تعذّر الاتصال بالخادم" : err.message;
    showStatus(message, true);
  } finally {
    clearTimeout(stageTimer);
    setLoading(false);
  }
});
