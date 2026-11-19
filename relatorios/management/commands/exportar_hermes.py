import logging
import os
import sys

from django.conf import settings
from django.core.management.base import BaseCommand

from relatorios.hermes import drive as drive_mod
from relatorios.hermes import exportador
from relatorios.hermes.views_sql import FUSO, NOMES, PAPEL_LEITURA, VERSAO_SCHEMA

log = logging.getLogger('uruz.exportacao')


def conexao_somente_leitura():
    """Dados de conexão do papel de leitura. O padrão é o mesmo banco do app, mas com o usuário hermes_ro."""
    banco = settings.DATABASES['default']
    senha = os.environ.get('HERMES_RO_PASSWORD', '')
    if not senha:
        raise RuntimeError('Falta HERMES_RO_PASSWORD no ambiente (a senha do papel de leitura).')
    return dict(
        host=os.environ.get('HERMES_RO_HOST') or banco.get('HOST') or 'localhost',
        port=os.environ.get('HERMES_RO_PORT') or banco.get('PORT') or '5432',
        dbname=os.environ.get('HERMES_RO_NAME') or banco.get('NAME'),
        user=os.environ.get('HERMES_RO_USER', PAPEL_LEITURA),
        password=senha,
    )


class Command(BaseCommand):
    help = (
        'Gera uruz_export_AAAA-MM-DD.zip (um CSV por view do schema relatorios + manifest.json) e entrega no Google Drive. '
        'Só lê. Se uma view falhar, registra no manifest e continua. Sai com código 1 se algo falhou.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--saida', default=os.environ.get('HERMES_EXPORT_DIR', 'exports'),
                            help='Pasta onde o zip é gravado (padrão: HERMES_EXPORT_DIR ou ./exports).')
        parser.add_argument('--manter-dias', type=int, default=exportador.RETENCAO_DIAS,
                            help='Quantos dias de zips manter, aqui e no Drive (padrão: 60).')
        parser.add_argument('--sem-entrega', action='store_true', help='Só grava o zip, sem enviar ao Drive.')

    def handle(self, *args, **opcoes):
        logging.basicConfig(stream=sys.stdout, level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
        momento = exportador.agora()
        dia = momento.date()
        falhou = False

        caminho_json = os.environ.get('GOOGLE_SA_JSON', '')
        drive_id = os.environ.get('GOOGLE_SHARED_DRIVE_ID', '')
        chave_existe = bool(caminho_json) and os.path.isfile(caminho_json)
        quer_drive = not opcoes['sem_entrega'] and chave_existe
        if not opcoes['sem_entrega'] and not chave_existe:
            log.warning('Sem chave da service account%s: o zip fica só na pasta do servidor, sem envio ao Drive.',
                        f' (o arquivo {caminho_json} ainda não existe)' if caminho_json else ' (GOOGLE_SA_JSON não definido)')
        entrega = dict(destino='google_drive' if quer_drive else 'somente_servidor',
                       unidade_compartilhada=drive_id if quer_drive else None)

        try:
            fonte = exportador.FontePostgres(**conexao_somente_leitura())
        except Exception as exc:
            log.error('Não consegui conectar ao banco com o papel de leitura: %s', type(exc).__name__)
            self.stdout.write(self.style.ERROR('Falha de conexão com o banco. Nada foi gerado.'))
            raise SystemExit(1)
        try:
            arquivos, conteudos = exportador.montar_arquivos(fonte, NOMES)
        finally:
            fonte.fechar()
        manifest = exportador.montar_manifest(arquivos, entrega, momento)
        caminho = exportador.gravar_zip(opcoes['saida'], dia, conteudos, manifest)
        falhou = falhou or bool(manifest['erros'])

        log.info('zip gerado: %s (%d bytes) | schema v%s | fuso %s', caminho, os.path.getsize(caminho), VERSAO_SCHEMA, FUSO)
        for a in arquivos:
            if a['status'] == 'ok':
                log.info('  %-22s %6d linhas  sha256 %s', a['arquivo'], a['linhas'], a['sha256'][:16] + '…')
            else:
                log.error('  %-22s ERRO %s', a['arquivo'], a['erro'])

        apagados = exportador.limpar_antigos(opcoes['saida'], dia, opcoes['manter_dias'])
        if apagados:
            log.info('retenção local: %d zip(s) antigo(s) removido(s)', len(apagados))

        if quer_drive:
            try:
                drive = drive_mod.Drive(caminho_json, drive_id)
                enviado = drive.enviar(caminho, exportador.nome_do_zip(dia))
                log.info('Drive: %s %s', enviado['nome'], enviado['acao'])
                log.info('Drive: Unidade Compartilhada %s | arquivo %s | %d bytes', drive_id, enviado['id'], enviado['bytes'])
                removidos = drive.limpar(dia, opcoes['manter_dias'])
                if removidos:
                    log.info('Drive: %d zip(s) antigo(s) enviado(s) para a lixeira', len(removidos))
            except Exception as exc:
                falhou = True
                log.error('Entrega no Drive falhou: %s', exc)

        self.stdout.write(self.style.SUCCESS(f'Exportação de {dia.isoformat()}: {len(conteudos)}/{len(NOMES)} views ok.')
                          if not falhou else self.style.ERROR(f'Exportação de {dia.isoformat()} terminou com erros (veja o log).'))
        if falhou:
            raise SystemExit(1)
