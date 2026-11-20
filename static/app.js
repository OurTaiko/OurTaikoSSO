document.documentElement.classList.add("js");

// Floating language ball: the options fan out around it. Without JS the footer row does the same job.
document.querySelectorAll(".lang-fab").forEach((fab) => {
  const ball = fab.querySelector(".lang-ball");
  const setOpen = (open) => {
    fab.classList.toggle("open", open);
    ball.setAttribute("aria-expanded", String(open));
  };
  ball.addEventListener("click", () => {
    const open = !fab.classList.contains("open");
    setOpen(open);
    if (open) fab.querySelector(".lang-options button").focus({ preventScroll: true });
  });
  document.addEventListener("click", (event) => {
    if (!fab.contains(event.target)) setOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && fab.classList.contains("open")) {
      setOpen(false);
      ball.focus();
    }
  });
});

// Keep the current section visible when the section bar scrolls sideways on phones.
document.querySelectorAll(".sub-nav .wrap").forEach((bar) => {
  const current = bar.querySelector("[aria-current=page]");
  if (current && bar.scrollWidth > bar.clientWidth) {
    bar.scrollLeft = current.offsetLeft - (bar.clientWidth - current.offsetWidth) / 2;
  }
});

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});

document.querySelectorAll(".code-input").forEach((input) => {
  input.addEventListener("input", () => {
    const start = input.selectionStart;
    input.value = input.value.toUpperCase();
    input.setSelectionRange(start, start);
  });
});

document.querySelectorAll(".avatar-form input[type=file]").forEach((input) => {
  input.addEventListener("change", () => {
    const file = input.files[0];
    const target = document.querySelector(".profile-banner .identity-avatar");
    if (!file || !target || !/^image\/(jpeg|png|webp)$/.test(file.type)) return;
    // Preview only; the server re-encodes and validates the upload.
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      const img = document.createElement("img");
      img.src = reader.result;
      img.alt = "";
      target.replaceChildren(img);
    });
    reader.readAsDataURL(file);
  });
});
