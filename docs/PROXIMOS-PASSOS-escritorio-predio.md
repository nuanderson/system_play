# Escritório virtual do prédio: resumo para continuar no Claude Code

## Objetivo
Sistema para o síndico controlar o prédio comercial (unidades alugadas), com agentes de IA para financeiro, manutenção, contratos, atendimento, consumo, documentos e relatórios. Os agentes preparam e sugerem. Nada é cobrado, enviado ou pago sem aprovação humana.

## Situação atual
- Cobranças de aluguel e condomínio são emitidas no Conta Azul.
- O rateio do condomínio é feito hoje numa planilha, a partir do relatório de despesas do prédio.
- O Conta Azul ainda não envia boleto, só a cobrança. Opções: ativar boleto Pix no Conta Azul (confirmar qual banco recebe, a Caixa, a Safra ou a BTG), boleto emitido pelo internet banking da Caixa ou da Safra, ou Pix com QR Code por unidade.
- Contas bancárias: Caixa e Safra.
- Não há número oficial de WhatsApp. A empresa não tem Facebook. Por isso a comunicação com locatários será por e-mail e portal dentro do sistema.
- Não há domínio do prédio confirmado.

## Decisões tomadas
- Stack: Django + PostgreSQL, como no projeto ComprasNexos. Cada agente é um módulo Python que chama a API da Anthropic.
- Modelos: Haiku 5.5 para tarefas simples e conferências. Sonnet 5.5 para rateio e textos que exigem cuidado.
- Custo estimado da API: US$ 2 a 5 por mês para o porte do prédio. Preços em platform.claude.com/docs/en/about-claude/pricing.
- E-mail por serviço transacional (Amazon SES, Resend ou Mailgun), com SPF, DKIM e DMARC no domínio próprio.
- Painel principal em pixel art, como visão ao vivo do estado dos agentes. Minigame só como camada opcional, depois.
- Ordem de construção: 1) banco do prédio (unidades, locatários, contratos); 2) agente Financeiro com rateio e cobrança; 3) painel com fila de aprovação; 4) Manutenção e Contratos; 5) comunicação por e-mail e portal.

## Arquivos
- Esboço do painel: `escritorio-predio.html` nesta pasta (scratchpad). Versão publicada em claude.ai/artifact/6cGgXcoS2UqcfrhTMCLmgU.
- Arquitetura: diagrama feito no chat, em três camadas (painel do síndico, orquestrador, agentes, dados e sistemas).

## Perguntas em aberto
1. Como o rateio é feito hoje: igual entre as unidades, por área, ou parte fixa e parte por consumo?
2. O boleto Pix do Conta Azul recebe na Caixa, na Safra ou na BTG?
3. A Caixa ou a Safra oferecem API de cobrança e Pix cobrança para a empresa?
4. Qual domínio o prédio vai usar nos e-mails?
5. Quantas unidades, e quantos locatários recebem cobrança por mês?

## Próximo passo sugerido
Montar o modelo de dados (unidades, locatários, contratos, cobranças, despesas, rateio) e o primeiro agente Financeiro com rateio em modo de simulação, sem envio real.
