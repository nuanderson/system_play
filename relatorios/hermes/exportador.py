"""Exportação diária para o agente de relatórios: um zip com um CSV por view e um manifest.json.

Só lê. Uma view que falha entra no manifest com o erro e as demais seguem. Gerar de novo no mesmo dia
substitui o zip daquele dia (o arquivo é escrito inteiro antes de trocar o antigo).
"""

import csv
import hashlib
import io
import json
import logging
import os
import re
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from .views_sql import CONVENCOES, FUSO, NOMES, SCHEMA, VERSAO_SCHEMA, VIEWS

log = logging.getLogger('uruz.exportacao')

PREFIXO = 'uruz_export_'
PADRAO_ARQUIVO = re.compile(r'^uruz_export_(\d{4}-\d{2}-\d{2})\.zip$')
RETENCAO_DIAS = 60


def agora():
    return datetime.now(ZoneInfo(FUSO))


def nome_do_zip(dia):
    return f'{PREFIXO}{dia.isoformat()}.zip'


def formatar(valor):
    """Valor de uma célula do CSV: vazio para nulo, ponto decimal sem milhar, datas em ISO 8601."""
    if valor is None:
        return ''
    if isinstance(valor, bool):
        return 'true' if valor else 'false'
    if isinstance(valor, Decimal):
        return format(valor, 'f')
    if isinstance(valor, datetime):
        if valor.tzinfo is not None:
            valor = valor.astimezone(ZoneInfo(FUSO))
        return valor.isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, float):
        return repr(valor)
    return str(valor)


def para_csv(colunas, linhas):
    """Bytes do CSV: UTF-8, cabeçalho na primeira linha, separador vírgula, quebra de linha \\n."""
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=',', lineterminator='\n')
    escritor.writerow(colunas)
    for linha in linhas:
        escritor.writerow([formatar(v) for v in linha])
    return saida.getvalue().encode('utf-8')


class FontePostgres:
    """Lê as views do schema relatorios com uma conexão própria, em transação somente leitura."""

    def __init__(self, **conexao):
        import psycopg
        self.conn = psycopg.connect(**conexao, autocommit=True, options='-c default_transaction_read_only=on')

    def consultar(self, view):
        if view not in NOMES:  # nunca monta SQL com texto que não seja uma das views conhecidas
            raise ValueError(f'View desconhecida: {view}')
        with self.conn.cursor() as cursor:
            cursor.execute(f'SELECT * FROM {SCHEMA}.{view}')
            colunas = [c.name for c in cursor.description]
            return colunas, cursor.fetchall()

    def fechar(self):
        self.conn.close()


def montar_arquivos(fonte, views=NOMES):
    """Consulta cada view. Devolve (lista de dicts do manifest, dict nome_do_arquivo -> bytes)."""
    arquivos, conteudos = [], {}
    for view in views:
        item = dict(arquivo=f'{view}.csv', view=view, descricao=VIEWS[view]['descricao'] if view in VIEWS else '',
                    linhas=0, vazia=False, aviso=None, sha256=None, colunas=[], status='ok', erro=None)
        try:
            colunas, linhas = fonte.consultar(view)
            dados = para_csv(colunas, linhas)
            item.update(linhas=len(linhas), colunas=list(colunas), sha256=hashlib.sha256(dados).hexdigest())
            if not linhas:  # view que funciona mas não tem dado hoje: diz isso, para não parecer erro nem esquecimento
                item.update(vazia=True, aviso='A view está correta, mas não retornou nenhuma linha hoje (não há dado para esta consulta).')
            conteudos[item['arquivo']] = dados
        except Exception as exc:  # uma view quebrada não derruba as outras
            item.update(status='erro', erro=f'{type(exc).__name__}: {exc}'[:500])
            log.error('view %s falhou: %s', view, item['erro'])
        arquivos.append(item)
    return arquivos, conteudos


def montar_manifest(arquivos, entrega, momento):
    return dict(
        sistema='edificio-uruz',
        gerado_em=momento.isoformat(timespec='seconds'),
        data=momento.date().isoformat(),
        fuso=FUSO,
        versao_schema=VERSAO_SCHEMA,
        arquivos=arquivos,
        erros=[f"{a['view']}: {a['erro']}" for a in arquivos if a['status'] != 'ok'],
        avisos=[f"{a['view']}: {a['aviso']}" for a in arquivos if a.get('aviso')],
        convencoes=CONVENCOES,
        entrega=entrega,
    )


def gravar_zip(pasta, dia, conteudos, manifest):
    """Escreve o zip do dia (substituindo o do mesmo dia) e devolve o caminho."""
    os.makedirs(pasta, exist_ok=True)
    destino = os.path.join(pasta, nome_do_zip(dia))
    temporario = destino + '.tmp'
    with zipfile.ZipFile(temporario, 'w', zipfile.ZIP_DEFLATED) as z:
        for nome, dados in conteudos.items():
            z.writestr(nome, dados)
        z.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'))
    os.replace(temporario, destino)
    return destino


def limpar_antigos(pasta, hoje, manter_dias=RETENCAO_DIAS):
    """Apaga os zips mais velhos que `manter_dias`, pela data no nome do arquivo (não pela data de modificação)."""
    apagados = []
    if not os.path.isdir(pasta):
        return apagados
    limite = hoje - timedelta(days=manter_dias)
    for nome in sorted(os.listdir(pasta)):
        achou = PADRAO_ARQUIVO.match(nome)
        if achou and date.fromisoformat(achou.group(1)) < limite:
            os.remove(os.path.join(pasta, nome))
            apagados.append(nome)
    return apagados
