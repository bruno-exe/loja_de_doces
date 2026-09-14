# Convite de aniversario

Site simples em FastAPI para convite de aniversario mobile.

## Rodar no computador

```bash
py -3.14 -m pip install -r requirements.txt
py -3.14 -m uvicorn main:app --host 127.0.0.1 --port 8002
```

Depois acesse:

```text
http://localhost:8002
```

## Publicar

O convite está integrado ao Come Doce. Inicie normalmente o Come Doce usando
`iniciar_site.cmd` na pasta raiz e acesse http://127.0.0.1:8000/convite/.

No servidor, publique também a pasta `CONVITE` completa, junto com a atualização
de `app/main.py`, e reinicie o serviço do Come Doce. O endereço será
https://comedoce.com.br/convite/. Não precisa instalar PHP, criar outro processo
ou alterar o banco de dados.

A confirmação continua sendo feita pelo WhatsApp. Apenas a página e os arquivos
de mídia/estilo permitidos ficam públicos; scripts Python e a pasta `data` não
são expostos. Preserve o nome maiúsculo `CONVITE` no Linux.
