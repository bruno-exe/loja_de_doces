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
    """Erro legivel ao abrir a imagem ou executar o Tesseract."""


VALUE_LABELS = ("valor enviado", "valor da transferencia", "valor do pagamento", "valor pago", "valor")
DATE_LABELS = ("data e hora", "realizado em", "data do pagamento", "data da transferencia", "data")
RECIPIENT_LABELS = ("destinatario", "recebedor", "quem recebeu", "nome do favorecido", "favorecido", "destino", "para")
PAYER_LABELS = ("nome do pagador", "pagador", "quem pagou", "dados do pagador", "origem")
INSTITUTION_LABELS = ("instituicao financeira", "instituicao", "banco", "ispb")
E2E_LABELS = ("endtoendid", "end to end id", "end_to_end_id", "e2e id", "e2e", "id de transacao pix", "id da transacao pix")
TRANSACTION_LABELS = ("numero da transacao", "n. transacao", "nº transacao", "id da transacao", "id transacao")
ALL_LABELS = VALUE_LABELS + DATE_LABELS + RECIPIENT_LABELS + PAYER_LABELS + INSTITUTION_LABELS + E2E_LABELS + TRANSACTION_LABELS

CURRENCY_MONEY_RE = re.compile(
    r"(?<![A-Z0-9])R\s*[$S]\s*([0-9OI]+(?:[.,][0-9OI]{1,2})?|[0-9OI]{1,3}(?:\.[0-9OI]{3})+(?:,[0-9OI]{1,2})?)(?![A-Z0-9])",
    re.IGNORECASE,
)
DECIMAL_MONEY_RE = re.compile(r"(?<!\d)([0-9]{1,6}[.,][0-9]{1,2})(?!\d)")
DATE_RE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2})\b")
TEXT_DATE_RE = re.compile(
    r"\b(\d{1,2})\s*(?:/|de\s+)?\s*(janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\s*(?:/|de\s+)?\s*(\d{4})\b",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"\b([01]?\d|2[0-3])[:h]([0-5]\d)(?::([0-5]\d))?\b", re.IGNORECASE)
DOCUMENT_RE = re.compile(r"(?<!\d)(\d{3}[.\s]?\d{3}[.\s]?\d{3}[-\s]?\d{2}|\d{2}[.\s]?\d{3}[.\s]?\d{3}[/\s]?\d{4}[-\s]?\d{2})(?!\d)")
E2E_RE = re.compile(r"\bE[A-Z0-9]{20,40}\b", re.IGNORECASE)
MONTHS = {
    name: index
    for index, name in enumerate(
        ("janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"),
        1,
    )
}


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _configure_tesseract() -> None:
    configured = os.getenv("TESSERACT_CMD", "").strip()
    if configured:
        if not Path(configured).is_file():
            raise PixReceiptOcrError(f"TESSERACT_CMD não aponta para um arquivo válido: {configured}")
        pytesseract.pytesseract.tesseract_cmd = configured
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


def prepare_image(image: Image.Image, max_dimension: int = 1200) -> np.ndarray:
    """Reduz imagens grandes e gera uma unica matriz em escala de cinza."""
    rgb = np.asarray(image)
    height, width = rgb.shape[:2]
    largest = max(height, width)
    if largest > max_dimension:
        scale = max_dimension / largest
        rgb = cv2.resize(rgb, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def generate_ocr_variants(image: Image.Image) -> dict[str, np.ndarray]:
    gray = prepare_image(image)
    height = gray.shape[0]
    # O recorte e uma view da matriz, portanto nao cria outra imagem completa.
    top_end = max(1, round(height * 0.50))
    return {"general": gray, "top_value": gray[:top_end, :]}


def _run_ocr(processed: np.ndarray, config: str) -> str:
    _configure_tesseract()
    try:
        return pytesseract.image_to_string(processed, lang="por", config=config).strip()
    except pytesseract.TesseractNotFoundError as exc:
        raise PixReceiptOcrError("Tesseract OCR não foi encontrado no sistema.") from exc
    except pytesseract.TesseractError as exc:
        message = str(exc)
        if "por.traineddata" in message or "Failed loading language" in message:
            raise PixReceiptOcrError("O pacote de idioma português (por.traineddata) não está instalado no Tesseract.") from exc
        raise PixReceiptOcrError(f"O Tesseract não conseguiu processar o comprovante: {message}") from exc


def run_ocr_passes(variants: dict[str, np.ndarray]) -> list[tuple[str, str]]:
    """Executa exatamente duas chamadas ao Tesseract no fluxo padrao."""
    return [
        ("general", _run_ocr(variants["general"], "--oem 3 --psm 6")),
        ("top_value", _run_ocr(variants["top_value"], "--oem 3 --psm 11 -c tessedit_char_whitelist=R$S0123456789,.")),
    ]


def _lines(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]


def _near_labels(lines: list[str], labels: tuple[str, ...], distance: int = 2) -> list[str]:
    candidates = []
    for index, line in enumerate(lines):
        folded = _fold(line)
        for label in labels:
            if label not in folded:
                continue
            remainder = line[folded.find(label) + len(label):].lstrip(" :-–—")
            if remainder:
                candidates.append(remainder)
            candidates.extend(lines[index + 1:index + 1 + distance])
            break
    return candidates


def _decimal_value(raw: str) -> Decimal | None:
    cleaned = re.sub(r"[^0-9OI,.]", "", raw.upper()).replace("O", "0").replace("I", "1")
    if not cleaned:
        return None
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    try:
        value = Decimal(cleaned)
        return value.quantize(Decimal("0.01")) if value >= 0 else None
    except InvalidOperation:
        return None


def _money_from_text(text: str, *, allow_bare: bool) -> Decimal | None:
    lines = _lines(text)
    for match in CURRENCY_MONEY_RE.finditer(text):
        value = _decimal_value(match.group(1))
        if value is not None:
            return value
    for candidate in _near_labels(lines, VALUE_LABELS, 1):
        match = CURRENCY_MONEY_RE.search(candidate) or DECIMAL_MONEY_RE.search(candidate)
        if match:
            value = _decimal_value(match.group(1))
            if value is not None:
                return value
    if allow_bare:
        for line in lines:
            match = re.fullmatch(r"\s*([0-9]{1,6}[.,][0-9]{1,2})\s*", line)
            if match:
                return _decimal_value(match.group(1))
    return None


def _normalize_date(raw: str) -> str | None:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    match = TEXT_DATE_RE.search(raw)
    if match:
        try:
            return date(int(match.group(3)), MONTHS[_fold(match.group(2))], int(match.group(1))).isoformat()
        except (ValueError, KeyError):
            pass
    return None


def _extract_datetime(text: str) -> tuple[str | None, str | None]:
    date_match = DATE_RE.search(text) or TEXT_DATE_RE.search(text)
    time_match = TIME_RE.search(text)
    extracted_date = _normalize_date(date_match.group(0)) if date_match else None
    extracted_time = None
    if time_match:
        extracted_time = f"{int(time_match.group(1)):02d}:{time_match.group(2)}"
        if time_match.group(3):
            extracted_time += f":{time_match.group(3)}"
    return extracted_date, extracted_time


def _extract_text_field(lines: list[str], labels: tuple[str, ...]) -> str | None:
    for candidate in _near_labels(lines, labels, 2):
        folded = _fold(candidate)
        if len(candidate) >= 3 and not any(folded == label or folded.startswith(label + ":") for label in ALL_LABELS):
            return candidate.strip(" :-–—")
    return None


def _extract_recipient_document(lines: list[str], text: str) -> str | None:
    for candidate in _near_labels(lines, RECIPIENT_LABELS, 5) + [text]:
        match = DOCUMENT_RE.search(candidate)
        if match:
            digits = re.sub(r"\D", "", match.group(1))
            if len(digits) in {11, 14}:
                return digits
    return None


def _extract_institution(lines: list[str]) -> str | None:
    labeled = _extract_text_field(lines, INSTITUTION_LABELS)
    if labeled:
        return labeled
    known = ("mercado pago", "nubank", "itau", "santander", "bradesco", "banco do brasil", "caixa", "inter", "picpay")
    for line in lines:
        folded = _fold(line)
        if any(name in folded for name in known) or re.search(r"\b(banco|bank|pagamentos|financeira)\b", folded):
            return line
    return None


def _extract_e2e(lines: list[str], text: str) -> str | None:
    for candidate in _near_labels(lines, E2E_LABELS, 2) + [text]:
        match = E2E_RE.search(re.sub(r"\s+", "", candidate))
        if match:
            return match.group(0).upper()
    return None


def _extract_transaction_id(lines: list[str]) -> str | None:
    for candidate in _near_labels(lines, TRANSACTION_LABELS, 2):
        match = re.search(r"[A-Za-z0-9-]{6,60}", candidate)
        if match:
            return match.group(0)
    return None


def parse_pix_receipt(text_passes: list[tuple[str, str]], data: dict | None = None, image_height: int | None = None) -> dict:
    """Interpreta o texto sem executar OCR adicional; data/image_height mantem compatibilidade."""
    del data, image_height
    texts = dict(text_passes)
    general_text = texts.get("general", "").strip()
    top_text = texts.get("top_value", "").strip()
    lines = _lines(general_text)
    value = _money_from_text(top_text, allow_bare=True) or _money_from_text(general_text, allow_bare=True)
    extracted_date, extracted_time = _extract_datetime(general_text)
    recipient = _extract_text_field(lines, RECIPIENT_LABELS)
    e2e_id = _extract_e2e(lines, general_text)
    return {
        "valor": value,
        "data": extracted_date,
        "hora": extracted_time,
        "destinatario": recipient,
        "cpf_cnpj_destinatario": _extract_recipient_document(lines, general_text),
        "pagador": _extract_text_field(lines, PAYER_LABELS),
        "instituicao": _extract_institution(lines),
        "e2e_id": e2e_id,
        "transaction_id": _extract_transaction_id(lines),
        "pix_id": e2e_id,
        "confidence": {
            "valor": 0.8 if value is not None else 0.0,
            "data": 0.8 if extracted_date else 0.0,
            "destinatario": 0.7 if recipient else 0.0,
            "e2e_id": 0.8 if e2e_id else 0.0,
        },
        "texto_ocr": general_text,
    }


def extract_pix_receipt(source: str | Path | bytes | BinaryIO | Image.Image) -> dict:
    image = _open_image(source)
    variants = generate_ocr_variants(image)
    return parse_pix_receipt(run_ocr_passes(variants))


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
    return normalized_key in normalized_text or any(
        normalized_key[index:index + 4] in normalized_text
        for index in range(len(normalized_key) - 3)
    )


def compare_pix_receipt(
    extracted: dict,
    *,
    expected_value: Decimal | str | None = None,
    expected_date: date | str | None = None,
    expected_recipient: str | None = None,
    expected_pix_key: str | None = None,
) -> dict:
    expected_decimal = _decimal_value(str(expected_value)) if expected_value is not None else None
    expected_date_text = expected_date.isoformat() if isinstance(expected_date, date) else expected_date
    return {
        "valor_confere": None if expected_decimal is None or extracted.get("valor") is None else extracted["valor"] == expected_decimal,
        "data_confere": None if not expected_date_text or not extracted.get("data") else extracted["data"] == expected_date_text,
        "destinatario_confere": _name_matches(extracted.get("destinatario"), expected_recipient),
        "chave_pix_confere": _pix_key_matches(extracted.get("texto_ocr", ""), expected_pix_key),
    }
