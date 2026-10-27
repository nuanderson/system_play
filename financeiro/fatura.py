"""Fatura do condomínio em PDF: o que a empresa paga no mês e os gastos do prédio que explicam o valor."""

import io
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import Despesa

MARINHO = colors.HexColor('#05385C')
AZUL = colors.HexColor('#0074C8')
CIANO = colors.HexColor('#00BEF5')
LINHA = colors.HexColor('#D5DEE8')
FUNDO = colors.HexColor('#F2F6FA')
ZERO = Decimal('0')


def real(valor):
    """R$ 1.234,56 (e -R$ 10,00 para desconto)."""
    texto = f'{abs(valor):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return f'-R$ {texto}' if valor < 0 else f'R$ {texto}'


def dados(cobranca):
    """Tudo o que entra na fatura, já somado. Só para cobrança de condomínio que veio de um rateio aprovado."""
    rateio = cobranca.rateio
    itens = list(cobranca.itens.select_related('unidade'))
    unidades_ids = {i.unidade_id for i in itens}
    rateio_itens = [r for r in rateio.itens.select_related('unidade') if r.unidade_id in unidades_ids]
    meus = sum((r.total for r in rateio_itens), ZERO)

    por_categoria = defaultdict(lambda: ZERO)
    for d in rateio.competencia.despesas.filter(entra_no_rateio=True):
        por_categoria[d.categoria.strip() or f'{Despesa.Tipo(d.tipo).label} (sem categoria)'] += d.valor
    categorias = sorted(por_categoria.items(), key=lambda par: par[1], reverse=True)
    total_gastos = sum(por_categoria.values(), ZERO)

    return dict(
        cobranca=cobranca, empresa=cobranca.locatario, competencia=rateio.competencia, rateio_itens=rateio_itens,
        ajustes=[i for i in itens if i.descricao != 'Condomínio'], meus=meus,
        participacao=(meus / rateio.total * 100) if rateio.total else ZERO,
        categorias=categorias, total_gastos=total_gastos,
    )


def _estilos():
    base = getSampleStyleSheet()
    return dict(
        titulo=ParagraphStyle('titulo', parent=base['Title'], fontName='Helvetica-Bold', fontSize=18,
                              textColor=MARINHO, alignment=2, spaceAfter=2),
        sub=ParagraphStyle('sub', parent=base['Normal'], fontSize=10, textColor=colors.grey, alignment=2),
        secao=ParagraphStyle('secao', parent=base['Normal'], fontName='Helvetica-Bold', fontSize=10,
                             textColor=AZUL, spaceBefore=14, spaceAfter=6),
        normal=ParagraphStyle('normal', parent=base['Normal'], fontSize=9.5, leading=13),
        nota=ParagraphStyle('nota', parent=base['Normal'], fontSize=8, leading=11, textColor=colors.grey),
    )


def _tabela(linhas, larguras, cabecalho=True, total=True, alinhar_direita=()):
    tabela = Table(linhas, colWidths=larguras, repeatRows=1 if cabecalho else 0)
    estilo = [
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LINEBELOW', (0, 0), (-1, -1), 0.4, LINHA),
    ]
    for coluna in alinhar_direita:
        estilo.append(('ALIGN', (coluna, 0), (coluna, -1), 'RIGHT'))
    if cabecalho:
        estilo += [('BACKGROUND', (0, 0), (-1, 0), MARINHO), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                   ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold')]
    if total:
        estilo += [('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'), ('BACKGROUND', (0, -1), (-1, -1), FUNDO)]
    tabela.setStyle(TableStyle(estilo))
    return tabela


def gerar_pdf(cobranca):
    """Devolve os bytes do PDF."""
    d = dados(cobranca)
    estilo = _estilos()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
        title=f'Fatura de condomínio {d["competencia"]} · {d["empresa"]}', author='Play Participações')
    largura = A4[0] - 36 * mm
    historia = []

    logo = settings.BASE_DIR / 'static' / 'img' / 'logo.png'
    cabecalho = [[
        Image(str(logo), width=22 * mm, height=22 * mm * 862 / 827) if logo.exists() else '',
        [Paragraph('Fatura de condomínio', estilo['titulo']),
         Paragraph(f'Edifício Uruz · competência {d["competencia"]}', estilo['sub'])],
    ]]
    topo = Table(cabecalho, colWidths=[30 * mm, largura - 30 * mm])
    topo.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LINEBELOW', (0, 0), (-1, 0), 1.5, CIANO),
                              ('BOTTOMPADDING', (0, 0), (-1, -1), 8)]))
    historia += [topo, Spacer(1, 10)]

    empresa = d['empresa']
    ficha = [
        ['Empresa', empresa.razao_social, 'Vencimento', f'{cobranca.vencimento:%d/%m/%Y}'],
        ['CNPJ', empresa.cnpj or '-', 'Valor a pagar', real(cobranca.valor)],
    ]
    quadro = Table(ficha, colWidths=[24 * mm, largura - 24 * mm - 62 * mm, 28 * mm, 34 * mm])
    quadro.setStyle(TableStyle([
        ('FONTSIZE', (0, 0), (-1, -1), 9.5), ('BACKGROUND', (0, 0), (-1, -1), FUNDO),
        ('TEXTCOLOR', (0, 0), (0, -1), colors.grey), ('TEXTCOLOR', (2, 0), (2, -1), colors.grey),
        ('FONTNAME', (3, 1), (3, 1), 'Helvetica-Bold'), ('FONTNAME', (1, 0), (1, 0), 'Helvetica-Bold'),
        ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('ALIGN', (3, 0), (3, -1), 'RIGHT'),
    ]))
    historia += [quadro]

    historia += [Paragraph('Valores cobrados', estilo['secao'])]
    linhas = [['Unidade', 'Área (m²)', 'Energia', 'Água', 'Demais', 'Total']]
    for r in d['rateio_itens']:
        area = f'{r.area:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
        linhas.append([r.unidade.nome, area, real(r.energia), real(r.agua), real(r.demais), real(r.total)])
    for ajuste in d['ajustes']:
        linhas.append([f'{ajuste.unidade.nome} · {ajuste.descricao}', '', '', '', '', real(ajuste.valor)])
    linhas.append(['Total', '', '', '', '', real(cobranca.valor)])
    historia.append(_tabela(linhas, [largura - 4 * 25 * mm - 22 * mm, 22 * mm] + [25 * mm] * 4,
                            alinhar_direita=(1, 2, 3, 4, 5)))
    historia.append(Spacer(1, 4))
    historia.append(Paragraph(
        f'Sua participação no total das despesas rateadas do mês: {d["participacao"]:.2f}%'.replace('.', ','),
        estilo['nota']))

    historia += [Paragraph('Gastos do prédio no mês', estilo['secao'])]
    linhas = [['Categoria', 'Valor', '% do total']]
    for nome, valor in d['categorias']:
        pct = (valor / d['total_gastos'] * 100) if d['total_gastos'] else ZERO
        linhas.append([nome, real(valor), f'{pct:.1f}%'.replace('.', ',')])
    if not d['categorias']:
        linhas.append(['Sem despesas lançadas', '', ''])
    linhas.append(['Total das despesas rateadas', real(d['total_gastos']), '100%' if d['categorias'] else ''])
    historia.append(_tabela(linhas, [largura - 60 * mm, 32 * mm, 28 * mm], alinhar_direita=(1, 2)))
    historia.append(Spacer(1, 4))
    historia.append(Paragraph(
        'Despesas consideradas no rateio. Energia e água seguem o percentual definido em contrato, quando houver; '
        'as demais despesas são divididas pela área de cada unidade. Multas e juros pagos por atraso não entram.',
        estilo['nota']))

    historia.append(Spacer(1, 16))
    historia.append(Paragraph(
        f'Play Participações · Edifício Uruz · gerada em {timezone.localdate():%d/%m/%Y}', estilo['nota']))
    doc.build(historia)
    return buffer.getvalue()
