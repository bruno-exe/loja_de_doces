import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path


logger = logging.getLogger(__name__)
_ocr_lock = threading.Lock()
_OUTPUT_LINE = re.compile(r"^\s*\d+\s+(.+?)\s+([01](?:\.\d+)?)\s*$")
_IGNORED_OUTPUT = (
    "the detection visualized image saved",
    "detection visualized image",
)


class PaddleOcrNcnnError(RuntimeError):
    """Falha controlada ao executar o binario externo de OCR."""


@dataclass(frozen=True)
class OCRLine:
    text: str
    confidence: float


@dataclass(frozen=True)
class OCRResult:
    engine: str
    raw_text: str
    lines: list[OCRLine]
    duration_seconds: float
    success: bool
    error: str | None = None


@dataclass(frozen=True)
class PaddleOcrNcnnConfig:
    root: Path
    executable: Path
    models_dir: Path
    threads: int
    timeout: int

    @classmethod
    def from_environment(cls) -> "PaddleOcrNcnnConfig":
        root = Path(os.getenv("PADDLEOCR_NCNN_ROOT", "/home/adm/PaddleOCR-Lite-Document"))
        executable = Path(os.getenv("PADDLEOCR_NCNN_EXECUTABLE", str(root / "ocr")))
        models_dir = Path(os.getenv("PADDLEOCR_NCNN_MODELS_DIR", str(root / "models")))
        try:
            threads = max(1, min(4, int(os.getenv("PADDLEOCR_NCNN_THREADS", "2"))))
            timeout = max(5, int(os.getenv("PADDLEOCR_NCNN_TIMEOUT", "45")))
        except ValueError as exc:
            raise PaddleOcrNcnnError("As configurações numéricas do PaddleOCR ncnn são inválidas.") from exc
        return cls(root, executable, models_dir, threads, timeout)

    def command(self, image_path: Path) -> list[str]:
        return [
            str(self.executable),
            "system",
            str(self.models_dir / "PP_OCRv5_mobile_det"),
            str(self.models_dir / "PP_OCRv5_mobile_rec"),
            str(self.models_dir / "cls-sim-op"),
            "arm8",
            "FP32",
            str(self.threads),
            "1",
            str(image_path),
            str(self.models_dir / "config.txt"),
            str(self.models_dir / "PP_OCRv5_vocab.txt"),
        ]


def parse_output(stdout: str) -> list[OCRLine]:
    lines = []
    for raw_line in stdout.splitlines():
        stripped = raw_line.strip()
        if not stripped or any(message in stripped.lower() for message in _IGNORED_OUTPUT):
            continue
        match = _OUTPUT_LINE.match(stripped)
        if not match:
            continue
        text = match.group(1).strip()
        if text:
            lines.append(OCRLine(text=text, confidence=float(match.group(2))))
    return lines


def _validate(config: PaddleOcrNcnnConfig, image_path: Path) -> None:
    if not image_path.is_file():
        raise PaddleOcrNcnnError("A imagem do comprovante não foi encontrada.")
    if not config.root.is_dir():
        raise PaddleOcrNcnnError("A pasta do PaddleOCR ncnn não foi encontrada. Verifique PADDLEOCR_NCNN_ROOT.")
    if not config.executable.is_file():
        raise PaddleOcrNcnnError("O executável do PaddleOCR ncnn não foi encontrado. Verifique PADDLEOCR_NCNN_EXECUTABLE.")
    required = (
        "PP_OCRv5_mobile_det.bin", "PP_OCRv5_mobile_det.param",
        "PP_OCRv5_mobile_rec.bin", "PP_OCRv5_mobile_rec.param",
        "cls-sim-op.bin", "cls-sim-op.param", "config.txt", "PP_OCRv5_vocab.txt",
    )
    missing = [name for name in required if not (config.models_dir / name).is_file()]
    if missing:
        raise PaddleOcrNcnnError("Arquivos dos modelos MOBILE do PaddleOCR ncnn não foram encontrados.")


def run_paddleocr(image_path: str | Path, config: PaddleOcrNcnnConfig | None = None) -> OCRResult:
    config = config or PaddleOcrNcnnConfig.from_environment()
    image_path = Path(image_path).resolve()
    _validate(config, image_path)
    started = time.monotonic()
    try:
        with _ocr_lock:
            completed = subprocess.run(
                config.command(image_path),
                cwd=config.root,
                capture_output=True,
                text=True,
                timeout=config.timeout,
                check=False,
            )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - started
        logger.warning("OCR engine=paddle_ncnn duration=%.2fs success=false reason=timeout", duration)
        raise PaddleOcrNcnnError(f"O PaddleOCR ultrapassou o limite de {config.timeout} segundos.") from exc
    except OSError as exc:
        raise PaddleOcrNcnnError("Não foi possível iniciar o executável do PaddleOCR ncnn.") from exc

    duration = time.monotonic() - started
    if completed.returncode != 0:
        logger.warning("OCR engine=paddle_ncnn duration=%.2fs success=false returncode=%s", duration, completed.returncode)
        raise PaddleOcrNcnnError(f"O PaddleOCR terminou com erro (código {completed.returncode}).")
    lines = parse_output(completed.stdout)
    if not lines:
        logger.warning("OCR engine=paddle_ncnn duration=%.2fs success=false reason=empty", duration)
        raise PaddleOcrNcnnError("O PaddleOCR não reconheceu texto na imagem.")
    raw_text = "\n".join(line.text for line in lines)
    logger.info("OCR engine=paddle_ncnn duration=%.2fs success=true", duration)
    return OCRResult("paddle_ncnn", raw_text, lines, duration, True)
