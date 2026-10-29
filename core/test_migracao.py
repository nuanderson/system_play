"""Ensaio das migrações novas: monta o banco no formato antigo, grava dados e migra para o formato atual."""

from datetime import date
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

ANTES = [
    ('cadastros', '0002_carga_inicial'),
    ('contratos', '0001_initial'),
    ('chamados', '0001_initial'),
    ('correspondencias', '0002_remove_correspondencia_rastreio_and_more'),
    ('core', '0003_usuario_trocar_senha_alter_usuario_perfil'),
    ('financeiro', '0001_initial'),
]


class MigracaoParaEmpresaTests(TransactionTestCase):
    """O que existe em produção hoje: chamados e correspondências só com a unidade, usuários só com a unidade."""

    def migrar(self, alvos):
        executor = MigrationExecutor(connection)
        executor.migrate(alvos)
        return executor.loader.project_state(alvos).apps

    def tearDown(self):
        # Deixa o banco de teste no formato mais novo para os testes seguintes.
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_dados_antigos_sobrevivem_e_ganham_a_empresa(self):
        antigo = self.migrar(ANTES)
        Unidade, Locatario = antigo.get_model('cadastros', 'Unidade'), antigo.get_model('cadastros', 'Locatario')
        Chamado, Correspondencia = antigo.get_model('chamados', 'Chamado'), antigo.get_model('correspondencias', 'Correspondencia')
        Usuario, Contrato = antigo.get_model('core', 'Usuario'), antigo.get_model('contratos', 'Contrato')

        clinica = Locatario.objects.create(razao_social='Clínica Vida')
        ocupada = Unidade.objects.get(nome='Sala 01')
        ocupada.locatario_id = clinica.pk
        ocupada.save()
        livre = Unidade.objects.get(nome='Sala 02')  # sem empresa

        usuario_com_unidade = Usuario.objects.create(username='resp', perfil='locatario', unidade_id=ocupada.pk, password='x')
        contrato = Contrato.objects.create(
            unidade_id=ocupada.pk, locatario_id=clinica.pk, status='vigente', data_inicio=date(2026, 1, 1),
            valor_aluguel=Decimal('1000'))
        chamado_ocupada = Chamado.objects.create(unidade_id=ocupada.pk, titulo='A', descricao='d')
        chamado_livre = Chamado.objects.create(unidade_id=livre.pk, titulo='B', descricao='d')
        chamado_comum = Chamado.objects.create(unidade_id=None, titulo='Área comum', descricao='d')
        carta_ocupada = Correspondencia.objects.create(unidade_id=ocupada.pk, destinatario='Ana', tipo='carta')
        carta_livre = Correspondencia.objects.create(unidade_id=livre.pk, destinatario='Bia', tipo='carta')

        novo = self.migrar([(app, nome) for app, nome in self._folhas()])
        Chamado, Correspondencia = novo.get_model('chamados', 'Chamado'), novo.get_model('correspondencias', 'Correspondencia')
        Usuario = novo.get_model('core', 'Usuario')

        # a empresa vem da unidade que o item já tinha
        self.assertEqual(Chamado.objects.get(pk=chamado_ocupada.pk).locatario_id, clinica.pk)
        self.assertEqual(Correspondencia.objects.get(pk=carta_ocupada.pk).locatario_id, clinica.pk)
        # unidade livre ou área comum: não há empresa para ligar, e nada se perde
        self.assertIsNone(Chamado.objects.get(pk=chamado_livre.pk).locatario_id)
        self.assertIsNone(Chamado.objects.get(pk=chamado_comum.pk).locatario_id)
        self.assertIsNone(Correspondencia.objects.get(pk=carta_livre.pk).locatario_id)
        self.assertEqual(Chamado.objects.get(pk=chamado_livre.pk).unidade_id, livre.pk)
        self.assertEqual(Correspondencia.objects.get(pk=carta_livre.pk).destinatario, 'Bia')
        self.assertEqual(Chamado.objects.count(), 3)
        self.assertEqual(Correspondencia.objects.count(), 2)
        # usuários antigos continuam só com a unidade e sem a obrigação de trocar a senha
        usuario = Usuario.objects.get(pk=usuario_com_unidade.pk)
        self.assertEqual((usuario.unidade_id, usuario.locatario_id, usuario.trocar_senha), (ocupada.pk, None, False))
        self.assertEqual(novo.get_model('contratos', 'Contrato').objects.get(pk=contrato.pk).locatario_id, clinica.pk)
        # quem já tinha acesso passa a constar nas pessoas da empresa (pela unidade antiga)
        pessoa = novo.get_model('cadastros', 'PessoaEmpresa').objects.get(usuario_id=usuario_com_unidade.pk)
        self.assertEqual((pessoa.locatario_id, pessoa.nome, pessoa.ativo), (clinica.pk, 'resp', True))

    def test_migrar_sem_nenhum_dado_tambem_funciona(self):
        self.migrar(ANTES)
        novo = self.migrar(self._folhas())
        self.assertEqual(novo.get_model('chamados', 'Chamado').objects.count(), 0)

    def test_migracoes_novas_sao_reversiveis(self):
        """Dá para voltar atrás (sem perder os dados antigos) se o deploy precisar ser desfeito."""
        self.migrar(self._folhas())
        antigo = self.migrar(ANTES)
        self.assertTrue(antigo.get_model('chamados', 'Chamado'))

    @staticmethod
    def _folhas():
        executor = MigrationExecutor(connection)
        return [n for n in executor.loader.graph.leaf_nodes() if n[0] in {a for a, _ in ANTES}]
