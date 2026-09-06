from datetime import date
from decimal import Decimal
from io import BytesIO

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


def empty_ocr_data() -> dict:
    return {
        key: []
        for key in (
            "text", "conf", "left", "top", "width", "height",
            "block_num", "par_num", "line_num",
        )
    }


def mock_ocr(monkeypatch, text: str, *, passes=None, data=None) -> None:
    results = passes if passes is not None else [("general", text)]
    monkeypatch.setattr(
        pix_receipt_ocr,
        "run_ocr_passes",
        lambda variants: (results, data or empty_ocr_data()),
    )


def parse_text(text: str, *, passes=None, data=None) -> dict:
    return pix_receipt_ocr.parse_pix_receipt(
        passes or [("general", text)], data or empty_ocr_data(), 1000
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
    comparison = pix_receipt_ocr.compare_pix_receipt(
        extracted,
        expected_value=Decimal("25.00"),
        expected_date=date(2026, 9, 2),
        expected_recipient="Joao da Silva",
        expected_pix_key="joao.silva@criar",
    )
    assert comparison == {
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
        "R$ 5": "5.00", "R$5": "5.00", "R$ 5,00": "5.00",
        "R$ 20,50": "20.50", "R$ 20.50": "20.50",
    }
    for text, expected in cases.items():
        assert parse_text(text)["valor"] == Decimal(expected)


def test_accepts_standalone_decimal_value_with_lower_confidence() -> None:
    result = parse_text("5,00")
    assert result["valor"] == Decimal("5.00")
    assert 0.55 <= result["confidence"]["valor"] < 0.72


def test_associates_separate_currency_and_number_boxes() -> None:
    data = {
        "text": ["R$", "5"], "conf": ["94", "96"],
        "left": [100, 145], "top": [260, 258],
        "width": [32, 22], "height": [35, 39],
        "block_num": [1, 1], "par_num": [1, 1], "line_num": [1, 1],
    }
    result = parse_text("Comprovante Pix", data=data)
    assert result["valor"] == Decimal("5.00")
    assert result["confidence"]["valor"] >= 0.55


def test_does_not_use_cpf_date_time_or_transaction_id_as_value() -> None:
    text = """Data 04/09/2026 16:54:16
CPF 123.456.789-01
ID da transacao ABC12345678901234567890"""
    assert parse_text(text)["valor"] is None


def test_selects_currency_value_among_unrelated_numbers() -> None:
    text = """CPF 123.456.789-01
Data 04/09/2026 16:54
R$ 20,50
ID E1234567820260902ABCDEF1234567890"""
    assert parse_text(text)["valor"] == Decimal("20.50")


def test_extracts_written_date_time_and_both_transaction_ids() -> None:
    text = """Comprovante Pix
4/setembro/2026 as 16:54:16
Numero da transacao
MP-123456789
EndToEndId
E1234567820260902ABCDEF1234567890"""
    result = parse_text(text)
    assert result["data"] == "2026-09-04"
    assert result["hora"] == "16:54:16"
    assert result["transaction_id"] == "MP-123456789"
    assert result["pix_id"] == "E1234567820260902ABCDEF1234567890"


def test_combines_passes_when_general_ocr_misses_value() -> None:
    passes = [
        ("general", "Comprovante Pix\n4/9/2026 16:54"),
        ("sparse", "Origem e destino"),
        ("top_value", "R$ 5"),
    ]
    result = parse_text("", passes=passes)
    assert result["valor"] == Decimal("5.00")
    assert result["data"] == "2026-09-04"


def test_rejects_separate_value_tokens_with_low_ocr_confidence() -> None:
    data = {
        "text": ["R$", "5"], "conf": ["0", "0"],
        "left": [100, 145], "top": [250, 250],
        "width": [32, 22], "height": [35, 35],
        "block_num": [1, 1], "par_num": [1, 1], "line_num": [1, 1],
    }
    result = parse_text("Comprovante Pix", data=data)
    assert result["valor"] is None
    assert result["confidence"]["valor"] == 0.0
