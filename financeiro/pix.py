"""Pix "copia e cola" e QR Code estático (BR Code, padrão do Banco Central), sem banco e sem API.

O QR leva a chave Pix da conta, o valor e uma referência da cobrança. Quem paga não consegue
mudar o valor no app. Como é estático, o banco NÃO avisa o sistema do pagamento: a baixa é manual.
"""

import io
import re
import unicodedata
from decimal import Decimal

import segno
from django.conf import settings


def configurado():
    return bool(settings.PIX_CHAVE and settings.PIX_NOME and settings.PIX_CIDADE)


def _campo(codigo, valor):
    return f'{codigo}{len(valor):02d}{valor}'


def _ascii(texto, limite):
    """Nome e cidade do recebedor: sem acento, maiúsculas, no tamanho que o padrão aceita."""
    limpo = unicodedata.normalize('NFKD', texto).encode('ascii', 'ignore').decode().upper()
    return re.sub(r'[^A-Z0-9 ]', '', limpo).strip()[:limite]


def normalizar_chave(chave):
    """CNPJ e CPF entram só com números. E-mail, telefone (+55...) e chave aleatória ficam como estão."""
    chave = chave.strip()
    return re.sub(r'\D', '', chave) if re.fullmatch(r'[\d./-]+', chave) else chave


def referencia(cobranca):
    """Até 25 letras e números. Aparece no extrato de alguns bancos e ajuda a achar o pagamento."""
    return f'URUZ{cobranca.pk:08d}'


def crc16(texto):
    """CRC16/CCITT-FALSE exigido pelo BR Code."""
    crc = 0xFFFF
    for byte in texto.encode('ascii'):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return f'{crc:04X}'


def payload(chave, nome, cidade, valor, ref):
    conta = _campo('00', 'br.gov.bcb.pix') + _campo('01', normalizar_chave(chave))
    corpo = (
        _campo('00', '01') + _campo('01', '11') + _campo('26', conta) + _campo('52', '0000') + _campo('53', '986')
        + _campo('54', f'{Decimal(valor):.2f}') + _campo('58', 'BR') + _campo('59', _ascii(nome, 25))
        + _campo('60', _ascii(cidade, 15)) + _campo('62', _campo('05', re.sub(r'[^A-Za-z0-9]', '', ref)[:25] or '***'))
        + '6304'
    )
    return corpo + crc16(corpo)


def codigo_da_cobranca(cobranca):
    """O "copia e cola" do valor desta cobrança, ou None se o Pix não foi configurado."""
    if not configurado():
        return None
    return payload(settings.PIX_CHAVE, settings.PIX_NOME, settings.PIX_CIDADE, cobranca.valor, referencia(cobranca))


def qr_svg(codigo):
    """SVG para colocar na página. Sem tamanho fixo: ele se ajusta à caixa em que for colocado."""
    return segno.make(codigo, error='m').svg_inline(border=2, dark='#05385c', light='#ffffff', omitsize=True)


def qr_png(codigo, escala=8):
    """Bytes do PNG, para o e-mail e para o PDF."""
    saida = io.BytesIO()
    segno.make(codigo, error='m').save(saida, kind='png', scale=escala, border=2)
    return saida.getvalue()
