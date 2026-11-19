from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from relatorios.hermes import views_sql


class Command(BaseCommand):
    help = (
        'Cria (ou recria) o schema "relatorios" e as views somente leitura para o agente de relatórios. '
        'Seguro rodar de novo. Não mexe em nenhuma tabela do sistema.'
    )

    def handle(self, *args, **opcoes):
        if connection.vendor != 'postgresql':
            raise CommandError('As views do schema relatorios só existem no PostgreSQL.')
        with transaction.atomic(), connection.cursor() as cursor:
            com_papel = views_sql.instalar(cursor)
        self.stdout.write(self.style.SUCCESS(
            f'Schema {views_sql.SCHEMA} v{views_sql.VERSAO_SCHEMA}: {len(views_sql.NOMES)} views instaladas '
            f'({", ".join(views_sql.NOMES)}).'))
        self.stdout.write(
            f'Papel {views_sql.PAPEL_LEITURA}: ' + ('SELECT concedido nas views.' if com_papel else 'ainda não existe, nada concedido.'))
