const form = document.getElementById("summarize-form");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const submitBtn = document.getElementById("submit-btn");

function showStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
  statusEl.hidden = false;
}

function renderPost(text) {
  const post = document.createElement("article");
  post.className = "post";
  post.textContent = text;

  const footer = document.createElement("footer");
  const count = document.createElement("span");
  count.textContent = `${[...text].length} / 280`;
  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.textContent = "نسخ";
  copyBtn.onclick = async () => {
    await navigator.clipboard.writeText(text);
    copyBtn.textContent = "تم النسخ ✓";
  };
  footer.append(count, copyBtn);
  post.append(footer);
  return post;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  resultEl.hidden = true;
  submitBtn.disabled = true;
  showStatus("جارٍ جلب النص والتلخيص... قد يستغرق ذلك دقيقة.");

  try {
    const response = await fetch("/api/summarize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: document.getElementById("url").value,
        language: document.getElementById("language").value,
        num_posts: Number(document.getElementById("num-posts").value),
      }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === "string" ? data.detail : "حدث خطأ غير متوقع");
    }

    document.getElementById("title").textContent = data.title;
    document.getElementById("summary").textContent = data.summary;
    const points = document.getElementById("key-points");
    points.replaceChildren(...data.key_points.map((p) => {
      const li = document.createElement("li");
      li.textContent = p;
      return li;
    }));
    document.getElementById("posts").replaceChildren(...data.x_posts.map(renderPost));

    statusEl.hidden = true;
    resultEl.hidden = false;
  } catch (err) {
    showStatus(err.message, true);
  } finally {
    submitBtn.disabled = false;
  }
});
