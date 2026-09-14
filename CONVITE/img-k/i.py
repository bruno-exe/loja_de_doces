import subprocess
import os

arquivo_entrada = r"C:\Users\bruno\Desktop\CONVITE\img-k\video.mp4"

nome, extensao = os.path.splitext(arquivo_entrada)
arquivo_saida = nome + "_compactado.mp4"

comando = [
    "ffmpeg",

    # Pula os 3 primeiros segundos
    "-ss", "4",

    "-i", arquivo_entrada,

    # Vídeo
    "-c:v", "libx264",
    "-preset", "medium",
    "-crf", "30",

    # Áudio
    "-c:a", "aac",
    "-b:a", "96k",

    # Compatibilidade com site/navegador
    "-movflags", "+faststart",

    # Sobrescreve se já existir
    "-y",

    arquivo_saida
]

resultado = subprocess.run(comando)

if resultado.returncode == 0:
    print("\nConcluído!")
    print("Arquivo salvo em:")
    print(arquivo_saida)
else:
    print("\nOcorreu um erro durante a conversão.")