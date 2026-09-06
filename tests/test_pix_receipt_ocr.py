from datetime import date
from decimal import Decimal
from io import BytesIO

import numpy as np
from PIL import Image

from app.services import pix_receipt_ocr


SAMPLE_OCR = """Comprovante Pix
Valor enviado
R$ 25,00
Realizado em 02/09/2026 as 22:35
Destinatario
JOAO DA SILVA
CPF 123.456.789-01
Pagador
MARIA SOUZA
Instituicao
BANCO XYZ
EndToEndId E1234567820260902ABCDEF1234567890
Chave Pix: joao.silva@criar
"""


def image_bytes() -> bytes:
    image = Image.new("RGB", (700, 1000), "white")
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def mock_ocr(monkeypatch, general: str, top: str = "") -> None:
    monkeypatch.setattr(
        pix_receipt_ocr,
        "run_ocr_passes",
        lambda variants: [("general", general), ("top_value", top)],
    )


def parse_text(general: str, top: str = "") -> dict:
    return pix_receipt_ocr.parse_pix_receipt(
        [("general", general), ("top_value", top)]
    )


def test_extracts_pix_receipt_fields_without_changing_source(monkeypatch) -> None:
    mock_ocr(monkeypatch, SAMPLE_OCR)
    original = image_bytes()
    source = BytesIO(original)
    result = pix_receipt_ocr.extract_pix_receipt(source)
    assert source.getvalue() == original
    assert result["valor"] == Decimal("25.00")
    assert result["data"] == "2026-09-02"
    assert result["hora"] == "22:35"
    assert result["destinatario"] == "JOAO DA SILVA"
    assert result["cpf_cnpj_destinatario"] == "12345678901"
    assert result["pagador"] == "MARIA SOUZA"
    assert result["instituicao"] == "BANCO XYZ"
    assert result["e2e_id"] == "E1234567820260902ABCDEF1234567890"
    assert result["texto_ocr"] == SAMPLE_OCR.strip()


def test_compares_expected_order_and_seller_data(monkeypatch) -> None:
    mock_ocr(monkeypatch, SAMPLE_OCR)
    extracted = pix_receipt_ocr.extract_pix_receipt(image_bytes())
    assert pix_receipt_ocr.compare_pix_receipt(
        extracted,
        expected_value=Decimal("25.00"),
        expected_date=date(2026, 9, 2),
        expected_recipient="Joao da Silva",
        expected_pix_key="joao.silva@criar",
    ) == {
        "valor_confere": True,
        "data_confere": True,
        "destinatario_confere": True,
        "chave_pix_confere": True,
    }


def test_returns_none_for_fields_not_found(monkeypatch) -> None:
    mock_ocr(monkeypatch, "Comprovante ilegivel\nOperacao concluida")
    result = pix_receipt_ocr.extract_pix_receipt(image_bytes())
    for field in (
        "valor", "data", "hora", "destinatario", "cpf_cnpj_destinatario",
        "pagador", "instituicao", "e2e_id",
    ):
        assert result[field] is None


def test_recognizes_supported_currency_formats() -> None:
    cases = {
        "R$ 5": "5.00",
        "R$5": "5.00",
        "R$ 5,00": "5.00",
        "R$ 20,50": "20.50",
        "R$ 20.50": "20.50",
        "20,50": "20.50",
    }
    for text, expected in cases.items():
        assert parse_text(text)["valor"] == Decimal(expected)


def test_top_region_recovers_value_missed_by_general_ocr() -> None:
    result = parse_text("Comprovante Pix\n4/9/2026 16:54", "R$ 5")
    assert result["valor"] == Decimal("5.00")
    assert result["data"] == "2026-09-04"


def test_does_not_use_cpf_date_time_or_transaction_id_as_value() -> None:
    text = """Data 04/09/2026 16:54:16
CPF 123.456.789-01
ID da transacao ABC12345678901234567890"""
    assert parse_text(text)["valor"] is None


def test_extracts_written_date_recipient_and_time() -> None:
    text = """Comprovante Pix
4/setembro/2026 as 16:54:16
Recebedor
MARIA DA SILVA"""
    result = parse_text(text)
    assert result["data"] == "2026-09-04"
    assert result["hora"] == "16:54:16"
    assert result["destinatario"] == "MARIA DA SILVA"


def test_main_pipeline_calls_tesseract_exactly_twice(monkeypatch) -> None:
    calls = []

    def fake_run(image, config):
        calls.append(config)
        return "Comprovante Pix" if len(calls) == 1 else "R$ 5"

    monkeypatch.setattr(pix_receipt_ocr, "_run_ocr", fake_run)
    variants = {
        "general": np.zeros((100, 100), dtype=np.uint8),
        "top_value": np.zeros((50, 100), dtype=np.uint8),
    }
    result = pix_receipt_ocr.parse_pix_receipt(pix_receipt_ocr.run_ocr_passes(variants))
    assert len(calls) == 2
    assert "--psm 6" in calls[0]
    assert "--psm 11" in calls[1]
    assert result["valor"] == Decimal("5.00")


def test_large_image_is_reduced_without_upscaling_small_images() -> None:
    large = pix_receipt_ocr.prepare_image(Image.new("RGB", (3000, 2000), "white"))
    small = pix_receipt_ocr.prepare_image(Image.new("RGB", (600, 400), "white"))
    assert large.shape == (800, 1200)
    assert small.shape == (400, 600)
