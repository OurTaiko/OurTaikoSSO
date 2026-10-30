document.documentElement.classList.add("js");

document.querySelectorAll(".language-picker select").forEach((select) => {
  select.addEventListener("change", () => select.form.requestSubmit());
});

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
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
