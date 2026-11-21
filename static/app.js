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

// Send each form once. A sign-in can take a few seconds (password check, then the redirects back
// to the app that asked); a second click meanwhile re-sends a CSRF token that signing in has
// already replaced, and the server answers 403. Registered after data-confirm so a cancelled
// confirm does not count as sent.
document.querySelectorAll("form").forEach((form) => {
  if (form.method !== "post") return;
  form.addEventListener("submit", (event) => {
    if (event.defaultPrevented) return;
    if (form.dataset.sending) {
      event.preventDefault();
      return;
    }
    form.dataset.sending = "1";
    // Not disabled: a disabled submitter would drop its name/value from the request.
    const buttons = form.querySelectorAll("button[type=submit], button:not([type])");
    buttons.forEach((button) => button.setAttribute("aria-busy", "true"));
    // If the browser never leaves the page, do not leave a dead button behind.
    setTimeout(() => {
      delete form.dataset.sending;
      buttons.forEach((button) => button.removeAttribute("aria-busy"));
    }, 15000);
  });
});
// Coming back with the Back button restores the page as it was left; let its forms be sent again.
window.addEventListener("pageshow", (event) => {
  if (!event.persisted) return;
  document.querySelectorAll("form[data-sending]").forEach((form) => {
    delete form.dataset.sending;
    form.querySelectorAll("[aria-busy]").forEach((button) => button.removeAttribute("aria-busy"));
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
