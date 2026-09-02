(function () {
  const form = document.getElementById("profilePhotoForm");
  const fileInput = document.getElementById("profilePhotoFile");
  const previewArea = document.getElementById("photoPreviewArea");
  const previewWrap = document.getElementById("photoPreviewWrap");
  const preview = document.getElementById("profilePhotoPreview");
  const marker = document.getElementById("facePointMarker");
  const faceX = document.getElementById("faceX");
  const faceY = document.getElementById("faceY");
  const instruction = document.getElementById("faceInstruction");
  const status = document.getElementById("photoStatus");
  const automaticButton = document.getElementById("automaticFace");
  const chooseButton = document.getElementById("chooseFace");
  const cameraDialog = document.getElementById("profileCameraDialog");
  const video = document.getElementById("profileCameraVideo");
  const canvas = document.getElementById("profileCameraCanvas");
  let previewUrl = null;
  let selectionMode = false;
  let cameraStream = null;
  const sellerMode = form?.dataset.photoMode === "seller";

  if (!form || !fileInput) return;

  function clearFacePoint() {
    faceX.value = "";
    faceY.value = "";
    marker.hidden = true;
  }

  function useAutomaticDetection() {
    selectionMode = false;
    clearFacePoint();
    automaticButton.classList.add("active");
    chooseButton.classList.remove("active");
    previewWrap.classList.remove("selecting-face");
    instruction.textContent = sellerMode
      ? "A imagem será centralizada automaticamente."
      : "A detecção automática escolherá o maior rosto da foto.";
  }

  function chooseFaceManually() {
    selectionMode = true;
    clearFacePoint();
    chooseButton.classList.add("active");
    automaticButton.classList.remove("active");
    previewWrap.classList.add("selecting-face");
    instruction.textContent = sellerMode
      ? "Toque ou clique na parte dos doces que deve ficar no centro da imagem."
      : "Toque ou clique no centro do rosto que deve aparecer na foto.";
  }

  function showPreview(file) {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    preview.src = previewUrl;
    previewArea.hidden = false;
    status.textContent = sellerMode
      ? "Imagem selecionada. Centralize automaticamente ou defina o foco."
      : "Foto selecionada. Escolha a detecção automática ou indique o rosto.";
    useAutomaticDetection();
  }

  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) showPreview(fileInput.files[0]);
  });
  automaticButton.addEventListener("click", useAutomaticDetection);
  chooseButton.addEventListener("click", chooseFaceManually);

  preview.addEventListener("click", (event) => {
    if (!selectionMode) return;
    const rectangle = preview.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (event.clientX - rectangle.left) / rectangle.width));
    const y = Math.min(1, Math.max(0, (event.clientY - rectangle.top) / rectangle.height));
    faceX.value = x.toFixed(6);
    faceY.value = y.toFixed(6);
    marker.style.left = `${x * 100}%`;
    marker.style.top = `${y * 100}%`;
    marker.hidden = false;
    instruction.textContent = sellerMode
      ? "Foco definido. Você já pode salvar a imagem."
      : "Rosto selecionado. Você já pode salvar a foto.";
  });

  function stopCamera() {
    if (cameraStream) {
      cameraStream.getTracks().forEach((track) => track.stop());
      cameraStream = null;
    }
    if (cameraDialog.open) cameraDialog.close();
  }

  document.getElementById("openProfileCamera").addEventListener("click", async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      fileInput.click();
      return;
    }
    try {
      cameraStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" }, audio: false });
      video.srcObject = cameraStream;
      cameraDialog.showModal();
    } catch (error) {
      status.textContent = "A câmera não foi autorizada. Escolha uma foto do aparelho.";
      fileInput.click();
    }
  });
  document.getElementById("closeProfileCamera").addEventListener("click", stopCamera);
  document.getElementById("takeProfilePhoto").addEventListener("click", () => {
    if (!video.videoWidth) return;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const context = canvas.getContext("2d");
    context.translate(canvas.width, 0);
    context.scale(-1, 1);
    context.drawImage(video, 0, 0);
    canvas.toBlob((blob) => {
      const captured = new File([blob], "foto-camera.jpg", { type: "image/jpeg" });
      const transfer = new DataTransfer();
      transfer.items.add(captured);
      fileInput.files = transfer.files;
      showPreview(captured);
      stopCamera();
    }, "image/jpeg", 0.92);
  });

  form.addEventListener("submit", (event) => {
    if (!fileInput.files.length) {
      event.preventDefault();
      status.textContent = "Escolha ou capture uma foto antes de salvar.";
      return;
    }
    if (selectionMode && (!faceX.value || !faceY.value)) {
      event.preventDefault();
      status.textContent = sellerMode
        ? "Toque na parte da imagem que deve ficar em destaque antes de salvar."
        : "Toque no rosto da pessoa antes de salvar.";
      return;
    }
    status.textContent = sellerMode
      ? "Processando e enquadrando a imagem dos doces..."
      : "Processando e enquadrando o rosto...";
  });
})();
