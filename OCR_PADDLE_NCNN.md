# OCR de comprovantes com PaddleOCR-Lite ncnn

O Come Doce utiliza o executável externo do PaddleOCR-Lite ncnn para ler os comprovantes. O Tesseract não faz mais parte do projeto e não existe fallback automático.

## Configuração no Raspberry Pi

Adicione ao arquivo `.env`:

```env
OCR_ENGINE=paddle_ncnn
PADDLEOCR_NCNN_ROOT=/home/adm/PaddleOCR-Lite-Document
PADDLEOCR_NCNN_EXECUTABLE=/home/adm/PaddleOCR-Lite-Document/ocr
PADDLEOCR_NCNN_MODELS_DIR=/home/adm/PaddleOCR-Lite-Document/models
PADDLEOCR_NCNN_THREADS=2
PADDLEOCR_NCNN_TIMEOUT=45
```

O Python executa, sem `shell=True`:

```text
/home/adm/PaddleOCR-Lite-Document/ocr system
/home/adm/PaddleOCR-Lite-Document/models/PP_OCRv5_mobile_det
/home/adm/PaddleOCR-Lite-Document/models/PP_OCRv5_mobile_rec
/home/adm/PaddleOCR-Lite-Document/models/cls-sim-op
arm8 FP32 2 1
/CAMINHO/DO/COMPROVANTE.jpg
/home/adm/PaddleOCR-Lite-Document/models/config.txt
/home/adm/PaddleOCR-Lite-Document/models/PP_OCRv5_vocab.txt
```

O diretório de trabalho (`cwd`) é `/home/adm/PaddleOCR-Lite-Document`. Somente os modelos MOBILE são usados.

## Teste manual

No servidor:

```bash
cd /home/adm/PaddleOCR-Lite-Document
./ocr system models/PP_OCRv5_mobile_det models/PP_OCRv5_mobile_rec models/cls-sim-op arm8 FP32 2 1 /home/adm/comedoce/app/private/payment_receipts/ARQUIVO.jpg models/config.txt models/PP_OCRv5_vocab.txt
```

Depois, no site, abra o pedido e pressione **Analisar novamente**. O sistema atualiza o texto e os campos extraídos sem alterar a imagem ou confirmar o pagamento.

## Segurança e desempenho

- Há no máximo uma execução do PaddleOCR por vez no processo do site.
- O timeout padrão é 45 segundos.
- `vis.jpg` é ignorado pelo Git e não é usado pelo site.
- O texto completo do comprovante não é escrito nos logs.
- Falhas retornam uma mensagem para revisão manual; não existe aprovação automática.
- No Windows, o site abre normalmente, mas a análise só funciona quando um executável ncnn compatível e seus modelos forem configurados.
