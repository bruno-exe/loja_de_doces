from datetime import date
from decimal import Decimal
from io import BytesIO

from PIL import Image

from app.services import pix_receipt_ocr


SAMPLE_OCR = """Comprovante Pix
Valor enviado
R$ 25,00
Realizado em 02/09/2026 às 22:35
Destinatário
JOAO DA SILVA
CPF 123.456.789-01
Pagador
MARIA SOUZA
Instituição
BANCO XYZ
EndToEndId E1234567820260902ABCDEF1234567890
Chave Pix: joao.silva@criar
"""


def image_bytes() -> bytes:
    image = Image.new("RGB", (700, 1000), "white")
    output = BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def test_extracts_pix_receipt_fields_without_changing_source(monkeypatch) -> None:
    monkeypatch.setattr(pix_receipt_ocr, "_run_ocr", lambda processed: SAMPLE_OCR)
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
    monkeypatch.setattr(pix_receipt_ocr, "_run_ocr", lambda processed: SAMPLE_OCR)
    extracted = pix_receipt_ocr.extract_pix_receipt(image_bytes())

    comparison = pix_receipt_ocr.compare_pix_receipt(
        extracted,
        expected_value=Decimal("25.00"),
        expected_date=date(2026, 9, 2),
        expected_recipient="João da Silva",
        expected_pix_key="joao.silva@criar",
    )

    assert comparison == {
        "valor_confere": True,
        "data_confere": True,
        "destinatario_confere": True,
        "chave_pix_confere": True,
    }


def test_returns_none_for_fields_not_found(monkeypatch) -> None:
    monkeypatch.setattr(pix_receipt_ocr, "_run_ocr", lambda processed: "Comprovante ilegível\nOperação concluída")

    result = pix_receipt_ocr.extract_pix_receipt(image_bytes())

    assert result["valor"] is None
    assert result["data"] is None
    assert result["hora"] is None
    assert result["destinatario"] is None
    assert result["cpf_cnpj_destinatario"] is None
    assert result["pagador"] is None
    assert result["instituicao"] is None
    assert result["e2e_id"] is None
