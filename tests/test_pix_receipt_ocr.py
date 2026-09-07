from datetime import date
from decimal import Decimal

import pytest

from app.services import pix_receipt_ocr
from app.services.paddleocr_ncnn import OCRLine, OCRResult, PaddleOcrNcnnError


TEXT = """Comprovante de Pix
4/setembro/2026 às 16:54:16
R$ 5
Recebedor
Fernando Cesar Ribeiro Junior
Mercado Pago
Número da transação
176328143979
ID de transação Pix
E10573521202609041954Se4u0nN2LTn"""


def test_parses_real_paddle_example():
    lines = [OCRLine(line, 0.87 if line == "R$ 5" else 0.99) for line in TEXT.splitlines()]
    result = pix_receipt_ocr.parse_pix_receipt(TEXT, lines)
    assert result["valor"] == Decimal("5.00")
    assert result["data"] == "2026-09-04"
    assert result["hora"] == "16:54:16"
    assert result["destinatario"] == "Fernando Cesar Ribeiro Junior"
    assert result["transaction_id"] == "176328143979"
    assert result["pix_id"] == "E10573521202609041954Se4u0nN2LTn"
    assert result["confidence"]["valor"] == 0.87


@pytest.mark.parametrize(
    "source,expected_date,expected_time",
    [
        ("04/09/26 às 08:05", "2026-09-04", "08:05"),
        ("04/08/2026 19:07:03", "2026-08-04", "19:07:03"),
        ("4/setembro/2026 às 16:54:16", "2026-09-04", "16:54:16"),
    ],
)
def test_recognizes_date_and_time_variations(source, expected_date, expected_time):
    result = pix_receipt_ocr.parse_pix_receipt(source)
    assert result["data"] == expected_date
    assert result["hora"] == expected_time


def test_ignores_invalid_time_before_valid_date_and_time():
    result = pix_receipt_ocr.parse_pix_receipt("16:541\n04/09/2026 às 16:54:16")
    assert result["data"] == "2026-09-04"
    assert result["hora"] == "16:54:16"


@pytest.mark.parametrize("source,expected", [("R$5", "5.00"), ("R$ 5,00", "5.00"), ("R$20,50", "20.50")])
def test_normalizes_values(source, expected):
    assert pix_receipt_ocr.parse_pix_receipt(source)["valor"] == Decimal(expected)


def test_extract_uses_paddle_only(monkeypatch, tmp_path):
    image = tmp_path / "receipt.jpg"
    image.touch()
    calls = []
    def fake_paddle(path):
        calls.append(path)
        return OCRResult("paddle_ncnn", TEXT, [OCRLine("R$ 5", 0.869408)], 1.0, True)
    monkeypatch.setattr(pix_receipt_ocr, "run_paddleocr", fake_paddle)
    assert pix_receipt_ocr.extract_pix_receipt(image)["valor"] == Decimal("5.00")
    assert calls == [image]


def test_paddle_failure_becomes_public_ocr_error(monkeypatch, tmp_path):
    image = tmp_path / "receipt.jpg"
    image.touch()
    monkeypatch.setattr(pix_receipt_ocr, "run_paddleocr", lambda path: (_ for _ in ()).throw(PaddleOcrNcnnError("indisponível")))
    with pytest.raises(pix_receipt_ocr.PixReceiptOcrError, match="indisponível"):
        pix_receipt_ocr.extract_pix_receipt(image)


def test_compare_contract_is_preserved():
    extracted = pix_receipt_ocr.parse_pix_receipt(TEXT + "\nChave Pix: vendedor@criar")
    assert pix_receipt_ocr.compare_pix_receipt(extracted, expected_value=Decimal("5.00"), expected_date=date(2026, 9, 4), expected_recipient="Fernando Cesar Ribeiro Junior", expected_pix_key="vendedor@criar") == {"valor_confere": True, "data_confere": True, "destinatario_confere": True, "chave_pix_confere": True}
