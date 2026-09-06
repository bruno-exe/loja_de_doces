import os, re, shutil, statistics, unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import cv2
import numpy as np
import pytesseract
from PIL import Image, UnidentifiedImageError
from pytesseract import Output


class PixReceiptOcrError(RuntimeError):
    """Erro legível ao abrir a imagem ou executar o Tesseract."""


VALUE_LABELS = ("valor enviado", "valor da transferencia", "valor do pagamento", "valor pago", "valor")
DATE_LABELS = ("data e hora", "realizado em", "data do pagamento", "data da transferencia", "data")
RECIPIENT_LABELS = ("destinatario", "recebedor", "quem recebeu", "nome do favorecido", "favorecido", "para", "destino")
PAYER_LABELS = ("nome do pagador", "pagador", "quem pagou", "dados do pagador", "origem")
INSTITUTION_LABELS = ("instituicao", "instituicao financeira", "banco", "ispb")
E2E_LABELS = ("endtoendid", "end to end id", "end_to_end_id", "e2e id", "e2e", "id de transacao pix", "id da transacao pix")
TRANSACTION_LABELS = ("numero da transacao", "n. transacao", "nº transacao", "id da transacao", "id transacao")
ALL_LABELS = VALUE_LABELS + DATE_LABELS + RECIPIENT_LABELS + PAYER_LABELS + INSTITUTION_LABELS + E2E_LABELS + TRANSACTION_LABELS


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    return "".join(char for char in value if not unicodedata.combining(char)).lower()


def _configure_tesseract() -> None:
    configured = os.getenv("TESSERACT_CMD", "").strip()
    if configured:
        if not Path(configured).is_file():
            raise PixReceiptOcrError(f"TESSERACT_CMD não aponta para um arquivo válido: {configured}")
        pytesseract.pytesseract.tesseract_cmd = configured
        return
    if discovered := shutil.which("tesseract"):
        pytesseract.pytesseract.tesseract_cmd = discovered
        return
    if os.name == "nt":
        for candidate in (Path(os.getenv("ProgramFiles", "C:/Program Files")) / "Tesseract-OCR/tesseract.exe", Path(os.getenv("LOCALAPPDATA", "")) / "Programs/Tesseract-OCR/tesseract.exe"):
            if candidate.is_file():
                pytesseract.pytesseract.tesseract_cmd = str(candidate)
                return
    raise PixReceiptOcrError("Tesseract OCR não foi encontrado. Instale-o ou defina TESSERACT_CMD com o caminho do executável.")


def _open_image(source: str | Path | bytes | BinaryIO | Image.Image) -> Image.Image:
    try:
        image = source.copy() if isinstance(source, Image.Image) else Image.open(BytesIO(source) if isinstance(source, bytes) else source)
        image.load()
        return image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, TypeError) as exc:
        raise PixReceiptOcrError("Não foi possível abrir a imagem do comprovante.") from exc


def prepare_image(image: Image.Image, max_dimension: int = 1800) -> np.ndarray:
    rgb = np.asarray(image)
    height, width = rgb.shape[:2]
    scale = min(max_dimension / max(height, width), 2.0)
    if scale != 1:
        rgb = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
    return rgb


def generate_ocr_variants(image: Image.Image) -> dict[str, np.ndarray]:
    rgb = prepare_image(image)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    contrast = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8)).apply(cv2.bilateralFilter(gray, 5, 35, 35))
    threshold = cv2.adaptiveThreshold(contrast, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11)
    height = contrast.shape[0]
    return {
        "original": rgb,
        "gray": gray,
        "contrast": contrast,
        "threshold": threshold,
        "top_value": contrast[int(height * .12):max(int(height * .58), 1), :],
    }


def _tesseract(function, image, config: str, **kwargs):
    _configure_tesseract()
    try:
        return function(image, lang="por", config=config, **kwargs)
    except pytesseract.TesseractNotFoundError as exc:
        raise PixReceiptOcrError("Tesseract OCR não foi encontrado no sistema.") from exc
    except pytesseract.TesseractError as exc:
        message = str(exc)
        if "por.traineddata" in message or "Failed loading language" in message:
            raise PixReceiptOcrError("O pacote de idioma português (por.traineddata) não está instalado no Tesseract.") from exc
        raise PixReceiptOcrError(f"O Tesseract não conseguiu processar o comprovante: {message}") from exc


def _run_ocr(processed: np.ndarray) -> str:
    return str(_tesseract(pytesseract.image_to_string, processed, "--oem 3 --psm 6")).strip()


def _run_ocr_config(processed: np.ndarray, config: str) -> str:
    return str(_tesseract(pytesseract.image_to_string, processed, config)).strip()


def _run_ocr_data(processed: np.ndarray) -> dict:
    return _tesseract(pytesseract.image_to_data, processed, "--oem 3 --psm 11 -c preserve_interword_spaces=1", output_type=Output.DICT)


def run_ocr_passes(variants: dict[str, np.ndarray]) -> tuple[list[tuple[str, str]], dict]:
    texts = [("general", _run_ocr(variants["contrast"])), ("sparse", _run_ocr_config(variants["gray"], "--oem 3 --psm 11")), ("threshold", _run_ocr_config(variants["threshold"], "--oem 3 --psm 11")), ("top_value", _run_ocr_config(variants["top_value"], "--oem 3 --psm 11 -c tessedit_char_whitelist=R$S0123456789,."))]
    return texts, _run_ocr_data(variants["contrast"])


def _lines(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]


def _near_labels(lines: list[str], labels: tuple[str, ...], distance: int = 2) -> list[str]:
    result = []
    for index, line in enumerate(lines):
        folded = _fold(line)
        for label in labels:
            if label in folded:
                remainder = line[folded.find(label) + len(label):].lstrip(" :-–—")
                if remainder:
                    result.append(remainder)
                result.extend(lines[index + 1:index + 1 + distance])
                break
    return result


CURRENCY_MONEY_RE = re.compile(r"(?<![A-Z0-9])R\s*[$S]\s*([0-9OI]+(?:[.,][0-9OI]{1,2})?|[0-9OI]{1,3}(?:\.[0-9OI]{3})+(?:,[0-9OI]{1,2})?)(?![A-Z0-9])", re.I)
DECIMAL_MONEY_RE = re.compile(r"(?<!\d)([0-9]{1,6}[.,][0-9]{1,2})(?!\d)")


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


@dataclass
class ValueCandidate:
    value: Decimal
    source: str
    confidence: float
    y_ratio: float | None = None
    contains_currency_symbol: bool = False
    font_height: int = 0
    evidence: set[str] = field(default_factory=set)


def find_money_candidates(text_passes: list[tuple[str, str]], data: dict, image_height: int) -> list[ValueCandidate]:
    candidates = []
    for source, text in text_passes:
        lines = _lines(text)
        for match in CURRENCY_MONEY_RE.finditer(text):
            value = _decimal_value(match.group(1))
            if value is not None:
                candidates.append(ValueCandidate(value, source, .82 if source == "top_value" else .72, contains_currency_symbol=True, evidence={source}))
        # Sem simbolo monetario, somente uma linha composta exclusivamente pelo
        # numero e aceita. Isso evita transformar datas, horas, CPF e IDs em valor.
        for line in lines:
            if match := re.fullmatch(r"\s*([0-9]{1,6}[.,][0-9]{1,2})\s*", line):
                if (value := _decimal_value(match.group(1))) is not None:
                    candidates.append(ValueCandidate(value, f"{source}:bare", .58, evidence={source, "bare"}))
        for nearby in _near_labels(lines, VALUE_LABELS, 2):
            match = CURRENCY_MONEY_RE.search(nearby) or DECIMAL_MONEY_RE.search(nearby)
            if match and (value := _decimal_value(match.group(1))) is not None:
                candidates.append(ValueCandidate(value, f"{source}:label", .78, contains_currency_symbol=bool(CURRENCY_MONEY_RE.search(nearby)), evidence={source, "label"}))
    tokens, count = [], len(data.get("text", []))
    for index in range(count):
        text = str(data["text"][index]).strip()
        if not text:
            continue
        try:
            conf = max(0., float(data.get("conf", [0] * count)[index])) / 100
        except (ValueError, TypeError, IndexError):
            conf = 0.
        tokens.append({"text": text, "left": int(data["left"][index]), "top": int(data["top"][index]), "width": int(data["width"][index]), "height": int(data["height"][index]), "conf": conf, "line": tuple(data.get(key, [0] * count)[index] for key in ("block_num", "par_num", "line_num"))})
    heights = [token["height"] for token in tokens if token["height"] > 0]
    median_height = statistics.median(heights) if heights else 1
    for index, token in enumerate(tokens):
        compact, number_token = token["text"].replace(" ", ""), None
        match = CURRENCY_MONEY_RE.search(compact)
        if match:
            raw = match.group(1)
        elif re.fullmatch(r"R[$S]", compact, re.I):
            nearby = [item for item in tokens[index + 1:index + 4] if item["line"] == token["line"] and item["left"] >= token["left"] + token["width"]]
            nearby.sort(key=lambda item: item["left"])
            number_token = nearby[0] if nearby else None
            if not number_token or number_token["left"] - token["left"] - token["width"] > max(token["height"] * 4, 50) or not re.fullmatch(r"[0-9OI]+(?:[.,][0-9OI]{1,2})?", number_token["text"], re.I):
                continue
            raw = number_token["text"]
        else:
            continue
        value = _decimal_value(raw)
        if value is None:
            continue
        height = max(token["height"], number_token["height"] if number_token else 0)
        conf = min(token["conf"], number_token["conf"] if number_token else token["conf"])
        y_ratio = token["top"] / max(image_height, 1)
        # A caixa so ganha prioridade quando combina a confianca real do
        # Tesseract com posicao/tamanho compativeis com um valor principal.
        score = .35 + conf * .30 + (.10 if y_ratio < .6 else 0) + (min(height / median_height, 2.5) - 1) * .08
        candidates.append(ValueCandidate(value, "image_to_data", min(.95, max(.4, score)), y_ratio, True, height, {"boxes"}))
    return candidates


def merge_money_candidates(candidates: list[ValueCandidate]) -> list[ValueCandidate]:
    grouped = {}
    for candidate in candidates:
        if candidate.value not in grouped:
            grouped[candidate.value] = candidate
            continue
        current = grouped[candidate.value]
        current.evidence |= candidate.evidence
        current.confidence = min(.99, max(current.confidence, candidate.confidence) + min(.15, .04 * (len(current.evidence) - 1)))
        current.contains_currency_symbol |= candidate.contains_currency_symbol
        current.font_height = max(current.font_height, candidate.font_height)
    return sorted(grouped.values(), key=lambda item: (item.confidence, item.contains_currency_symbol, item.font_height), reverse=True)


DATE_RE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2})\b")
TEXT_DATE_RE = re.compile(r"\b(\d{1,2})\s*(?:/|de\s+)?\s*(janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\s*(?:/|de\s+)?\s*(\d{4})\b", re.I)
TIME_RE = re.compile(r"\b([01]?\d|2[0-3])[:h]([0-5]\d)(?::([0-5]\d))?\b", re.I)
MONTHS = {name: index for index, name in enumerate(("janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"), 1)}


def _normalize_date(raw: str) -> str | None:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    if match := TEXT_DATE_RE.search(raw):
        try:
            return date(int(match.group(3)), MONTHS[_fold(match.group(2))], int(match.group(1))).isoformat()
        except (ValueError, KeyError):
            pass
    return None


def _extract_datetime(lines: list[str], text: str) -> tuple[str | None, str | None]:
    found_date = found_time = None
    for candidate in _near_labels(lines, DATE_LABELS, 2) + [text]:
        if found_date is None:
            match = DATE_RE.search(candidate) or TEXT_DATE_RE.search(candidate)
            found_date = _normalize_date(match.group(0)) if match else None
        if found_time is None and (match := TIME_RE.search(candidate)):
            found_time = f"{int(match.group(1)):02d}:{match.group(2)}" + (f":{match.group(3)}" if match.group(3) else "")
        if found_date and found_time:
            break
    return found_date, found_time


def _extract_text_field(lines: list[str], labels: tuple[str, ...]) -> str | None:
    for candidate in _near_labels(lines, labels, 2):
        folded = _fold(candidate)
        if len(candidate) >= 3 and not any(folded == label or folded.startswith(label + ":") for label in ALL_LABELS):
            return candidate.strip(" :-–—")
    return None


DOCUMENT_RE = re.compile(r"(?<!\d)(\d{3}[.\s]?\d{3}[.\s]?\d{3}[-\s]?\d{2}|\d{2}[.\s]?\d{3}[.\s]?\d{3}[/\s]?\d{4}[-\s]?\d{2})(?!\d)")
E2E_RE = re.compile(r"\bE[A-Z0-9]{20,40}\b", re.I)


def _extract_recipient_document(lines: list[str], text: str) -> str | None:
    for candidate in _near_labels(lines, RECIPIENT_LABELS, 5) + [text]:
        if match := DOCUMENT_RE.search(candidate):
            digits = re.sub(r"\D", "", match.group(1))
            if len(digits) in {11, 14}:
                return digits
    return None


def _extract_institution(lines: list[str]) -> str | None:
    if labeled := _extract_text_field(lines, INSTITUTION_LABELS):
        return labeled
    known = ("mercado pago", "nubank", "itau", "santander", "bradesco", "banco do brasil", "caixa", "inter", "picpay")
    for line in lines:
        folded = _fold(line)
        if any(name in folded for name in known) or re.search(r"\b(banco|bank|pagamentos|financeira)\b", folded):
            return line
    return None


def _extract_e2e(lines: list[str], text: str) -> str | None:
    for candidate in _near_labels(lines, E2E_LABELS, 2) + [text]:
        compact = re.sub(r"\s+", "", candidate)
        if match := E2E_RE.search(compact):
            return match.group(0).upper()
        if candidate != text and (tokens := re.findall(r"[A-Za-z0-9-]{12,45}", candidate)):
            return tokens[0]
    return None


def _extract_transaction_id(lines: list[str]) -> str | None:
    for candidate in _near_labels(lines, TRANSACTION_LABELS, 2):
        if token := re.search(r"[A-Za-z0-9-]{6,60}", candidate):
            return token.group(0)
    return None


def calculate_field_confidence(value_candidate: ValueCandidate | None, found_date: str | None, recipient: str | None, e2e_id: str | None) -> dict[str, float]:
    return {"valor": round(value_candidate.confidence, 2) if value_candidate else 0., "data": .9 if found_date else 0., "destinatario": .82 if recipient else 0., "e2e_id": .92 if e2e_id else 0.}


def parse_pix_receipt(text_passes: list[tuple[str, str]], data: dict, image_height: int) -> dict:
    primary = text_passes[0][1].strip() if text_passes else ""
    combined_lines = []
    for _, text in text_passes:
        for line in _lines(text):
            if line not in combined_lines:
                combined_lines.append(line)
    combined = "\n".join(combined_lines)
    candidates = merge_money_candidates(find_money_candidates(text_passes, data, image_height))
    best = candidates[0] if candidates and candidates[0].confidence >= .55 else None
    found_date, found_time = _extract_datetime(combined_lines, combined)
    recipient, e2e = _extract_text_field(combined_lines, RECIPIENT_LABELS), _extract_e2e(combined_lines, combined)
    return {"valor": best.value if best else None, "data": found_date, "hora": found_time, "destinatario": recipient, "cpf_cnpj_destinatario": _extract_recipient_document(combined_lines, combined), "pagador": _extract_text_field(combined_lines, PAYER_LABELS), "instituicao": _extract_institution(combined_lines), "e2e_id": e2e, "transaction_id": _extract_transaction_id(combined_lines), "pix_id": e2e, "confidence": calculate_field_confidence(best, found_date, recipient, e2e), "texto_ocr": primary}


def extract_pix_receipt(source: str | Path | bytes | BinaryIO | Image.Image) -> dict:
    image = _open_image(source)
    variants = generate_ocr_variants(image)
    text_passes, data = run_ocr_passes(variants)
    return parse_pix_receipt(text_passes, data, variants["contrast"].shape[0])


def _normalized_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _fold(value))


def _name_matches(extracted: str | None, expected: str | None) -> bool | None:
    if not expected or not extracted:
        return None
    expected_tokens = {re.sub(r"[^a-z0-9]", "", token) for token in _fold(expected).split() if len(token) > 2}
    extracted_tokens = {re.sub(r"[^a-z0-9]", "", token) for token in _fold(extracted).split() if len(token) > 2}
    return bool(expected_tokens) and len(expected_tokens & extracted_tokens) / len(expected_tokens) >= .6


def _pix_key_matches(text: str, expected_key: str | None) -> bool | None:
    if not expected_key:
        return None
    text, key = _normalized_identifier(text), _normalized_identifier(expected_key)
    if len(key) < 4:
        return None
    return key in text or any(key[index:index + 4] in text for index in range(len(key) - 3))


def compare_pix_receipt(extracted: dict, *, expected_value: Decimal | str | None = None, expected_date: date | str | None = None, expected_recipient: str | None = None, expected_pix_key: str | None = None) -> dict:
    expected_decimal = _decimal_value(str(expected_value)) if expected_value is not None else None
    expected_date_text = expected_date.isoformat() if isinstance(expected_date, date) else expected_date
    return {"valor_confere": None if expected_decimal is None or extracted.get("valor") is None else extracted["valor"] == expected_decimal, "data_confere": None if not expected_date_text or not extracted.get("data") else extracted["data"] == expected_date_text, "destinatario_confere": _name_matches(extracted.get("destinatario"), expected_recipient), "chave_pix_confere": _pix_key_matches(extracted.get("texto_ocr", ""), expected_pix_key)}
