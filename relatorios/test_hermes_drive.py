"""Entrega na Unidade Compartilhada do Google Drive.

Duas camadas: uma sessão falsa (confere cada parâmetro das chamadas) e um servidor HTTP de mentira, que recebe o upload de
verdade, lê o multipart como o Google leria e responde como a API (inclusive 404 no DELETE, como no papel de Gerente de
conteúdo). Nenhuma chamada sai para a internet.
"""

import io
import json
import os
import re
import tempfile
import threading
import zipfile
from datetime import date, timedelta
from email.parser import BytesParser
from email import policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.parse import parse_qs, urlparse

import requests
from django.core.management import call_command
from django.test import SimpleTestCase

from relatorios.hermes import drive
from relatorios.test_hermes import FonteFalsa, RespostaFalsa, SessaoFalsa

DRIVE_ID = '0AN6kuuTWn11WUk9PVA'
HOJE = date(2026, 11, 19)


def zip_de_teste(pasta, nome='uruz_export_2026-11-19.zip', conteudo=b'PK-conteudo-do-zip'):
    caminho = os.path.join(pasta, nome)
    with open(caminho, 'wb') as f:
        f.write(conteudo)
    return caminho


class DriveComSessaoFalsaTests(SimpleTestCase):
    def setUp(self):
        self.pasta = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(self.pasta, ignore_errors=True))
        self.zip = zip_de_teste(self.pasta)

    def drive(self, sessao):
        return drive.Drive('chave.json', DRIVE_ID, sessao=sessao)

    def test_sem_id_da_unidade_compartilhada_nao_funciona(self):
        with self.assertRaisesMessage(drive.DriveErro, 'GOOGLE_SHARED_DRIVE_ID'):
            drive.Drive('chave.json', '')

    def test_listar_usa_os_parametros_da_unidade_compartilhada(self):
        sessao = SessaoFalsa(get=[RespostaFalsa(corpo={'files': [{'id': 'a', 'name': 'x'}]})])
        self.drive(sessao).listar("name contains 'uruz_export_' and trashed = false")
        parametros = sessao.chamadas[0][2]['params']
        self.assertEqual((parametros['driveId'], parametros['corpora']), (DRIVE_ID, 'drive'))
        self.assertEqual((parametros['includeItemsFromAllDrives'], parametros['supportsAllDrives']), ('true', 'true'))

    def test_listar_segue_a_paginacao(self):
        sessao = SessaoFalsa(get=[RespostaFalsa(corpo={'files': [{'id': '1', 'name': 'a'}], 'nextPageToken': 'T2'}),
                                  RespostaFalsa(corpo={'files': [{'id': '2', 'name': 'b'}]})])
        achados = self.drive(sessao).listar('trashed = false')
        self.assertEqual([a['id'] for a in achados], ['1', '2'])
        self.assertNotIn('pageToken', sessao.chamadas[0][2]['params'])
        self.assertEqual(sessao.chamadas[1][2]['params']['pageToken'], 'T2')

    def test_envia_para_a_raiz_da_unidade_compartilhada_com_supports_all_drives(self):
        sessao = SessaoFalsa(get=[RespostaFalsa(corpo={'files': []})], post=[RespostaFalsa(corpo={'id': 'ARQ1'})])
        r = self.drive(sessao).enviar(self.zip, 'uruz_export_2026-11-19.zip')
        self.assertEqual((r['acao'], r['id'], r['nome']), ('criado', 'ARQ1', 'uruz_export_2026-11-19.zip'))
        metodo, url, kw = sessao.chamadas[-1]
        self.assertEqual(metodo, 'post')
        self.assertEqual(url, drive.UPLOAD)
        self.assertEqual(kw['params']['supportsAllDrives'], 'true')
        self.assertEqual(kw['params']['uploadType'], 'multipart')
        corpo = kw['data'].decode('latin-1')
        self.assertIn(f'"parents": ["{DRIVE_ID}"]', corpo)
        self.assertIn('PK-conteudo-do-zip', corpo)

    def test_reexecucao_no_mesmo_dia_troca_o_conteudo_sem_duplicar(self):
        sessao = SessaoFalsa(get=[RespostaFalsa(corpo={'files': [{'id': 'ARQ9', 'name': 'x'}]})],
                             patch=[RespostaFalsa(corpo={'id': 'ARQ9'})])
        r = self.drive(sessao).enviar(self.zip, 'uruz_export_2026-11-19.zip')
        self.assertEqual(r['acao'], 'substituído')
        metodo, url, kw = sessao.chamadas[-1]
        self.assertEqual((metodo, url), ('patch', f'{drive.UPLOAD}/ARQ9'))
        self.assertNotIn('parents', kw['data'].decode('latin-1'))  # arquivo existente não troca de pai
        self.assertEqual(kw['params']['supportsAllDrives'], 'true')

    def test_nome_com_aspas_e_escapado_na_consulta(self):
        sessao = SessaoFalsa(get=[RespostaFalsa(corpo={'files': []})], post=[RespostaFalsa(corpo={'id': 'A'})])
        self.drive(sessao).enviar(self.zip, "d'avila.zip")
        self.assertIn("name = 'd\\'avila.zip'", sessao.chamadas[0][2]['params']['q'])

    def test_retencao_manda_para_a_lixeira_e_nunca_usa_delete(self):
        listagem = [{'id': 'a', 'name': 'uruz_export_2026-08-01.zip'},   # velho
                    {'id': 'b', 'name': 'uruz_export_2026-11-18.zip'},   # recente
                    {'id': 'c', 'name': 'uruz_export_2026-09-20.zip'},   # 60 dias exatos: fica
                    {'id': 'd', 'name': 'uruz_export_2026-08-21.zip'},   # 90 dias: sai
                    {'id': 'e', 'name': 'minha_planilha_importante.xlsx'},
                    {'id': 'f', 'name': 'uruz_export_2020-01-01.zip.bak'}]
        sessao = SessaoFalsa(get=[RespostaFalsa(corpo={'files': listagem})],
                             patch=[RespostaFalsa(), RespostaFalsa()], delete=[RespostaFalsa(status=404)])
        removidos = self.drive(sessao).limpar(HOJE, 60)
        self.assertEqual(sorted(removidos), ['uruz_export_2026-08-01.zip', 'uruz_export_2026-08-21.zip'])
        self.assertFalse([c for c in sessao.chamadas if c[0] == 'delete'])  # delete devolveria 404 nesse papel
        patches = [c for c in sessao.chamadas if c[0] == 'patch']
        self.assertEqual(sorted(c[1].rsplit('/', 1)[1] for c in patches), ['a', 'd'])
        for _m, url, kw in patches:
            self.assertEqual(kw['json'], {'trashed': True})
            self.assertEqual(kw['params'], {'supportsAllDrives': 'true'})
            self.assertTrue(url.startswith(drive.API + '/'))

    def test_erro_de_cota_do_google_vira_DriveErro_legivel(self):
        sessao = SessaoFalsa(get=[RespostaFalsa(corpo={'files': []})],
                             post=[RespostaFalsa(status=403, texto='{"error": {"errors": [{"reason": "storageQuotaExceeded"}]}}')])
        with self.assertRaisesRegex(drive.DriveErro, '403.*storageQuotaExceeded'):
            self.drive(sessao).enviar(self.zip, 'uruz_export_2026-11-19.zip')

    def test_erro_404_e_401_tambem_viram_DriveErro(self):
        for status in (401, 404):
            sessao = SessaoFalsa(get=[RespostaFalsa(status=status, texto='x')])
            with self.assertRaisesRegex(drive.DriveErro, str(status)):
                self.drive(sessao).listar('trashed = false')


class DriveDeMentira(BaseHTTPRequestHandler):
    """Imita as chamadas do Drive v3 que usamos, com as regras que o N3 descreveu."""

    def log_message(self, *args):
        pass

    def _json(self, status, corpo):
        dados = json.dumps(corpo).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def _ler(self):
        return self.rfile.read(int(self.headers.get('Content-Length', 0)))

    def _consulta(self):
        url = urlparse(self.path)
        return url.path, {k: v[0] for k, v in parse_qs(url.query).items()}

    def _exige_unidade_compartilhada(self, params):
        if params.get('supportsAllDrives') != 'true':
            self._json(400, {'error': 'supportsAllDrives ausente'})
            return False
        return True

    def do_GET(self):
        caminho, p = self._consulta()
        if not self._exige_unidade_compartilhada(p):
            return
        if p.get('driveId') != DRIVE_ID or p.get('corpora') != 'drive' or p.get('includeItemsFromAllDrives') != 'true':
            return self._json(400, {'error': 'parametros da unidade compartilhada ausentes'})
        q = p['q']
        arquivos = [a for a in self.server.arquivos.values() if not a['trashed']]
        exato = re.search(r"name = '(.*)' and trashed = false", q)
        prefixo = re.search(r"name contains '(.*)' and trashed = false", q)
        if exato:
            arquivos = [a for a in arquivos if a['name'] == exato.group(1).replace("\\'", "'")]
        elif prefixo:
            arquivos = [a for a in arquivos if prefixo.group(1) in a['name']]
        self.server.listagens.append(q)
        self._json(200, {'files': [{'id': a['id'], 'name': a['name']} for a in arquivos]})

    def _multipart(self, corpo):
        mensagem = BytesParser(policy=policy.default).parsebytes(
            b'Content-Type: ' + self.headers['Content-Type'].encode() + b'\r\n\r\n' + corpo)
        partes = list(mensagem.iter_parts())
        return json.loads(partes[0].get_content()), partes[1].get_content(), partes[1].get_content_type()

    def do_POST(self):
        caminho, p = self._consulta()
        corpo = self._ler()
        if caminho != '/upload/drive/v3/files' or not self._exige_unidade_compartilhada(p):
            return self._json(404, {'error': 'rota'})
        metadados, conteudo, tipo = self._multipart(corpo)
        if metadados.get('parents') != [DRIVE_ID]:
            return self._json(403, {'error': {'errors': [{'reason': 'storageQuotaExceeded'}]}})  # fora da unidade: sem cota
        id_ = f'F{len(self.server.arquivos) + 1}'
        self.server.arquivos[id_] = dict(id=id_, name=metadados['name'], conteudo=conteudo, tipo=tipo, trashed=False, parents=metadados['parents'])
        self._json(200, {'id': id_, 'name': metadados['name']})

    def do_PATCH(self):
        caminho, p = self._consulta()
        corpo = self._ler()
        if not self._exige_unidade_compartilhada(p):
            return
        id_ = caminho.rsplit('/', 1)[1]
        if id_ not in self.server.arquivos:
            return self._json(404, {'error': 'sem arquivo'})
        if caminho.startswith('/upload/'):
            _meta, conteudo, _tipo = self._multipart(corpo)
            self.server.arquivos[id_]['conteudo'] = conteudo
            return self._json(200, {'id': id_, 'name': self.server.arquivos[id_]['name']})
        alteracao = json.loads(corpo)
        if alteracao.get('trashed') is True:
            self.server.arquivos[id_]['trashed'] = True
        self._json(200, {'id': id_})

    def do_DELETE(self):
        self.server.deletes.append(self.path)
        self._json(404, {'error': 'File not found'})  # o papel Gerente de conteúdo recebe 404 no delete


class ServidorDeMentira:
    def __enter__(self):
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), DriveDeMentira)
        self.http.arquivos, self.http.listagens, self.http.deletes = {}, [], []
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.http.server_address[1]}'
        return self

    def __exit__(self, *erro):
        self.http.shutdown()
        self.http.server_close()

    def arquivo(self, nome):
        return next((a for a in self.http.arquivos.values() if a['name'] == nome and not a['trashed']), None)


class DriveComServidorHttpTests(SimpleTestCase):
    """O Drive de verdade fala HTTP: aqui a requisição é montada, enviada e lida como o Google leria."""

    def setUp(self):
        self.pasta = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(self.pasta, ignore_errors=True))
        self.servidor = ServidorDeMentira()
        self.addCleanup(self.servidor.__exit__, None, None, None)
        self.servidor.__enter__()
        self.drive = drive.Drive('chave.json', DRIVE_ID, sessao=requests.Session())
        self.drive.api = self.servidor.base + '/drive/v3/files'
        self.drive.upload = self.servidor.base + '/upload/drive/v3/files'

    def test_o_zip_chega_inteiro_na_unidade_compartilhada(self):
        conteudo = bytes(range(256)) * 40  # binário de verdade, com todos os bytes
        caminho = zip_de_teste(self.pasta, conteudo=conteudo)
        r = self.drive.enviar(caminho, 'uruz_export_2026-11-19.zip')
        self.assertEqual(r['acao'], 'criado')
        recebido = self.servidor.arquivo('uruz_export_2026-11-19.zip')
        self.assertEqual(recebido['conteudo'], conteudo)  # byte a byte
        self.assertEqual((recebido['parents'], recebido['tipo']), ([DRIVE_ID], 'application/zip'))

    def test_enviar_de_novo_substitui_o_conteudo_e_nao_cria_outro(self):
        self.drive.enviar(zip_de_teste(self.pasta, conteudo=b'primeira versao'), 'uruz_export_2026-11-19.zip')
        r = self.drive.enviar(zip_de_teste(self.pasta, conteudo=b'segunda versao'), 'uruz_export_2026-11-19.zip')
        self.assertEqual(r['acao'], 'substituído')
        self.assertEqual(len(self.servidor.http.arquivos), 1)
        self.assertEqual(self.servidor.arquivo('uruz_export_2026-11-19.zip')['conteudo'], b'segunda versao')

    def test_retencao_vai_para_a_lixeira_e_o_delete_nunca_e_chamado(self):
        for dias in (0, 30, 60, 61, 90):
            self.drive.enviar(zip_de_teste(self.pasta, nome=f'uruz_export_{(HOJE - timedelta(days=dias)).isoformat()}.zip'),
                              f'uruz_export_{(HOJE - timedelta(days=dias)).isoformat()}.zip')
        removidos = self.drive.limpar(HOJE, 60)
        self.assertEqual(sorted(removidos), ['uruz_export_2026-08-21.zip', 'uruz_export_2026-09-19.zip'])
        self.assertEqual(self.servidor.http.deletes, [])
        vivos = sorted(a['name'] for a in self.servidor.http.arquivos.values() if not a['trashed'])
        self.assertEqual(vivos, ['uruz_export_2026-09-20.zip', 'uruz_export_2026-10-20.zip', 'uruz_export_2026-11-19.zip'])

    def test_arquivo_de_outra_pessoa_na_unidade_nao_e_tocado(self):
        self.servidor.http.arquivos['X1'] = dict(id='X1', name='planilha_do_financeiro.xlsx', conteudo=b'x', tipo='x', trashed=False, parents=[DRIVE_ID])
        self.drive.limpar(HOJE, 60)
        self.assertFalse(self.servidor.http.arquivos['X1']['trashed'])


class ComandoComDriveDeMenitraTests(SimpleTestCase):
    """O comando exportar_hermes de ponta a ponta, com a chave presente: sobe na Unidade Compartilhada e registra o log."""

    def setUp(self):
        self.pasta = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(self.pasta, ignore_errors=True))
        self.chave = os.path.join(self.pasta, 'uruz-export.json')
        open(self.chave, 'w').write('{}')
        self.servidor = ServidorDeMentira()
        self.addCleanup(self.servidor.__exit__, None, None, None)
        self.servidor.__enter__()
        sessao = requests.Session()
        self.patches = [
            mock.patch.dict(os.environ, {'GOOGLE_SA_JSON': self.chave, 'GOOGLE_SHARED_DRIVE_ID': DRIVE_ID,
                                         'HERMES_RO_PASSWORD': 'senha-de-teste'}),
            mock.patch.object(drive.Drive, 'sessao', new_callable=mock.PropertyMock, return_value=sessao),
            mock.patch.object(drive.Drive, 'api', self.servidor.base + '/drive/v3/files'),
            mock.patch.object(drive.Drive, 'upload', self.servidor.base + '/upload/drive/v3/files'),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def exportar(self):
        fonte = FonteFalsa()
        fonte.fechar = lambda: None
        saida = io.StringIO()
        with mock.patch('relatorios.management.commands.exportar_hermes.exportador.FontePostgres', return_value=fonte):
            with self.assertLogs('uruz.exportacao', level='INFO') as logs:
                call_command('exportar_hermes', '--saida', os.path.join(self.pasta, 'exports'), stdout=saida)
        return '\n'.join(logs.output)

    def test_sobe_na_unidade_compartilhada_e_registra_o_log_pedido(self):
        log = self.exportar()
        nome = f'uruz_export_{date.today().isoformat()}.zip'
        self.assertRegex(log, rf'Drive: {re.escape(nome)} criado')
        self.assertIn(f'Unidade Compartilhada {DRIVE_ID}', log)
        recebido = self.servidor.arquivo(nome)
        self.assertIsNotNone(recebido)
        self.assertEqual(recebido['parents'], [DRIVE_ID])
        with zipfile.ZipFile(io.BytesIO(recebido['conteudo'])) as z:  # o que chegou no Drive é o zip certo, com manifest
            manifest = json.loads(z.read('manifest.json'))
        self.assertEqual(manifest['entrega'], {'destino': 'google_drive', 'unidade_compartilhada': DRIVE_ID})
        self.assertEqual(len(manifest['arquivos']), 5)
        with open(os.path.join(self.pasta, 'exports', nome), 'rb') as local:
            self.assertEqual(recebido['conteudo'], local.read())  # idêntico ao zip do servidor

    def test_segunda_execucao_no_dia_registra_substituido(self):
        self.exportar()
        log = self.exportar()
        self.assertRegex(log, r'Drive: uruz_export_\d{4}-\d{2}-\d{2}\.zip substituído')
        self.assertEqual(len([a for a in self.servidor.http.arquivos.values() if not a['trashed']]), 1)

    def test_zips_velhos_da_unidade_vao_para_a_lixeira_na_execucao_diaria(self):
        velho = f'uruz_export_{(date.today() - timedelta(days=75)).isoformat()}.zip'
        self.servidor.http.arquivos['V1'] = dict(id='V1', name=velho, conteudo=b'x', tipo='x', trashed=False, parents=[DRIVE_ID])
        log = self.exportar()
        self.assertTrue(self.servidor.http.arquivos['V1']['trashed'])
        self.assertIn('1 zip(s) antigo(s) enviado(s) para a lixeira', log)
        self.assertEqual(self.servidor.http.deletes, [])

    def test_chave_sem_id_da_unidade_falha_e_deixa_o_zip_no_servidor(self):
        with mock.patch.dict(os.environ, {'GOOGLE_SHARED_DRIVE_ID': ''}):
            with self.assertRaises(SystemExit) as saiu:
                self.exportar()
        self.assertEqual(saiu.exception.code, 1)
        self.assertEqual(len(os.listdir(os.path.join(self.pasta, 'exports'))), 1)
        self.assertEqual(self.servidor.http.arquivos, {})
