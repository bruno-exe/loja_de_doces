(() => {
  const dialog = document.querySelector("#purchaseDialog");
  const form = document.querySelector("#purchaseForm");
  if (!dialog || !form) return;

  const name = document.querySelector("#purchaseProductName");
  const payLater = document.querySelector("#purchasePayLater");
  const delivery = document.querySelector("#purchaseDelivery");
  const payLaterNote = document.querySelector("#purchasePayLaterNote");
  const deliveryNote = document.querySelector("#purchaseDeliveryNote");

  document.querySelectorAll("[data-buy-product]").forEach((button) => {
    button.addEventListener("click", () => {
      form.reset();
      form.action = `/produtos/${button.dataset.productId}/comprar`;
      name.textContent = button.dataset.productName;
      payLater.disabled = button.dataset.acceptsCredit !== "true";
      delivery.disabled = button.dataset.hasDelivery !== "true";
      payLaterNote.textContent = payLater.disabled ? "Este vendedor não aceita pagamento posterior para este doce." : "Você poderá pagar ao vendedor depois.";
      deliveryNote.textContent = delivery.disabled ? "Este doce está disponível somente para retirada." : "O vendedor entregará no local combinado.";
      dialog.showModal();
    });
  });

  dialog.querySelector(".purchase-dialog-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
})();
