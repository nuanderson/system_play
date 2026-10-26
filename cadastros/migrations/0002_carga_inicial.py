"""Carga inicial: unidades do prédio com as áreas do projeto arquitetônico (set/2022)."""

from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations

AREA_RATEIO_2_ANDAR = Decimal('327.72')  # 406,41 (andar) - 78,69 (sala de reunião)

SALAS = [
    ('Sala 01', '15.46'), ('Sala 02', '15.96'), ('Sala 03', '16.01'), ('Sala 04', '23.04'),
    ('Sala 05', '23.13'), ('Sala 06', '16.16'), ('Sala 07', '17.16'), ('Sala 08', '21.49'),
]


def carregar(apps, schema_editor):
    Unidade = apps.get_model('cadastros', 'Unidade')

    Unidade.objects.get_or_create(nome='Térreo (clínica)', defaults=dict(
        tipo='andar', andar='Térreo', ordem=1,
        area_privativa=None, area_rateio=Decimal('425.65')))
    Unidade.objects.get_or_create(nome='1º andar (academia)', defaults=dict(
        tipo='andar', andar='1º andar', ordem=2,
        area_privativa=None, area_rateio=Decimal('406.41')))

    total_priv = sum(Decimal(a) for _, a in SALAS)
    partes = []
    for nome, area in SALAS:
        parte = (AREA_RATEIO_2_ANDAR * Decimal(area) / total_priv).quantize(Decimal('0.01'), ROUND_HALF_UP)
        partes.append(parte)
    partes[-1] += AREA_RATEIO_2_ANDAR - sum(partes)  # ajusta centavos para fechar o total

    for i, ((nome, area), parte) in enumerate(zip(SALAS, partes), start=3):
        Unidade.objects.get_or_create(nome=nome, defaults=dict(
            tipo='sala', andar='2º andar', ordem=i,
            area_privativa=Decimal(area), area_rateio=parte))

    Unidade.objects.get_or_create(nome='3º andar (restaurante)', defaults=dict(
        tipo='andar', andar='3º andar', ordem=20,
        area_privativa=None, area_rateio=Decimal('311.57')))


def desfazer(apps, schema_editor):
    Unidade = apps.get_model('cadastros', 'Unidade')
    nomes = ['Térreo (clínica)', '1º andar (academia)', '3º andar (restaurante)'] + [n for n, _ in SALAS]
    Unidade.objects.filter(nome__in=nomes).delete()


class Migration(migrations.Migration):
    dependencies = [('cadastros', '0001_initial')]
    operations = [migrations.RunPython(carregar, desfazer)]
