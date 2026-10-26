"""Leitura do relatório "Visão Contas a Pagar" (Conta Azul), em .xls ou .xlsx."""

import hashlib
import io
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import openpyxl
import xlrd

from .models import Despesa


class ErroImportacao(Exception):
    pass


COLUNAS = {
    'fornecedor': 'nome do fornecedor',
    'descricao': 'descricao',
    'vencimento': 'data de vencimento',
    'valor': 'valor original da parcela (r$)',
    'juros': 'juros realizado (r$)',
    'multa': 'multa realizado (r$)',
    'categoria': 'categoria 1',
    'centro_custo': 'centro de custo 1',
}
OBRIGATORIAS = ('descricao', 'vencimento', 'valor')


def _normalizar(texto):
    texto = unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode()
    return texto.strip().lower()


def _decimal(valor):
    if valor in (None, ''):
        return Decimal('0.00')
    if isinstance(valor, str):
        valor = valor.replace('R$', '').strip()
        if ',' in valor:
            valor = valor.replace('.', '').replace(',', '.')
    try:
        return Decimal(str(valor)).quantize(Decimal('0.01'))
    except InvalidOperation:
        raise ErroImportacao(f'Valor inválido na planilha: {valor!r}')


def _data(valor, modo_data=None):
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, (int, float)) and modo_data is not None:
        return xlrd.xldate_as_datetime(valor, modo_data).date()
    texto = str(valor or '').strip()
    for formato in ('%d/%m/%Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(texto[:10], formato).date()
        except ValueError:
            continue
    raise ErroImportacao(f'Data inválida na planilha: {valor!r}')


def _linhas_brutas(conteudo):
    """Devolve (lista de linhas, modo_data). Aceita .xlsx (zip) e .xls (OLE)."""
    if conteudo[:2] == b'PK':
        planilha = openpyxl.load_workbook(io.BytesIO(conteudo), data_only=True, read_only=True)
        return [list(linha) for linha in planilha.worksheets[0].iter_rows(values_only=True)], None
    if conteudo[:4] == b'\xd0\xcf\x11\xe0':
        livro = xlrd.open_workbook(file_contents=conteudo)
        folha = livro.sheet_by_index(0)
        return [folha.row_values(i) for i in range(folha.nrows)], livro.datemode
    raise ErroImportacao('Arquivo não reconhecido. Envie o relatório em .xls ou .xlsx.')


def ler_planilha(conteudo):
    """Converte o relatório em uma lista de dicionários, sem tocar no banco."""
    linhas, modo_data = _linhas_brutas(conteudo)
    if not linhas:
        raise ErroImportacao('A planilha está vazia.')
    cabecalho = {_normalizar(nome): i for i, nome in enumerate(linhas[0])}
    posicao = {}
    for campo, nome in COLUNAS.items():
        if nome in cabecalho:
            posicao[campo] = cabecalho[nome]
    faltando = [COLUNAS[c] for c in OBRIGATORIAS if c not in posicao]
    if faltando:
        raise ErroImportacao('Colunas não encontradas na planilha: ' + ', '.join(faltando) + '.')

    def celula(linha, campo):
        i = posicao.get(campo)
        return linha[i] if i is not None and i < len(linha) else None

    resultado = []
    for linha in linhas[1:]:
        if not any(c not in (None, '') for c in linha):
            continue
        valor = celula(linha, 'valor')
        if valor in (None, ''):
            continue
        resultado.append(dict(
            fornecedor=str(celula(linha, 'fornecedor') or '').strip(),
            descricao=str(celula(linha, 'descricao') or '').strip(),
            vencimento=_data(celula(linha, 'vencimento'), modo_data),
            valor=_decimal(valor),
            multa_juros=_decimal(celula(linha, 'juros')) + _decimal(celula(linha, 'multa')),
            categoria=str(celula(linha, 'categoria') or '').strip(),
            centro_custo=str(celula(linha, 'centro_custo') or '').strip(),
        ))
    return resultado


def tipo_da_despesa(categoria):
    nome = _normalizar(categoria)
    if 'energia' in nome:
        return Despesa.Tipo.ENERGIA
    if 'agua' in nome:
        return Despesa.Tipo.AGUA
    return Despesa.Tipo.DEMAIS


def importar(competencia, linhas):
    """
    Grava as despesas que vencem no mês da competência. Reimportar o mesmo relatório não duplica.
    Retorna dict com contagens: novas, repetidas, fora_do_mes.
    """
    novas = repetidas = fora = 0
    vistas = {}
    for linha in linhas:
        venc = linha['vencimento']
        if (venc.year, venc.month) != (competencia.referencia.year, competencia.referencia.month):
            fora += 1
            continue
        base = hashlib.sha1(
            f"{linha['fornecedor']}|{linha['descricao']}|{venc.isoformat()}|{linha['valor']}".encode()
        ).hexdigest()
        vistas[base] = vistas.get(base, 0) + 1
        chave = f'{base}#{vistas[base]}'
        _, criada = Despesa.objects.get_or_create(
            competencia=competencia, chave=chave,
            defaults=dict(
                fornecedor=linha['fornecedor'], descricao=linha['descricao'], categoria=linha['categoria'],
                centro_custo=linha['centro_custo'], vencimento=venc, valor=linha['valor'],
                multa_juros=linha['multa_juros'], tipo=tipo_da_despesa(linha['categoria']),
            ),
        )
        if criada:
            novas += 1
        else:
            repetidas += 1
    return dict(novas=novas, repetidas=repetidas, fora_do_mes=fora)
