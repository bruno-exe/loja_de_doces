(() => {
  const fields = document.querySelector("#productVariationFields");
  const addButton = document.querySelector("#addProductVariation");
  if (!fields || !addButton) return;

  const connectRemoveButton = (button) => {
    button.addEventListener("click", () => {
      const rows = fields.querySelectorAll(".product-variation-field");
      if (rows.length === 1) {
        rows[0].querySelector("input").value = "";
      } else {
        button.closest(".product-variation-field").remove();
      }
    });
  };
  fields.querySelectorAll("[data-remove-variation]").forEach(connectRemoveButton);
  addButton.addEventListener("click", () => {
    if (fields.children.length >= 20) return;
    const row = document.createElement("div");
    row.className = "product-variation-field";
    row.innerHTML = '<input name="subcategorias" maxlength="120" placeholder="Nome da subcategoria"><button type="button" data-remove-variation aria-label="Remover subcategoria">&times;</button>';
    fields.appendChild(row);
    connectRemoveButton(row.querySelector("button"));
    row.querySelector("input").focus();
  });
})();
