import os
import re
import shutil
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import cv2
import numpy as np
import pytesseract
from PIL import Image, UnidentifiedImageError


class PixReceiptOcrError(RuntimeError):
    """Erro legível ao abrir a imagem ou executar o Tesseract."""


VALUE_LABELS = ("valor enviado", "valor da transferencia", "valor do pagamento", "valor pago", "valor")
DATE_LABELS = ("data e hora", "realizado em", "data do pagamento", "data da transferencia", "data")
RECIPIENT_LABELS = ("destinatario", "recebedor", "quem recebeu", "nome do favorecido", "favorecido", "para")
PAYER_LABELS = ("nome do pagador", "pagador", "quem pagou", "dados do pagador", "origem")
INSTITUTION_LABELS = ("instituicao", "instituicao financeira", "banco", "ispb")
E2E_LABELS = ("endtoendid", "end to end id", "e2e id", "e2e", "id da transacao", "id transacao")


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    return "".join(character for character in value if not unicodedata.combining(character)).lower()


def _configure_tesseract() -> None:
    configured = os.getenv("TESSERACT_CMD", "").strip()
    if configured:
        executable = Path(configured)
        if not executable.is_file():
            raise PixReceiptOcrError(f"TESSERACT_CMD não aponta para um arquivo válido: {configured}")
        pytesseract.pytesseract.tesseract_cmd = str(executable)
        return
    discovered = shutil.which("tesseract")
    if discovered:
        pytesseract.pytesseract.tesseract_cmd = discovered
        return
    if os.name == "nt":
        candidates = (
            Path(os.getenv("ProgramFiles", "C:/Program Files")) / "Tesseract-OCR" / "tesseract.exe",
            Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
        )
        for candidate in candidates:
            if candidate.is_file():
                pytesseract.pytesseract.tesseract_cmd = str(candidate)
                return
    raise PixReceiptOcrError("Tesseract OCR não foi encontrado. Instale-o ou defina TESSERACT_CMD com o caminho do executável.")


def _open_image(source: str | Path | bytes | BinaryIO | Image.Image) -> Image.Image:
    try:
        if isinstance(source, Image.Image):
            image = source.copy()
        elif isinstance(source, bytes):
            image = Image.open(BytesIO(source))
        else:
            image = Image.open(source)
        image.load()
        return image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, TypeError) as exc:
        raise PixReceiptOcrError("Não foi possível abrir a imagem do comprovante.") from exc


def _preprocess(image: Image.Image) -> np.ndarray:
    rgb = np.asarray(image)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if gray.shape[1] < 1400:
        scale = min(2.0, 1400 / max(gray.shape[1], 1))
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    denoised = cv2.bilateralFilter(gray, 5, 35, 35)
    return cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8)).apply(denoised)


def _run_ocr(processed: np.ndarray) -> str:
    _configure_tesseract()
    try:
        return pytesseract.image_to_string(processed, lang="por", config="--oem 3 --psm 6").strip()
    except pytesseract.TesseractNotFoundError as exc:
        raise PixReceiptOcrError("Tesseract OCR não foi encontrado no sistema.") from exc
    except pytesseract.TesseractError as exc:
        message = str(exc)
        if "por.traineddata" in message or "Failed loading language" in message:
            raise PixReceiptOcrError("O pacote de idioma português (por.traineddata) não está instalado no Tesseract.") from exc
        raise PixReceiptOcrError(f"O Tesseract não conseguiu processar o comprovante: {message}") from exc


def _lines(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]


def _near_labels(lines: list[str], labels: tuple[str, ...], distance: int = 2) -> list[str]:
    candidates: list[str] = []
    for index, line in enumerate(lines):
        folded = _fold(line)
        for label in labels:
            if label not in folded:
                continue
            position = folded.find(label)
            remainder = line[position + len(label):].lstrip(" :-–—")
            if remainder:
                candidates.append(remainder)
            candidates.extend(lines[index + 1:index + 1 + distance])
            break
    return candidates


MONEY_RE = re.compile(r"(?:R\$\s*)?(\d{1,3}(?:\.\d{3})*,\d{2}|\d+(?:[.,]\d{2}))", re.IGNORECASE)


def _decimal_value(raw: str) -> Decimal | None:
    cleaned = re.sub(r"[^\d,.]", "", raw)
    if not cleaned:
        return None
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _extract_value(lines: list[str], text: str) -> Decimal | None:
    for candidate in _near_labels(lines, VALUE_LABELS):
        match = MONEY_RE.search(candidate)
        if match and (value := _decimal_value(match.group(1))) is not None:
            return value
    for match in re.finditer(r"R\$\s*([\d.,]+)", text, re.IGNORECASE):
        if (value := _decimal_value(match.group(1))) is not None:
            return value
    return None


DATE_RE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2})\b")
TIME_RE = re.compile(r"\b([01]?\d|2[0-3])[:h]([0-5]\d)(?::[0-5]\d)?\b", re.IGNORECASE)


def _normalize_date(raw: str) -> str | None:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _extract_datetime(lines: list[str], text: str) -> tuple[str | None, str | None]:
    candidates = _near_labels(lines, DATE_LABELS, 2) + [text]
    found_date = found_time = None
    for candidate in candidates:
        if found_date is None and (date_match := DATE_RE.search(candidate)):
            found_date = _normalize_date(date_match.group(1))
        if found_time is None and (time_match := TIME_RE.search(candidate)):
            found_time = f"{int(time_match.group(1)):02d}:{time_match.group(2)}"
        if found_date and found_time:
            break
    return found_date, found_time


ALL_LABELS = VALUE_LABELS + DATE_LABELS + RECIPIENT_LABELS + PAYER_LABELS + INSTITUTION_LABELS + E2E_LABELS


def _extract_text_field(lines: list[str], labels: tuple[str, ...]) -> str | None:
    for candidate in _near_labels(lines, labels, 2):
        folded = _fold(candidate)
        if len(candidate) >= 3 and not any(folded == label or folded.startswith(label + ":") for label in ALL_LABELS):
            return candidate.strip(" :-–—")
    return None


DOCUMENT_RE = re.compile(r"(?<!\d)(\d{3}[.\s]?\d{3}[.\s]?\d{3}[-\s]?\d{2}|\d{2}[.\s]?\d{3}[.\s]?\d{3}[/\s]?\d{4}[-\s]?\d{2})(?!\d)")


def _extract_recipient_document(lines: list[str], text: str) -> str | None:
    candidates = _near_labels(lines, RECIPIENT_LABELS, 5)
    for candidate in candidates + [text]:
        if match := DOCUMENT_RE.search(candidate):
            digits = re.sub(r"\D", "", match.group(1))
            if len(digits) in {11, 14}:
                return digits
    return None


def _extract_institution(lines: list[str]) -> str | None:
    labeled = _extract_text_field(lines, INSTITUTION_LABELS)
    if labeled:
        return labeled
    for line in lines:
        if re.search(r"\b(banco|bank|pagamentos|financeira|institui[cç][aã]o)\b", line, re.IGNORECASE):
            return line
    return None


E2E_RE = re.compile(r"\bE[A-Z0-9]{20,40}\b", re.IGNORECASE)


def _extract_e2e(lines: list[str], text: str) -> str | None:
    for candidate in _near_labels(lines, E2E_LABELS, 2) + [text]:
        compact = re.sub(r"\s+", "", candidate)
        if match := E2E_RE.search(compact):
            return match.group(0).upper()
        if candidate != text:
            tokens = re.findall(r"[A-Za-z0-9-]{12,45}", candidate)
            if tokens:
                return tokens[0]
    return None


def extract_pix_receipt(source: str | Path | bytes | BinaryIO | Image.Image) -> dict:
    """Executa OCR sem escrever ou modificar o comprovante original."""
    image = _open_image(source)
    text = _run_ocr(_preprocess(image)).strip()
    lines = _lines(text)
    extracted_date, extracted_time = _extract_datetime(lines, text)
    return {
        "valor": _extract_value(lines, text),
        "data": extracted_date,
        "hora": extracted_time,
        "destinatario": _extract_text_field(lines, RECIPIENT_LABELS),
        "cpf_cnpj_destinatario": _extract_recipient_document(lines, text),
        "pagador": _extract_text_field(lines, PAYER_LABELS),
        "instituicao": _extract_institution(lines),
        "e2e_id": _extract_e2e(lines, text),
        "texto_ocr": text,
    }


def _normalized_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _fold(value))


def _name_matches(extracted: str | None, expected: str | None) -> bool | None:
    if not expected or not extracted:
        return None
    expected_tokens = {re.sub(r"[^a-z0-9]", "", token) for token in _fold(expected).split() if len(token) > 2}
    extracted_tokens = {re.sub(r"[^a-z0-9]", "", token) for token in _fold(extracted).split() if len(token) > 2}
    return bool(expected_tokens) and len(expected_tokens & extracted_tokens) / len(expected_tokens) >= 0.6


def _pix_key_matches(text: str, expected_key: str | None) -> bool | None:
    if not expected_key:
        return None
    normalized_text = _normalized_identifier(text)
    normalized_key = _normalized_identifier(expected_key)
    if len(normalized_key) < 4:
        return None
    if normalized_key in normalized_text:
        return True
    return any(normalized_key[index:index + 4] in normalized_text for index in range(len(normalized_key) - 3))


def compare_pix_receipt(
    extracted: dict,
    *,
    expected_value: Decimal | str | None = None,
    expected_date: date | str | None = None,
    expected_recipient: str | None = None,
    expected_pix_key: str | None = None,
) -> dict:
    """Compara dados esperados sem decidir ou confirmar automaticamente o pagamento."""
    expected_decimal = _decimal_value(str(expected_value)) if expected_value is not None else None
    expected_date_text = expected_date.isoformat() if isinstance(expected_date, date) else expected_date
    return {
        "valor_confere": None if expected_decimal is None or extracted.get("valor") is None else extracted["valor"] == expected_decimal,
        "data_confere": None if not expected_date_text or not extracted.get("data") else extracted["data"] == expected_date_text,
        "destinatario_confere": _name_matches(extracted.get("destinatario"), expected_recipient),
        "chave_pix_confere": _pix_key_matches(extracted.get("texto_ocr", ""), expected_pix_key),
    }
