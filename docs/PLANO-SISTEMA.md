# Sistema de gestão do prédio — Edifício Uruz (plano)

Rascunho para validação. Sem IA e sem integração com o Conta Azul nesta versão.

## 1. Objetivo

Sistema web para o síndico controlar o prédio: contratos, aluguel e condomínio (com o rateio já definido em `PLANO-DE-RATEIO.md`), chamados de manutenção, correspondências e relatórios. Locatários e outras pessoas autorizadas acessam por login.

## 2. Perfis de acesso

| Perfil | O que pode |
|---|---|
| Síndico | Tudo: cadastros, contratos, rateio, cobranças, chamados, correspondências, relatórios, usuários |
| Equipe do prédio (zeladoria, portaria) | Registrar correspondências, atender e atualizar chamados |
| Locatário (responsável da unidade) | Ver contrato, cobranças e correspondências da sua unidade; abrir e acompanhar chamados; cadastrar pessoas da sua unidade |
| Membro da unidade (funcionário do locatário) | Abrir chamados e ver correspondências da sua unidade |

Cadastro de novas pessoas: por convite do síndico ou do responsável da unidade, com aprovação do síndico. Nunca cadastro aberto ao público.

## 3. Módulos

1. **Cadastros:** unidades, áreas, locatários, usuários.
2. **Contratos:** dados, valor do aluguel, vigência, reajuste, regras específicas (como o restaurante), documentos anexados, alertas de vencimento.
3. **Aluguel e condomínio:** importação do relatório de despesas (.xls), seleção do que entra no rateio, cálculo, aprovação do síndico, cobranças por unidade e controle de pago, em aberto e atrasado.
4. **Chamados de manutenção:** abertura com foto, local e urgência; status; responsável ou fornecedor; custo; comentários e histórico.
5. **Correspondências:** registro de entrada com foto, aviso por e-mail ao locatário, baixa na retirada.
6. **Relatórios:** despesas do mês, rateio por unidade, inadimplência, chamados (abertos, tempo de resolução, custo), contratos a vencer, correspondências pendentes.

## 4. Modelo de dados (resumo)

- **Usuário:** nome, e-mail, perfil, unidade, ativo.
- **Unidade:** nome, andar, tipo, área privativa, área do rateio, ativa. Carga inicial: térreo, 1º andar, salas 01 a 08, restaurante.
- **Locatário:** empresa, CNPJ, contato, e-mail de cobrança.
- **Contrato:** unidade, locatário, início, fim, valor do aluguel, reajuste (índice e mês), regras de condomínio (por exemplo, % de energia e água do restaurante), arquivo, status.
- **Competência:** mês de referência, situação (aberta, rateada, aprovada, fechada).
- **Despesa:** linha do relatório importado (fornecedor, descrição, categoria, valor, vencimento) com marcações: entra no rateio, motivo da exclusão (multa, juros, Play), tipo (energia, água, demais).
- **Rateio e itens do rateio:** valores por unidade e por componente (energia, água, demais), status rascunho ou aprovado, guarda o cálculo do mês.
- **Cobrança:** unidade, competência, tipo (aluguel ou condomínio), valor, vencimento, status, data do pagamento.
- **Chamado:** unidade, solicitante, título, descrição, local, prioridade, status, responsável, fornecedor, custo, anexos, comentários, histórico.
- **Correspondência:** unidade, tipo, remetente, recebida em, foto, retirada por, retirada em.
- **Documento:** anexos genéricos (contratos, notas, fotos).
- **Registro de auditoria:** quem mudou o quê e quando.

## 5. Regras do rateio no sistema

- Energia: restaurante com % definido no contrato; o restante entre clínica, academia e salas, por área.
- Água: igual, com o % da água.
- Demais despesas: entre os quatro, por área.
- 2º andar: dividido entre as 8 salas pela área privativa.
- Multas e juros e despesas marcadas "da Play" ficam fora.
- Cálculo em código determinístico, com conferência: a soma das cobranças tem que bater com o total rateado.
- Nenhuma cobrança é emitida sem aprovação do síndico.

## 6. Tecnologia recomendada

- **Python + Django + PostgreSQL.** Já traz login, permissões, painel administrativo, banco e migrações. Para um sistema com vários módulos e perfis, é o caminho mais rápido e seguro. Você já conhece a stack pelo ComprasNexos.
- **Telas:** HTML renderizado no servidor com HTMX e Tailwind, sem aplicação separada em JavaScript. Menos peças, mais fácil de manter.
- **E-mail:** serviço transacional (Resend, Amazon SES ou Mailgun) para avisos de chamado, correspondência e cobrança.
- **Arquivos:** fotos e documentos no disco do servidor no início, com possibilidade de migrar para armazenamento externo.
- **Ambiente:** primeiro local e de teste (Docker), depois servidor. Hostinger: precisa ser **VPS**, não hospedagem compartilhada. Backups automáticos do banco são obrigatórios.

## 7. Cobrança direta (fase futura, opcional)

O sistema gera as cobranças e você as emite por fora. Para cobrar direto, o desenho seria:
1. O sistema cria o boleto ou Pix pela API de um provedor de pagamentos (como Asaas, Efí ou Mercado Pago) ou pela API do banco.
2. O provedor avisa o sistema quando o pagamento cai (webhook) e o sistema dá baixa sozinho.
3. O dinheiro cai na conta do provedor ou do banco, com taxa por cobrança.

Fica fora da primeira versão. O sistema nasce com uma camada de cobrança separada, para plugar isso depois sem refazer nada.

## 8. Fases

| Fase | Entrega |
|---|---|
| 0 | Projeto, login, perfis, cadastros (unidades com áreas já carregadas), ambiente de teste |
| 1 | Contratos |
| 2 | Condomínio: importar relatório, rateio, aprovação, cobranças manuais, aluguel |
| 3 | Chamados de manutenção |
| 4 | Correspondências |
| 5 | Relatórios |
| 6 | Portal do locatário refinado, servidor, backups e segurança |
| 7 | Cobrança direta (opcional) |

## 9. Pontos em aberto

- Servidor definitivo (Hostinger VPS ou outro).
- Domínio do sistema e dos e-mails.
- Lista dos contratos (será cadastrada do zero).
- Quais relatórios exatamente, além dos listados.
- Prazo de retenção de dados e LGPD (dados de contato dos locatários e funcionários).
