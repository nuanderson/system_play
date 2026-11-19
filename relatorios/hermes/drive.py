"""Entrega do zip numa pasta do Google Drive, com uma service account.

A service account só enxerga o que for compartilhado com o e-mail dela. Com a pasta "Edifício Uruz - Exportações"
compartilhada como Editor, ela só alcança essa pasta. O escopo "drive" é o mínimo que o Google oferece para
mexer numa pasta que a própria conta não criou.
"""

import json
import logging
import re
from datetime import date, timedelta

log = logging.getLogger('uruz.exportacao')

ESCOPO = 'https://www.googleapis.com/auth/drive'
API = 'https://www.googleapis.com/drive/v3/files'
UPLOAD = 'https://www.googleapis.com/upload/drive/v3/files'
PASTA_PADRAO = 'Edifício Uruz - Exportações'
PADRAO_ARQUIVO = re.compile(r'^uruz_export_(\d{4}-\d{2}-\d{2})\.zip$')


class DriveErro(Exception):
    pass


def _aspas(texto):
    return texto.replace('\\', '\\\\').replace("'", "\\'")


class Drive:
    def __init__(self, caminho_json=None, pasta_id='', nome_pasta=PASTA_PADRAO, sessao=None):
        self.caminho_json, self.pasta_id, self.nome_pasta = caminho_json, pasta_id, nome_pasta
        self._sessao = sessao

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

    def _listar(self, consulta):
        resposta = self._chamar('get', API, params=dict(
            q=consulta, fields='files(id,name)', pageSize=1000, supportsAllDrives='true',
            includeItemsFromAllDrives='true'))
        return resposta.json().get('files', [])

    def pasta(self):
        """Id da pasta de destino: o configurado, ou a pasta com o nome padrão que foi compartilhada com a conta."""
        if self.pasta_id:
            return self.pasta_id
        achadas = self._listar(
            f"name = '{_aspas(self.nome_pasta)}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false")
        if not achadas:
            raise DriveErro(f'Não achei a pasta "{self.nome_pasta}". Ela foi compartilhada com o e-mail da service account?')
        if len(achadas) > 1:
            raise DriveErro(f'Há {len(achadas)} pastas chamadas "{self.nome_pasta}" visíveis para a conta. '
                            'Defina GOOGLE_DRIVE_FOLDER_ID para escolher uma.')
        self.pasta_id = achadas[0]['id']
        return self.pasta_id

    def enviar(self, caminho_zip, nome):
        """Sobe o zip. Se já existe um arquivo com esse nome na pasta (reexecução no mesmo dia), substitui o conteúdo."""
        pasta = self.pasta()
        existentes = self._listar(f"'{pasta}' in parents and name = '{_aspas(nome)}' and trashed = false")
        with open(caminho_zip, 'rb') as f:
            conteudo = f.read()
        metadados = {'name': nome} if existentes else {'name': nome, 'parents': [pasta]}
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
            resposta = self._chamar('patch', f"{UPLOAD}/{existentes[0]['id']}", params=parametros, data=corpo, headers=cabecalho)
            acao = 'substituído'
        else:
            resposta = self._chamar('post', UPLOAD, params=parametros, data=corpo, headers=cabecalho)
            acao = 'criado'
        return dict(id=resposta.json().get('id'), nome=nome, acao=acao, bytes=len(conteudo))

    def limpar(self, hoje, manter_dias=60):
        """Apaga da pasta os zips com mais de `manter_dias` (pela data no nome). Só mexe em nomes uruz_export_*.zip."""
        pasta = self.pasta()
        limite = hoje - timedelta(days=manter_dias)
        apagados = []
        for arq in self._listar(f"'{pasta}' in parents and name contains 'uruz_export_' and trashed = false"):
            achou = PADRAO_ARQUIVO.match(arq['name'])
            if achou and date.fromisoformat(achou.group(1)) < limite:
                self._chamar('delete', f"{API}/{arq['id']}", params=dict(supportsAllDrives='true'))
                apagados.append(arq['name'])
        return apagados
