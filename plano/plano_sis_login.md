# Come Doce — plano do sistema de cadastro e login

## 1. Objetivo desta primeira etapa

Criar para o **Come Doce** uma base própria de autenticação, inspirada no sistema já funcional de `C:\NP\namoropago`, contendo somente:

- página inicial simples;
- cadastro de conta;
- escolha obrigatória entre **Comprador** e **Vendedor**;
- login com e-mail e senha;
- sessão segura para permanecer conectado;
- logout;
- encaminhamento para um perfil inicial diferente conforme o tipo da conta.

Esta etapa não implementará ainda catálogo de produtos, compras, dívidas ou administração. Esses recursos serão definidos e construídos separadamente, com teste e aprovação entre as etapas.

## 2. Forma correta e mais simples de reaproveitar o projeto existente

A opção mais segura é **copiar apenas o padrão de autenticação e adaptar o código**, em vez de duplicar a pasta inteira do Namoro Pago.

Isso permite manter o que já funciona — hash de senha, sessão, CSRF, validação de e-mail e acesso ao banco — sem levar para o Come Doce recursos que pertencem a uma rede social.

O novo projeto deve ter:

- código próprio dentro de `C:\CD`;
- banco de dados novo e vazio;
- nome de cookie próprio, como `comedoce_session`;
- segredo de sessão próprio;
- telas e identidade visual com o nome **Come Doce**;
- modelos de dados pequenos, próprios para comprador e vendedor.

Não devem ser copiados o banco de dados, o ambiente virtual (`.venv`), arquivos `.env`, segredos, uploads ou backups do projeto antigo.

## 3. O que pode ser aproveitado do Namoro Pago

Após analisar `C:\NP\namoropago`, estes componentes podem servir como referência:

| Origem | Uso no Come Doce |
| --- | --- |
| `app/security.py` | Hash e verificação de senha com Argon2; criação e validação de token CSRF |
| `app/database.py` | Configuração básica do SQLAlchemy e das sessões do banco |
| Parte de `app/config.py` | Configuração por variáveis de ambiente, segredo e segurança do cookie |
| Rotas `/cadastro`, `/login` e `/logout` de `app/main.py` | Fluxo de autenticação, adaptado ao novo modelo |
| `app/templates/login.html` | Estrutura do formulário de login, com textos e aparência novos |
| `app/templates/cadastro.html` | Estrutura do formulário, removendo campos sociais e adicionando o tipo de conta |
| Dependências essenciais de `requirements.txt` | FastAPI, Uvicorn, Jinja2, SQLAlchemy, python-multipart, Argon2, itsdangerous, email-validator, pytest e httpx |

O arquivo `app/main.py` antigo possui muitas funções sem relação com autenticação. Portanto, ele não deve ser copiado inteiro; as rotas necessárias devem ser extraídas para arquivos pequenos no novo projeto.

## 4. O que não será copiado

- sexo, interesses amorosos, preferências de relacionamento e busca de pessoas;
- perfil público de rede social;
- mensagens, convites, confiança, denúncias e bloqueios;
- planos, moedas, carteira e pagamentos do Namoro Pago;
- avatar e processamento de fotos;
- configurações de visibilidade por gênero;
- campos como `butterfly_plan`, `message_plan`, `trust_level` e semelhantes;
- tabelas e dados existentes do Namoro Pago;
- arquivos de backup e integrações externas.

## 5. Estrutura inicial recomendada

```text
C:\CD\
├── comedoce.txt
├── plano\
│   └── plano_sis_login.md
├── app\
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── models.py
│   ├── security.py
│   ├── routes\
│   │   └── auth.py
│   ├── templates\
│   │   ├── base.html
│   │   ├── home.html
│   │   ├── cadastro.html
│   │   ├── login.html
│   │   ├── perfil_comprador.html
│   │   └── perfil_vendedor.html
│   └── static\
│       └── style.css
├── tests\
│   └── test_auth.py
├── requirements.txt
├── .env.example
└── iniciar_site.cmd
```

A separação de `routes/auth.py` desde o começo evita que `main.py` fique muito grande conforme o site crescer.

## 6. Modelo de dados inicial

### Tabela `usuarios`

Campos mínimos:

- `id` — identificador interno;
- `nome` — nome da pessoa ou responsável;
- `email` — único e normalizado em letras minúsculas;
- `senha_hash` — nunca guardar a senha original;
- `tipo_conta` — somente `comprador` ou `vendedor`;
- `ativo` — permite bloquear uma conta futuramente sem apagá-la;
- `criado_em` — data e hora de criação.

O tipo da conta deve ser validado tanto no formulário quanto no servidor. Não se deve confiar apenas no valor enviado pelo navegador.

### Perfis separados

Recomendação para facilitar o crescimento do sistema:

- `perfil_comprador`: relação de um para um com `usuarios`;
- `perfil_vendedor`: relação de um para um com `usuarios`.

No primeiro cadastro, o sistema cria o usuário e somente o perfil correspondente ao tipo escolhido. Assim, campos de produtos não ficam misturados com dados de compras e débitos.

Nesta primeira entrega, os dois perfis podem ser telas mínimas exibindo nome, e-mail, tipo da conta e a indicação de que os demais recursos serão adicionados depois.

## 7. Fluxo esperado

1. A pessoa abre a página inicial.
2. Escolhe criar uma conta.
3. Informa nome, e-mail, senha, confirmação da senha e seleciona **Comprador** ou **Vendedor**.
4. O servidor valida os dados, verifica se o e-mail já existe e transforma a senha em hash Argon2.
5. A conta e o perfil correspondente são criados no banco novo.
6. A sessão é iniciada com segurança.
7. Compradores são encaminhados ao perfil de comprador; vendedores, ao perfil de vendedor.
8. Em acessos futuros, a pessoa entra com e-mail e senha.
9. Ao sair, a sessão é apagada e o cookie é removido.

## 8. Segurança que deve ser mantida

- hash de senha com **Argon2**, já usado no projeto de origem;
- senha de 8 a 128 caracteres;
- validação de e-mail no servidor;
- e-mail único no banco e normalizado;
- mensagem genérica no login: “E-mail ou senha incorretos”;
- token CSRF nos formulários de cadastro, login e logout;
- limpeza da sessão antes de iniciar uma nova sessão;
- cookie `HttpOnly` e `SameSite=Lax`;
- `Secure=true` quando o site estiver publicado com HTTPS;
- segredo da sessão fora do código, configurado por variável de ambiente;
- logout por `POST`, não por link `GET`;
- consulta apenas de usuários ativos;
- nenhuma senha ou segredo escrito em logs.

## 9. Etapas de implementação e teste

O trabalho deve parar ao final de cada etapa para permitir teste e ajustes antes de continuar.

**Andamento atual:** Etapa 1 implementada em 31/08/2026 e aguardando avaliação visual do responsável pelo projeto.

### Etapa 1 — Fundação do Come Doce `[implementada]`

- criar a estrutura mínima do projeto;
- configurar ambiente virtual e dependências;
- configurar FastAPI, templates, arquivos estáticos e banco próprio;
- criar página inicial com o nome Come Doce;
- iniciar o servidor por `iniciar_site.cmd`.

**Teste:** abrir a página inicial no computador e no celular ou no modo responsivo do navegador.

### Etapa 2 — Banco e tipos de conta

- criar a tabela `usuarios`;
- criar os perfis mínimos de comprador e vendedor;
- restringir `tipo_conta` a `comprador` ou `vendedor`;
- criar banco novo, sem reutilizar dados do Namoro Pago.

**Teste:** iniciar com banco vazio e confirmar a criação correta das tabelas.

### Etapa 3 — Cadastro

- criar tela de cadastro;
- adicionar escolha clara entre Comprador e Vendedor;
- validar nome, e-mail, senha, confirmação e tipo de conta;
- criar usuário e perfil correspondente;
- mostrar erros de forma simples, sem apagar os campos seguros já preenchidos.

**Teste:** cadastrar um comprador, um vendedor, tentar e-mail duplicado, senha curta, confirmação diferente e tipo inválido.

### Etapa 4 — Login e sessão

- criar tela de login;
- verificar senha com Argon2;
- manter sessão por cookie seguro;
- impedir que pessoa desconectada abra uma página protegida;
- redirecionar a pessoa conectada conforme `tipo_conta`.

**Teste:** entrar com cada tipo de conta, atualizar a página, fechar e reabrir o navegador e testar senha incorreta.

### Etapa 5 — Logout

- criar botão Sair;
- validar CSRF;
- limpar sessão e cookie;
- voltar à página inicial.

**Teste:** sair e tentar abrir novamente uma página protegida.

### Etapa 6 — Perfis mínimos diferentes

- perfil do comprador com espaço futuro para quantidade comprada e valor devido;
- perfil do vendedor com espaço futuro para produtos;
- nesta etapa, não implementar ainda cálculos nem lista de produtos.

**Teste:** garantir que comprador não recebe a página de vendedor e vice-versa.

### Etapa 7 — Testes automatizados e revisão

- testar cadastro, login, sessão, proteção de rotas, tipos de conta e logout;
- revisar textos para que não exista nenhuma menção a namoro ou rede social;
- revisar layout responsivo;
- documentar como instalar e iniciar.

## 10. Critérios para considerar o sistema de login concluído

- o nome exibido é Come Doce;
- existem apenas os tipos Comprador e Vendedor;
- ambos conseguem se cadastrar e entrar;
- a senha não é guardada em texto puro;
- a sessão permanece ativa e pode ser encerrada;
- rotas privadas exigem login;
- cada tipo chega ao perfil correto;
- nenhuma função social do projeto antigo foi copiada;
- todos os testes manuais da etapa foram aprovados pelo responsável pelo projeto.

## 11. Decisões deixadas para etapas futuras

- quais dados adicionais serão pedidos ao vendedor;
- formato da lista de produtos, imagens, preço, estoque e categorias;
- como registrar compras do comprador;
- significado e origem do “valor que deve”;
- pagamentos, cobranças e permissões administrativas;
- possibilidade ou não de mudar o tipo da conta depois do cadastro.

Essas decisões não são necessárias para construir a autenticação e devem aguardar a definição de cada módulo.

## 12. Próximo passo recomendado

Abrir e avaliar a página inicial em diferentes tamanhos de tela. Depois da aprovação visual, executar somente a **Etapa 2 — Banco e tipos de conta**; o cadastro será implementado na etapa seguinte.

Essa é a forma mais fácil de encontrar problemas cedo: uma entrega pequena, testável e aprovada antes da próxima.
