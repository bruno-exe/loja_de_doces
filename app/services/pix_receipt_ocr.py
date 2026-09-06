import os
import re
import tempfile
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import BinaryIO

from PIL import Image

from .paddleocr_ncnn import OCRLine, PaddleOcrNcnnError, run_paddleocr


class PixReceiptOcrError(RuntimeError):
    """Erro público do serviço de leitura de comprovantes."""


VALUE_LABELS = ("valor enviado", "valor da transferencia", "valor do pagamento", "valor pago", "valor")
DATE_LABELS = ("data e hora", "realizado em", "data do pagamento", "data da transferencia", "data")
RECIPIENT_LABELS = ("destinatario", "recebedor", "quem recebeu", "nome do favorecido", "favorecido", "destino", "para")
PAYER_LABELS = ("nome do pagador", "pagador", "quem pagou", "dados do pagador", "origem")
INSTITUTION_LABELS = ("instituicao financeira", "instituicao", "banco", "ispb")
E2E_LABELS = ("endtoendid", "end to end id", "end_to_end_id", "e2e id", "e2e", "id de transacao pix", "id da transacao pix")
TRANSACTION_LABELS = ("numero da transacao", "n. transacao", "nº transacao", "id da transacao", "id transacao")
ALL_LABELS = VALUE_LABELS + DATE_LABELS + RECIPIENT_LABELS + PAYER_LABELS + INSTITUTION_LABELS + E2E_LABELS + TRANSACTION_LABELS

CURRENCY_MONEY_RE = re.compile(r"(?<![A-Z0-9])R\s*[$S]\s*([0-9OI]+(?:[.,][0-9OI]{1,2})?)(?![A-Z0-9])", re.IGNORECASE)
DECIMAL_MONEY_RE = re.compile(r"(?<!\d)([0-9]{1,6}[.,][0-9]{1,2})(?!\d)")
DATE_RE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2})\b")
TEXT_DATE_RE = re.compile(r"\b(\d{1,2})\s*(?:/|de\s+)?\s*(janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\s*(?:/|de\s+)?\s*(\d{4})\b", re.IGNORECASE)
TIME_RE = re.compile(r"\b([01]?\d|2[0-3])[:h]([0-5]\d)(?::([0-5]\d))?\b", re.IGNORECASE)
DOCUMENT_RE = re.compile(r"(?<!\d)(\d{3}[.\s]?\d{3}[.\s]?\d{3}[-\s]?\d{2}|\d{2}[.\s]?\d{3}[.\s]?\d{3}[/\s]?\d{4}[-\s]?\d{2})(?!\d)")
E2E_RE = re.compile(r"\bE[A-Z0-9]{20,40}\b", re.IGNORECASE)
MONTHS = {name: index for index, name in enumerate(("janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"), 1)}


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


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


def _extract_value(ocr_lines: list[OCRLine], text: str) -> tuple[Decimal | None, float]:
    # O valor com simbolo monetario tem prioridade. O limite baixo preserva o
    # caso real R$ 5 com confianca 0.869408 sem exigir uma leitura perfeita.
    for line in sorted(ocr_lines, key=lambda item: item.confidence, reverse=True):
        match = CURRENCY_MONEY_RE.search(line.text)
        if match and line.confidence >= 0.45:
            return _decimal_value(match.group(1)), line.confidence
    lines = _lines(text)
    for candidate in _near_labels(lines, VALUE_LABELS, 1):
        match = CURRENCY_MONEY_RE.search(candidate) or DECIMAL_MONEY_RE.search(candidate)
        if match:
            return _decimal_value(match.group(1)), 0.6
    for line in ocr_lines:
        match = re.fullmatch(r"\s*([0-9]{1,6}[.,][0-9]{1,2})\s*", line.text)
        if match and line.confidence >= 0.60:
            return _decimal_value(match.group(1)), line.confidence
    return None, 0.0


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
    return next((line for line in lines if any(name in _fold(line) for name in known)), None)


def _extract_e2e(lines: list[str], text: str) -> str | None:
    for candidate in _near_labels(lines, E2E_LABELS, 2) + [text]:
        match = E2E_RE.search(re.sub(r"\s+", "", candidate))
        if match:
            return match.group(0)
    return None


def _extract_transaction_id(lines: list[str]) -> str | None:
    for candidate in _near_labels(lines, TRANSACTION_LABELS, 2):
        match = re.search(r"[A-Za-z0-9-]{6,60}", candidate)
        if match:
            return match.group(0)
    return None


def parse_pix_receipt(text: str, ocr_lines: list[OCRLine] | None = None) -> dict:
    ocr_lines = ocr_lines or [OCRLine(line, 1.0) for line in _lines(text)]
    lines = _lines(text)
    value, value_confidence = _extract_value(ocr_lines, text)
    extracted_date, extracted_time = _extract_datetime(text)
    recipient = _extract_text_field(lines, RECIPIENT_LABELS)
    e2e_id = _extract_e2e(lines, text)
    return {
        "valor": value,
        "data": extracted_date,
        "hora": extracted_time,
        "destinatario": recipient,
        "cpf_cnpj_destinatario": _extract_recipient_document(lines, text),
        "pagador": _extract_text_field(lines, PAYER_LABELS),
        "instituicao": _extract_institution(lines),
        "e2e_id": e2e_id,
        "transaction_id": _extract_transaction_id(lines),
        "pix_id": e2e_id,
        "confidence": {"valor": round(value_confidence, 3), "data": 0.8 if extracted_date else 0.0, "destinatario": 0.7 if recipient else 0.0, "e2e_id": 0.8 if e2e_id else 0.0},
        "texto_ocr": text.strip(),
    }


def _extract_from_path(path: Path) -> dict:
    try:
        result = run_paddleocr(path)
    except PaddleOcrNcnnError as exc:
        raise PixReceiptOcrError(str(exc)) from exc
    return parse_pix_receipt(result.raw_text, result.lines)


def extract_pix_receipt(source: str | Path | bytes | BinaryIO | Image.Image) -> dict:
    engine = os.getenv("OCR_ENGINE", "paddle_ncnn").strip().lower()
    if engine != "paddle_ncnn":
        raise PixReceiptOcrError("OCR_ENGINE deve ser paddle_ncnn nesta versão do Come Doce.")
    if isinstance(source, (str, Path)):
        return _extract_from_path(Path(source))
    try:
        if isinstance(source, Image.Image):
            image = source.copy().convert("RGB")
        elif isinstance(source, bytes):
            from io import BytesIO
            image = Image.open(BytesIO(source)).convert("RGB")
        else:
            image = Image.open(source).convert("RGB")
        with tempfile.NamedTemporaryFile(suffix=".jpg") as temporary:
            image.save(temporary.name, "JPEG", quality=95)
            return _extract_from_path(Path(temporary.name))
    except PixReceiptOcrError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise PixReceiptOcrError("Não foi possível abrir a imagem do comprovante.") from exc


def _normalized_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _fold(value))


def _name_matches(extracted: str | None, expected: str | None) -> bool | None:
    if not expected or not extracted:
        return None
    expected_tokens = {_normalized_identifier(token) for token in expected.split() if len(token) > 2}
    extracted_tokens = {_normalized_identifier(token) for token in extracted.split() if len(token) > 2}
    return bool(expected_tokens) and len(expected_tokens & extracted_tokens) / len(expected_tokens) >= 0.6


def _pix_key_matches(text: str, expected_key: str | None) -> bool | None:
    if not expected_key:
        return None
    normalized_text = _normalized_identifier(text)
    normalized_key = _normalized_identifier(expected_key)
    if len(normalized_key) < 4:
        return None
    return normalized_key in normalized_text or any(normalized_key[index:index + 4] in normalized_text for index in range(len(normalized_key) - 3))


def compare_pix_receipt(extracted: dict, *, expected_value: Decimal | str | None = None, expected_date: date | str | None = None, expected_recipient: str | None = None, expected_pix_key: str | None = None) -> dict:
    expected_decimal = _decimal_value(str(expected_value)) if expected_value is not None else None
    expected_date_text = expected_date.isoformat() if isinstance(expected_date, date) else expected_date
    return {
        "valor_confere": None if expected_decimal is None or extracted.get("valor") is None else extracted["valor"] == expected_decimal,
        "data_confere": None if not expected_date_text or not extracted.get("data") else extracted["data"] == expected_date_text,
        "destinatario_confere": _name_matches(extracted.get("destinatario"), expected_recipient),
        "chave_pix_confere": _pix_key_matches(extracted.get("texto_ocr", ""), expected_pix_key),
    }
