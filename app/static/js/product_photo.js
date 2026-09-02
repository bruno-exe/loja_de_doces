(function () {
  const form = document.getElementById("newProductForm");
  const fileInput = document.getElementById("productImageFile");
  const previewArea = document.getElementById("productPreviewArea");
  const previewWrap = document.getElementById("productPreviewWrap");
  const preview = document.getElementById("productImagePreview");
  const marker = document.getElementById("productFocusMarker");
  const focusX = document.getElementById("productFocusX");
  const focusY = document.getElementById("productFocusY");
  const instruction = document.getElementById("productFocusInstruction");
  const status = document.getElementById("productImageStatus");
  const centerButton = document.getElementById("centerProductImage");
  const focusButton = document.getElementById("defineProductFocus");
  const dialog = document.getElementById("productCameraDialog");
  const video = document.getElementById("productCameraVideo");
  const canvas = document.getElementById("productCameraCanvas");
  let selectionMode = false;
  let previewUrl = null;
  let cameraStream = null;

  if (!form || !fileInput) return;

  function clearFocus() {
    focusX.value = "";
    focusY.value = "";
    marker.hidden = true;
  }
  function centerImage() {
    selectionMode = false;
    clearFocus();
    centerButton.classList.add("active");
    focusButton.classList.remove("active");
    previewWrap.classList.remove("selecting-face");
    instruction.textContent = "A imagem será centralizada automaticamente.";
  }
  function defineFocus() {
    selectionMode = true;
    clearFocus();
    focusButton.classList.add("active");
    centerButton.classList.remove("active");
    previewWrap.classList.add("selecting-face");
    instruction.textContent = "Toque ou clique na parte do produto que deve ficar no centro.";
  }
  function showPreview(file) {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    preview.src = previewUrl;
    previewArea.hidden = false;
    status.textContent = "Imagem selecionada. Centralize ou defina o foco.";
    centerImage();
  }

  fileInput.addEventListener("change", () => { if (fileInput.files[0]) showPreview(fileInput.files[0]); });
  centerButton.addEventListener("click", centerImage);
  focusButton.addEventListener("click", defineFocus);
  preview.addEventListener("click", (event) => {
    if (!selectionMode) return;
    const rectangle = preview.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (event.clientX - rectangle.left) / rectangle.width));
    const y = Math.min(1, Math.max(0, (event.clientY - rectangle.top) / rectangle.height));
    focusX.value = x.toFixed(6);
    focusY.value = y.toFixed(6);
    marker.style.left = `${x * 100}%`;
    marker.style.top = `${y * 100}%`;
    marker.hidden = false;
    instruction.textContent = "Foco definido. Você já pode criar o produto.";
  });

  function stopCamera() {
    if (cameraStream) { cameraStream.getTracks().forEach((track) => track.stop()); cameraStream = null; }
    if (dialog.open) dialog.close();
  }
  document.getElementById("openProductCamera").addEventListener("click", async () => {
    if (!navigator.mediaDevices?.getUserMedia) { fileInput.click(); return; }
    try {
      cameraStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
      video.srcObject = cameraStream;
      dialog.showModal();
    } catch (error) {
      status.textContent = "A câmera não foi autorizada. Escolha uma imagem do aparelho.";
      fileInput.click();
    }
  });
  document.getElementById("closeProductCamera").addEventListener("click", stopCamera);
  document.getElementById("takeProductPhoto").addEventListener("click", () => {
    if (!video.videoWidth) return;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    canvas.toBlob((blob) => {
      const captured = new File([blob], "produto-camera.jpg", { type: "image/jpeg" });
      const transfer = new DataTransfer();
      transfer.items.add(captured);
      fileInput.files = transfer.files;
      showPreview(captured);
      stopCamera();
    }, "image/jpeg", 0.92);
  });

  form.addEventListener("submit", (event) => {
    if (!fileInput.files.length) { event.preventDefault(); status.textContent = "Escolha ou capture uma imagem antes de criar o produto."; return; }
    if (selectionMode && (!focusX.value || !focusY.value)) { event.preventDefault(); status.textContent = "Toque na imagem para definir o foco."; return; }
    status.textContent = "Criando produto...";
  });
})();
