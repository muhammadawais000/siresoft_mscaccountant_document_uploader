// Row selection on the documents list: tick rows, "select all", and the bulk bar.
// The table is re-rendered by the server (filters, paging, polling), so the ticked ids
// are kept here and ticked again after every swap.
(() => {
  const bar = document.getElementById("bulkbar");
  if (!bar) return;
  const count = document.getElementById("bulkbar-count");
  const bulkDelete = document.getElementById("bulk-delete");
  const selected = new Set();

  const rows = () => Array.from(document.querySelectorAll("[data-select-row]"));

  function render() {
    const boxes = rows();
    boxes.forEach((box) => {
      box.checked = selected.has(box.value);
      box.closest("tr")?.classList.toggle("is-selected", box.checked);
    });
    const all = document.querySelector("[data-select-all]");
    if (all) {
      const ticked = boxes.filter((box) => box.checked).length;
      all.disabled = boxes.length === 0;
      all.checked = boxes.length > 0 && ticked === boxes.length;
      all.indeterminate = ticked > 0 && ticked < boxes.length;
    }
    count.textContent = `${selected.size} selected`;
    bulkDelete.textContent =
      selected.size === 1 ? "Delete 1 document" : `Delete ${selected.size} documents`;
    if (selected.size) {
      bar.hidden = false;
      requestAnimationFrame(() => bar.classList.add("is-open"));
    } else {
      bar.classList.remove("is-open");
      bar.hidden = true;
    }
  }

  document.body.addEventListener("change", (event) => {
    const box = event.target;
    if (box.matches("[data-select-row]")) {
      box.checked ? selected.add(box.value) : selected.delete(box.value);
      render();
    } else if (box.matches("[data-select-all]")) {
      rows().forEach((row) => (box.checked ? selected.add(row.value) : selected.delete(row.value)));
      render();
    }
  });

  bar.querySelector("[data-clear-selection]").addEventListener("click", () => {
    selected.clear();
    render();
  });

  // Fade the ticked rows while the bulk delete runs.
  bulkDelete.addEventListener("htmx:beforeRequest", () => {
    rows()
      .filter((box) => box.checked)
      .forEach((box) => box.closest("tr")?.classList.add("is-removing"));
  });

  // After the table is replaced, keep only ids still on the page, then tick them again.
  document.body.addEventListener("htmx:afterSettle", () => {
    const present = new Set(rows().map((box) => box.value));
    for (const id of selected) if (!present.has(id)) selected.delete(id);
    render();
  });
})();
