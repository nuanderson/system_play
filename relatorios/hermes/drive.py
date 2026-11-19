"""Entrega do zip numa Unidade Compartilhada do Google Drive, com uma service account.

Service account NÃO tem cota de armazenamento no "Meu Drive": o upload ali falha com storageQuotaExceeded. Por isso o
destino é uma Unidade Compartilhada da qual a conta é membro (papel Gerente de conteúdo). Todas as chamadas levam
supportsAllDrives=true. Com esse papel, apagar de vez (files.delete) devolve 404, então a retenção manda para a lixeira
(files.update com trashed=true).
"""

import json
import logging
import re
from datetime import date, timedelta

log = logging.getLogger('uruz.exportacao')

ESCOPO = 'https://www.googleapis.com/auth/drive'
API = 'https://www.googleapis.com/drive/v3/files'
UPLOAD = 'https://www.googleapis.com/upload/drive/v3/files'
PADRAO_ARQUIVO = re.compile(r'^uruz_export_(\d{4}-\d{2}-\d{2})\.zip$')


class DriveErro(Exception):
    pass


def _aspas(texto):
    return texto.replace('\\', '\\\\').replace("'", "\\'")


class Drive:
    api = API
    upload = UPLOAD

    def __init__(self, caminho_json=None, drive_id='', sessao=None):
        if not drive_id:
            raise DriveErro('Falta o id da Unidade Compartilhada (GOOGLE_SHARED_DRIVE_ID).')
        self.caminho_json, self.drive_id, self._sessao = caminho_json, drive_id, sessao

    @property
    def sessao(self):
        if self._sessao is None:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2 import service_account
            credenciais = service_account.Credentials.from_service_account_file(self.caminho_json, scopes=[ESCOPO])
            self._sessao = AuthorizedSession(credenciais)
        return self._sessao

    def _chamar(self, metodo, url, **kwargs):
        resposta = getattr(self.sessao, metodo)(url, timeout=120, **kwargs)
        if resposta.status_code >= 400:
            # o corpo do erro do Google não traz credenciais; corta para não encher o log
            raise DriveErro(f'Drive respondeu {resposta.status_code}: {resposta.text[:300]}')
        return resposta

    def listar(self, consulta):
        """Arquivos da Unidade Compartilhada que casam com a consulta (files.list, com paginação)."""
        achados, pagina = [], None
        while True:
            parametros = dict(
                q=consulta, driveId=self.drive_id, corpora='drive', includeItemsFromAllDrives='true',
                supportsAllDrives='true', fields='nextPageToken,files(id,name)', pageSize=1000)
            if pagina:
                parametros['pageToken'] = pagina
            corpo = self._chamar('get', self.api, params=parametros).json()
            achados += corpo.get('files', [])
            pagina = corpo.get('nextPageToken')
            if not pagina:
                return achados

    def enviar(self, caminho_zip, nome):
        """
        Sobe o zip para a raiz da Unidade Compartilhada (files.create com parents=[drive_id]).
        Se já existe um arquivo com esse nome (reexecução no mesmo dia), troca o conteúdo (files.update).
        """
        existentes = self.listar(f"name = '{_aspas(nome)}' and trashed = false")
        with open(caminho_zip, 'rb') as f:
            conteudo = f.read()
        metadados = {'name': nome} if existentes else {'name': nome, 'parents': [self.drive_id]}
        limite = 'uruz-' + 'x' * 24
        corpo = (
            f'--{limite}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'.encode()
            + json.dumps(metadados).encode('utf-8')
            + f'\r\n--{limite}\r\nContent-Type: application/zip\r\n\r\n'.encode() + conteudo
            + f'\r\n--{limite}--'.encode()
        )
        cabecalho = {'Content-Type': f'multipart/related; boundary={limite}'}
        parametros = dict(uploadType='multipart', supportsAllDrives='true', fields='id,name')
        if existentes:
            resposta = self._chamar('patch', f"{self.upload}/{existentes[0]['id']}", params=parametros, data=corpo, headers=cabecalho)
            acao = 'substituído'
        else:
            resposta = self._chamar('post', self.upload, params=parametros, data=corpo, headers=cabecalho)
            acao = 'criado'
        return dict(id=resposta.json().get('id'), nome=nome, acao=acao, bytes=len(conteudo))

    def limpar(self, hoje, manter_dias=60):
        """
        Manda para a lixeira os zips com mais de `manter_dias` (pela data no nome), com files.update trashed=true.
        Só mexe em nomes uruz_export_AAAA-MM-DD.zip.
        """
        limite = hoje - timedelta(days=manter_dias)
        removidos = []
        for arq in self.listar("name contains 'uruz_export_' and trashed = false"):
            achou = PADRAO_ARQUIVO.match(arq['name'])
            if achou and date.fromisoformat(achou.group(1)) < limite:
                self._chamar('patch', f"{self.api}/{arq['id']}", params=dict(supportsAllDrives='true'), json={'trashed': True})
                removidos.append(arq['name'])
        return removidos
