(() => {
  const dialog = document.querySelector("#purchaseDialog");
  const form = document.querySelector("#purchaseForm");
  if (!dialog || !form) return;

  const name = document.querySelector("#purchaseProductName");
  const payLater = document.querySelector("#purchasePayLater");
  const delivery = document.querySelector("#purchaseDelivery");
  const payLaterNote = document.querySelector("#purchasePayLaterNote");
  const deliveryNote = document.querySelector("#purchaseDeliveryNote");
  const variationOptions = document.querySelector("#purchaseVariationOptions");
  const quantityBlock = document.querySelector("#purchaseQuantityBlock");
  const quantityInput = document.querySelector("#purchaseQuantity");
  const addToCartButton = document.querySelector("#addToCartButton");
  const promotionMessage = document.querySelector("#purchasePromotionMessage");
  let discountQuantity = 0;
  let discountValue = "";
  let discountValueCents = 0;
  let unitValueCents = 0;
  const formatMoney = (cents) => new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(cents / 100);

  const updatePromotion = () => {
    if (!discountQuantity) {
      promotionMessage.hidden = true;
      return;
    }
    const variationInputs = variationOptions.querySelectorAll('input[type="number"]');
    const total = variationInputs.length
      ? Array.from(variationInputs).reduce((sum, input) => sum + (Number(input.value) || 0), 0)
      : (Number(quantityInput.value) || 0);
    const remainder = total % discountQuantity;
    const missing = remainder === 0 && total > 0 ? 0 : discountQuantity - remainder;
    const completeKits = Math.floor(total / discountQuantity);
    promotionMessage.replaceChildren();
    promotionMessage.classList.toggle("is-applied", completeKits > 0);
    if (completeKits > 0) {
      const originalValue = total * unitValueCents;
      const appliedDiscount = completeKits * discountValueCents;
      const originalLine = document.createElement("span");
      originalLine.textContent = `Valor: ${formatMoney(originalValue)}`;
      const discountLine = document.createElement("strong");
      discountLine.textContent = `Desconto de ${formatMoney(appliedDiscount)} aplicado!`;
      const totalLine = document.createElement("span");
      totalLine.textContent = `Total a pagar: ${formatMoney(originalValue - appliedDiscount)}`;
      promotionMessage.append(originalLine, discountLine, totalLine);
      if (missing > 0) {
        const nextDiscount = document.createElement("small");
        nextDiscount.textContent = `Adicione mais ${missing} ${missing === 1 ? "item" : "itens"} para ganhar outro desconto.`;
        promotionMessage.append(nextDiscount);
      }
    } else {
      promotionMessage.textContent = `Adicione mais ${missing} ${missing === 1 ? "item" : "itens"} para conseguir ${discountValue} de desconto.`;
    }
    promotionMessage.hidden = false;
  };

  document.querySelectorAll("[data-buy-product]").forEach((button) => {
    button.addEventListener("click", () => {
      form.reset();
      form.action = `/produtos/${button.dataset.productId}/comprar`;
      addToCartButton.formAction = `/produtos/${button.dataset.productId}/carrinho`;
      name.textContent = button.dataset.productName;
      payLater.disabled = button.dataset.acceptsCredit !== "true";
      delivery.disabled = button.dataset.hasDelivery !== "true";
      payLaterNote.textContent = payLater.disabled ? "Este vendedor não aceita pagamento posterior para este doce." : "Você poderá pagar ao vendedor depois.";
      deliveryNote.textContent = delivery.disabled ? "Este doce está disponível somente para retirada." : "O vendedor entregará no local combinado.";
      const hasVariations = button.dataset.hasVariations === "true";
      variationOptions.replaceChildren();
      quantityBlock.hidden = hasVariations;
      quantityInput.disabled = hasVariations;
      discountQuantity = Number(button.dataset.discountQuantity) || 0;
      discountValue = button.dataset.discountValue;
      discountValueCents = Number(button.dataset.discountValueCents) || 0;
      unitValueCents = Number(button.dataset.unitValueCents) || 0;
      if (hasVariations) {
        const template = document.querySelector(`#purchaseVariations${button.dataset.productId}`);
        if (template) variationOptions.appendChild(template.content.cloneNode(true));
      }
      variationOptions.querySelectorAll('input[type="number"]').forEach((input) => {
        input.addEventListener("focus", () => {
          if (input.value === "0") input.value = "";
        });
        input.addEventListener("blur", () => {
          if (input.value === "") input.value = "0";
          updatePromotion();
        });
        input.addEventListener("input", updatePromotion);
      });
      updatePromotion();
      dialog.showModal();
    });
  });
  quantityInput.addEventListener("input", updatePromotion);

  dialog.querySelector(".purchase-dialog-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
})();
