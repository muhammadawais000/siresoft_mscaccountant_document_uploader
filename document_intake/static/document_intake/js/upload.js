// The only custom JavaScript on the list page. htmx sends the upload; this file adds
// three small conveniences that HTML attributes cannot do on their own.
(() => {
  const form = document.getElementById("upload-form");
  if (!form) return;
  const input = document.getElementById("file-input");
  const zone = form.querySelector("[data-dropzone]");
  const progress = document.getElementById("upload-progress");

  // 1. Upload as soon as files are chosen (no extra "Send" button).
  input.addEventListener("change", () => {
    if (input.files.length) form.requestSubmit();
  });

  // 2. Drag and drop: put the dropped files into the input, then upload.
  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("dropzone--active");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("dropzone--active"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("dropzone--active");
    input.files = event.dataTransfer.files;
    form.requestSubmit();
  });

  // 3. Progress bar while the files are sent, then clear the file input.
  form.addEventListener("htmx:xhr:progress", (event) => {
    if (event.detail.lengthComputable) {
      progress.value = Math.round((event.detail.loaded / event.detail.total) * 100);
    }
  });
  form.addEventListener("htmx:afterRequest", () => {
    input.value = "";
    progress.value = 0;
  });
})();
