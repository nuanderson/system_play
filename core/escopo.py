"""Quem enxerga chamados e correspondências que não são do prédio todo.

Esses registros pertencem a uma EMPRESA (e, quando dá para saber, a uma unidade dela).
- Responsável ou membro da empresa, sem unidade marcada: vê tudo da empresa.
- Quem está limitado a uma unidade (ou é de cadastro antigo, só com unidade): vê só o que é da unidade dele.
"""

from django.db.models import Q


def q_escopo(usuario):
    """Filtro para chamados e correspondências visíveis a um responsável ou membro."""
    ids = usuario.unidades_ids()
    filtro = Q(unidade_id__in=ids)
    if usuario.locatario_id and not usuario.unidade_id:
        filtro |= Q(locatario_id=usuario.locatario_id)
    return filtro


def unidades_da_empresa(locatario):
    """As unidades ativas que a empresa ocupa."""
    from cadastros.models import Unidade
    if locatario is None:
        return Unidade.objects.none()
    return Unidade.objects.filter(locatario=locatario, ativa=True)


def unidade_unica(locatario):
    """A unidade da empresa quando ela ocupa uma só. Com várias (ou nenhuma), não dá para saber qual."""
    unidades = list(unidades_da_empresa(locatario)[:2])
    return unidades[0] if len(unidades) == 1 else None


def empresa_do_usuario(usuario):
    """A empresa de um responsável ou membro (pela empresa marcada ou pela unidade antiga)."""
    if usuario.locatario_id:
        return usuario.locatario
    if usuario.unidade_id:
        return usuario.unidade.locatario
    return None
