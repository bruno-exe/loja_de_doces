import subprocess
from types import SimpleNamespace

import pytest

from app.services import paddleocr_ncnn


def config(tmp_path, threads=2):
    root = tmp_path / "paddle"
    models = root / "models"
    models.mkdir(parents=True)
    executable = root / "ocr"
    executable.touch()
    for name in ("PP_OCRv5_mobile_det.bin", "PP_OCRv5_mobile_det.param", "PP_OCRv5_mobile_rec.bin", "PP_OCRv5_mobile_rec.param", "cls-sim-op.bin", "cls-sim-op.param", "config.txt", "PP_OCRv5_vocab.txt"):
        (models / name).touch()
    return paddleocr_ncnn.PaddleOcrNcnnConfig(root, executable, models, threads, 45)


def test_parses_output_and_ignores_visualization_message():
    lines = paddleocr_ncnn.parse_output("11\tR$ 5\t0.869408\n20  176328143979  0.999939\nThe detection visualized image saved in ./vis.jpg")
    assert [(line.text, line.confidence) for line in lines] == [("R$ 5", 0.869408), ("176328143979", 0.999939)]


def test_runs_expected_command_without_shell(monkeypatch, tmp_path):
    settings = config(tmp_path, threads=3)
    image = tmp_path / "receipt.jpg"
    image.touch()
    captured = {}
    def fake_run(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return SimpleNamespace(returncode=0, stdout="11 R$ 5 0.869408\n", stderr="")
    monkeypatch.setattr(paddleocr_ncnn.subprocess, "run", fake_run)
    result = paddleocr_ncnn.run_paddleocr(image, settings)
    assert result.raw_text == "R$ 5"
    assert captured["command"][7] == "3"
    assert captured["kwargs"]["cwd"] == settings.root
    assert "shell" not in captured["kwargs"]
    assert captured["kwargs"]["timeout"] == 45


@pytest.mark.parametrize("problem", ["timeout", "process", "empty"])
def test_reports_controlled_execution_errors(monkeypatch, tmp_path, problem):
    settings = config(tmp_path)
    image = tmp_path / "receipt.jpg"
    image.touch()
    if problem == "timeout":
        action = lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("ocr", 45))
    elif problem == "process":
        action = lambda *args, **kwargs: SimpleNamespace(returncode=2, stdout="", stderr="error")
    else:
        action = lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(paddleocr_ncnn.subprocess, "run", action)
    with pytest.raises(paddleocr_ncnn.PaddleOcrNcnnError):
        paddleocr_ncnn.run_paddleocr(image, settings)


def test_reports_missing_executable(tmp_path):
    settings = config(tmp_path)
    settings.executable.unlink()
    image = tmp_path / "receipt.jpg"
    image.touch()
    with pytest.raises(paddleocr_ncnn.PaddleOcrNcnnError, match="executável"):
        paddleocr_ncnn.run_paddleocr(image, settings)
