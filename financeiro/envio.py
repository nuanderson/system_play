"""Envio da cobrança por e-mail, com o boleto em anexo quando houver."""

from django.conf import settings
from django.core.mail import EmailMessage

from . import fatura
from .models import EnvioCobranca


class EmailNaoConfigurado(Exception):
    pass


def email_configurado():
    return bool(settings.EMAIL_HOST)


def _real(valor):
    return f'R$ {valor:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def montar_texto(cobranca, mensagem=''):
    linhas = [f'Olá, {cobranca.locatario}.', '']
    if mensagem:
        linhas += [mensagem.strip(), '']
    linhas += [
        f'Segue a cobrança de {cobranca.get_tipo_display().lower()} referente a {cobranca.referencia:%m/%Y}, '
        'do Edifício Uruz.', '',
        f'Valor: {_real(cobranca.valor)}',
        f'Vencimento: {cobranca.vencimento:%d/%m/%Y}',
    ]
    itens = list(cobranca.itens.select_related('unidade'))
    if itens:
        linhas += ['', 'Composição:']
        for item in itens:
            sinal = '- ' if item.valor < 0 else ''
            linhas.append(f'  {item.unidade.nome} · {item.descricao}: {sinal}{_real(abs(item.valor))}')
    linhas += ['', 'Em caso de dúvida, responda este e-mail.', '', 'Play Participações · Edifício Uruz']
    return '\n'.join(linhas)


def enviar_cobranca(cobranca, para, usuario, mensagem='', anexar_boleto=True, anexar_fatura=True):
    """Envia e registra o resultado. Falha do servidor de e-mail vira registro com erro, não exceção."""
    if not email_configurado():
        raise EmailNaoConfigurado('O e-mail de envio ainda não foi configurado no servidor.')
    com_boleto = bool(anexar_boleto and cobranca.boleto)
    com_fatura = bool(anexar_fatura and cobranca.fatura_disponivel)
    email = EmailMessage(
        subject=f'Cobrança de {cobranca.get_tipo_display().lower()} {cobranca.referencia:%m/%Y} · Edifício Uruz',
        body=montar_texto(cobranca, mensagem), from_email=settings.DEFAULT_FROM_EMAIL, to=[para],
        reply_to=[settings.EMAIL_RESPONDER_PARA] if settings.EMAIL_RESPONDER_PARA else None,
    )
    if com_boleto:
        try:
            with cobranca.boleto.open('rb') as arquivo:
                email.attach(cobranca.boleto_nome or 'boleto.pdf', arquivo.read())
        except FileNotFoundError:
            com_boleto = False
    if com_fatura:
        email.attach(f'fatura-condominio-{cobranca.referencia:%Y-%m}.pdf', fatura.gerar_pdf(cobranca), 'application/pdf')
    try:
        email.send(fail_silently=False)
        erro = ''
    except Exception as exc:  # servidor recusou, senha errada, sem rede...
        erro = f'{type(exc).__name__}: {exc}'[:300]
    return EnvioCobranca.objects.create(
        cobranca=cobranca, para=para, com_boleto=com_boleto, com_fatura=com_fatura, enviado_por=usuario,
        ok=not erro, erro=erro)
