from django.db import migrations


def criar_itens(apps, schema_editor):
    """Cada cobrança que já existe ganha um item com o valor da sua unidade."""
    Cobranca = apps.get_model('financeiro', 'Cobranca')
    CobrancaItem = apps.get_model('financeiro', 'CobrancaItem')
    for c in Cobranca.objects.filter(unidade__isnull=False, itens__isnull=True):
        CobrancaItem.objects.create(
            cobranca=c, unidade_id=c.unidade_id, valor=c.valor,
            descricao='Aluguel' if c.tipo == 'aluguel' else 'Condomínio')


class Migration(migrations.Migration):
    dependencies = [('financeiro', '0002_cobrancaitem_boleto_unificada')]
    operations = [migrations.RunPython(criar_itens, migrations.RunPython.noop)]
