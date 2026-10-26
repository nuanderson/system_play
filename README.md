# Sistema do Edifício Uruz (ambiente de teste)

Django 6 + PostgreSQL, rodando em Docker. Identidade visual da Play Participações (logo e paleta em `static/img` e `templates/base.html`).

## Subir com Docker

1. Copie `.env.example` para `.env` e preencha `SECRET_KEY` e `DB_PASSWORD` (o `.env` não é versionado).
2. Suba os containers:

```bash
docker compose up -d --build
```

3. Crie o seu usuário (peça a senha no terminal, não deixe em arquivo):

```bash
docker compose exec web python manage.py createsuperuser
```

4. Abra http://localhost:8765/ (a porta vem de `WEB_PORT` no `.env`). Entre em `/admin/`, abra o seu usuário e defina o perfil como **Síndico**.

Comandos úteis:

```bash
docker compose logs -f web          # logs
docker compose exec web python manage.py test   # testes
docker compose down                 # parar (os dados ficam no volume)
docker compose down -v              # parar e APAGAR o banco e os arquivos
```

As migrações rodam sozinhas ao iniciar o container `web`. Os dados ficam nos volumes `pgdata` (banco) e `media` (arquivos enviados).

## Sem Docker (desenvolvimento rápido)

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

Usa SQLite quando `DB_NAME` não está definido.

## Para o servidor (Hostinger VPS ou outro)

- `DEBUG=0`, `SECRET_KEY` longa e `DB_PASSWORD` forte no `.env`.
- `ALLOWED_HOSTS` com o domínio e `CSRF_TRUSTED_ORIGINS` com o endereço completo (`https://...`).
- Colocar um proxy com HTTPS na frente (Caddy ou Nginx) e definir `HTTPS=1`.
- Backups do volume `pgdata` (por exemplo, `docker compose exec db pg_dump`) antes de usar com dados reais.
- Arquivos enviados (`media`) ainda não são servidos em produção. Isso entra junto com contratos e chamados.

## Perfis e acessos

Seis perfis. A tabela completa de permissões está num único arquivo, `core/acesso.py`; para mudar o acesso de um perfil, basta acrescentar ou tirar o perfil da permissão ali (há testes que cobrem cada tela para cada perfil).

| Perfil | Pode |
|---|---|
| **Síndico** | Tudo, inclusive aprovar o rateio e gerenciar usuários |
| **Administrativo** | Contratos, locatários, condomínio e rateio (importa, calcula e aprova), cobranças, correspondências, relatórios, mural |
| **Equipe e portaria** | Chamados (todos), correspondências (registra, dá baixa, devolve) e mural |
| **Consulta** | Só leitura: relatórios, contratos e cobranças |
| **Responsável da unidade** | Contratos e cobranças da própria unidade, chamados e correspondências da unidade, mural, e cadastra/desativa as pessoas da própria unidade |
| **Membro da unidade** | Chamados e correspondências da unidade, mural |

**Empresas com mais de uma unidade.** No cadastro do locatário, marque todas as unidades que ele ocupa (só aparecem as livres). Cada unidade continua com o próprio contrato, rateio e cobrança. Responsável e membro são ligados à **empresa** e enxergam as unidades dela (contratos, cobranças, chamados e correspondências); quem recebe uma unidade nova passa a vê-la sozinho. Para limitar uma pessoa a uma só unidade, preencha "Limitar a uma unidade". Ao abrir um chamado, quem tem várias unidades escolhe em qual. Não dá para tirar uma unidade que tenha contrato vigente do locatário, nem criar contrato de outro locatário numa unidade já cadastrada para alguém. Cadastros antigos, ligados só a uma unidade, continuam iguais.

Usuários: o síndico cria e gerencia em **Usuários e acessos**; o responsável da unidade gerencia **Pessoas da empresa**. Quem é cadastrado recebe uma **senha provisória**, mostrada uma única vez, e só consegue usar o sistema depois de escolher a própria senha. Desativar um usuário tira o acesso na hora e mantém o histórico. O sistema não deixa desativar a si mesmo, mudar o próprio perfil nem ficar sem síndico ativo. A sessão expira em 12 horas, e o nome no alto da página leva à troca de senha.

## Mural de contatos

Seção Operação. Quadro em estilo post-it com os telefones de prestadores (elétrica, hidráulica, ar-condicionado, elevador, limpeza, segurança, internet, chaveiro, dedetização, jardinagem, emergência e outros). Todos os usuários logados veem, com link para ligar e, quando marcado, para o WhatsApp; só a equipe e o síndico cadastram, editam e removem. Tem filtro por categoria e busca por nome, telefone ou observação.

## Fase 5 (feita): relatórios

Só o síndico acessa (`/relatorios/`). Seis relatórios, todos com impressão e exportação em CSV (separador `;`, vírgula decimal e acentos corretos, para abrir direto no Excel): despesas do mês (por categoria e evolução de 12 meses), rateio por unidade, inadimplência e cobranças (por locatário, por faixa de atraso e resumo mensal), chamados (volume, tempo médio de resolução e custo num período), contratos (vigentes, vencimentos, receita mensal e unidades sem contrato) e correspondências (recebimentos, retiradas e o que está parado). Textos digitados por pessoas são protegidos contra injeção de fórmula no Excel. O cálculo fica em `relatorios/dados.py`.

## Fase 4 (feita): correspondências

A equipe e o síndico registram cartas, encomendas, documentos e notificações por unidade (tipo, destinatário, foto, data e hora e observações; sem remetente nem rastreio, só controle e histórico de recebimento), com "salvar e registrar outra" para lotes. A baixa registra quem retirou e quando, e dá para marcar como devolvida ao remetente (com motivo). Correspondência parada há 7 dias ou mais ganha destaque. Locatários e membros veem só o que chegou para a própria unidade; a foto só é entregue a quem pode ver a correspondência. Ainda não há aviso por e-mail.

## Fase 3 (feita): chamados de manutenção

Locatários e membros abrem chamados da própria unidade (com fotos ou PDF); equipe e síndico abrem também de áreas comuns. Cada chamado tem categoria, prioridade, situação (aberto, em andamento, aguardando, resolvido, cancelado), responsável, fornecedor e custo, comentários e um histórico automático. Notas internas, fornecedor e custo ficam só para a equipe. Fotos e arquivos só são entregues a quem vê o chamado. O solicitante pode cancelar enquanto o chamado está aberto. Ainda não há aviso por e-mail.

## Fase 2 (feita): condomínio, rateio e cobranças

Fluxo mensal: importar o relatório de contas a pagar (.xls/.xlsx do Conta Azul) → marcar o que não entra no rateio (com motivo) → calcular → aprovar → cobranças de condomínio por unidade. Aluguéis são gerados por mês a partir dos contratos vigentes. Cobranças têm baixa de pagamento, edição e cancelamento, com destaque para as atrasadas.

Regras do rateio (`financeiro/calculo.py`): quem tem % de energia/água no contrato paga essa porcentagem da conta, e o restante é dividido por área entre os demais; as demais despesas são divididas por área entre todas as unidades. Tudo em Decimal, fechando no centavo. Unidade sem contrato vigente não gera cobrança (custo da Play). Conferido com o relatório real de setembro: R$ 49.432,74.

## Fase 1 (feita): contratos

Cadastro de contratos por unidade (situação, vigência, aluguel, vencimento, reajuste, % de energia e água para quem paga porcentagem fixa), alerta de vencimento em 90 dias, um contrato vigente por unidade, e anexos (PDF, Word, Excel, JPG, PNG, até 10 MB). Os arquivos só são entregues por uma tela que confere a permissão: o síndico vê tudo, e o responsável da unidade vê só o contrato da própria unidade. Contrato vigente preenche sozinho o locatário da unidade.

## Fase 0 (feita)

Login e logout, perfis (síndico, equipe, responsável da unidade, membro), cadastro de unidades e locatários, carga inicial das 11 unidades com as áreas do projeto (soma do rateio: 1.471,35 m²). Usuários são criados pela administração (`/admin/`) por enquanto.
