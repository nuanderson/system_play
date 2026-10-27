"""Varredura: abre todas as telas do sistema com cada perfil e procura erro, texto antigo e marcação quebrada."""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone

from cadastros.models import Locatario, Unidade
from chamados.models import Chamado, Comentario
from contatos.models import Contato
from contratos.models import Contrato
from correspondencias.models import Correspondencia
from financeiro.models import Cobranca, Competencia, Despesa
from financeiro.calculo import calcular_rateio

from .models import Usuario

PERFIS = [p.value for p in Usuario.Perfil]


def rotas_sem_admin():
    """(nome, tem_argumento) de todas as rotas do sistema, menos as da administração do Django."""
    encontradas = []

    def percorrer(padroes, tem_arg=False):
        for padrao in padroes:
            if isinstance(padrao, URLResolver):
                if padrao.namespace == 'admin':
                    continue
                percorrer(padrao.url_patterns, tem_arg or '<' in str(padrao.pattern))
            elif isinstance(padrao, URLPattern) and padrao.name:
                encontradas.append((padrao.name, tem_arg or '<' in str(padrao.pattern)))

    percorrer(get_resolver().url_patterns)
    return sorted(set(encontradas))


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class VarreduraTests(TestCase):
    def setUp(self):
        self.clinica = Locatario.objects.create(razao_social='Clínica Vida')
        self.outra = Locatario.objects.create(razao_social='Outra Empresa')
        self.terreo = Unidade.objects.get(nome='Térreo (clínica)')
        self.sala1 = Unidade.objects.get(nome='Sala 01')
        self.sala3 = Unidade.objects.get(nome='Sala 03')
        Unidade.objects.filter(pk__in=[self.terreo.pk, self.sala1.pk]).update(locatario=self.clinica)
        Unidade.objects.filter(pk=self.sala3.pk).update(locatario=self.outra)

        self.usuarios = {}
        for perfil in PERFIS:
            self.usuarios[perfil] = Usuario.objects.create_user(
                perfil, password='x', perfil=perfil, first_name=perfil.capitalize(), email=f'{perfil}@e.com',
                locatario=self.clinica if perfil in ('locatario', 'membro') else None)

        hoje = timezone.localdate()
        self.contrato = Contrato.objects.create(
            unidade=self.sala1, locatario=self.clinica, status='vigente', data_inicio=date(2026, 1, 1),
            data_fim=hoje + timedelta(days=40), valor_aluguel=Decimal('1000'), pct_energia=Decimal('60'))
        self.comp = Competencia.objects.create(referencia=date(2026, 9, 1))
        Despesa.objects.create(competencia=self.comp, descricao='Energia', valor=Decimal('500'), tipo='energia',
                               categoria='Energia Elétrica', vencimento=date(2026, 9, 5), chave='e')
        Despesa.objects.create(competencia=self.comp, descricao='Limpeza', valor=Decimal('200'), tipo='demais',
                               vencimento=date(2026, 9, 6), chave='l', multa_juros=Decimal('3'))
        calcular_rateio(self.comp)
        self.cobranca = Cobranca.objects.create(
            unidade=self.sala1, locatario=self.clinica, tipo='aluguel', referencia=hoje.replace(day=1),
            valor=Decimal('1000'), vencimento=hoje - timedelta(days=4))
        self.chamado = Chamado.objects.create(locatario=self.clinica, unidade=self.sala1, solicitante=self.usuarios['sindico'],
                                              titulo='Ar pingando', descricao='d', custo=Decimal('50'))
        Comentario.objects.create(chamado=self.chamado, autor=self.usuarios['sindico'], texto='Interno', interno=True)
        self.correspondencia = Correspondencia.objects.create(
            locatario=self.clinica, tipo='encomenda', destinatario='Ana', registrado_por=self.usuarios['equipe'],
            recebido_em=timezone.now() - timedelta(days=9))
        Contato.objects.create(categoria='eletrica', nome='João', telefone='63999990000', whatsapp=True)

        self.argumentos = {
            'contrato_detalhe': self.contrato.pk, 'contrato_editar': self.contrato.pk,
            'competencia_detalhe': self.comp.pk, 'cobranca_editar': self.cobranca.pk, 'cobranca_pagar': self.cobranca.pk,
            'chamado_detalhe': self.chamado.pk, 'correspondencia_detalhe': self.correspondencia.pk,
            'correspondencia_editar': self.correspondencia.pk, 'locatario_editar': self.clinica.pk,
            'unidade_editar': self.sala1.pk, 'usuario_editar': self.usuarios['locatario'].pk,
        }

    def url(self, nome, tem_arg):
        if not tem_arg:
            return reverse(nome)
        return reverse(nome, args=[self.argumentos.get(nome, 1)])

    def test_nenhuma_tela_da_erro_para_nenhum_perfil(self):
        rotas = rotas_sem_admin()
        self.assertGreater(len(rotas), 60)  # a varredura cobre o sistema inteiro
        problemas = []
        for perfil in PERFIS:
            self.client.force_login(self.usuarios[perfil])
            for nome, tem_arg in rotas:
                if nome in ('logout',):
                    continue
                resposta = self.client.get(self.url(nome, tem_arg))
                if resposta.status_code >= 500:
                    problemas.append(f'{perfil} {nome}: HTTP {resposta.status_code}')
                    continue
                if resposta.status_code != 200 or 'text/html' not in resposta.get('Content-Type', ''):
                    continue
                pagina = resposta.content.decode('utf-8')
                for ruim in ('Traceback', '{{', '{%', 'Locatário', 'locatários', 'None</', '>None<'):
                    if ruim in pagina:
                        problemas.append(f'{perfil} {nome}: aparece {ruim!r}')
        self.assertEqual(problemas, [])

    def test_quem_e_barrado_recebe_403_e_nao_erro(self):
        """Tela proibida devolve 403 (ou 404 quando o item é de outra empresa), nunca 500 nem página com dados."""
        self.client.force_login(self.usuarios['membro'])
        for nome in ('usuarios', 'relatorios', 'competencia_lista', 'unidade_lista', 'contrato_lista'):
            self.assertEqual(self.client.get(reverse(nome)).status_code, 403, nome)

    def test_as_telas_de_cada_perfil_mostram_a_empresa_e_nao_vazam_outra(self):
        outra_chamado = Chamado.objects.create(locatario=self.outra, unidade=self.sala3, solicitante=self.usuarios['sindico'],
                                               titulo='Chamado da outra', descricao='d')
        for perfil in ('locatario', 'membro'):
            self.client.force_login(self.usuarios[perfil])
            self.assertContains(self.client.get(reverse('chamado_lista')), 'Ar pingando', msg_prefix=perfil)
            self.assertNotContains(self.client.get(reverse('chamado_lista')), 'Chamado da outra', msg_prefix=perfil)
            self.assertEqual(self.client.get(reverse('chamado_detalhe', args=[outra_chamado.pk])).status_code, 404, perfil)

    def test_a_empresa_nao_ve_nota_interna_nem_custo(self):
        self.client.force_login(self.usuarios['locatario'])
        pagina = self.client.get(reverse('chamado_detalhe', args=[self.chamado.pk]))
        self.assertNotContains(pagina, 'Interno')
        self.assertNotContains(pagina, 'Custo')
