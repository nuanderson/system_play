import secrets

from django.db.models import Q

# Sem 0/O, 1/l/I e outros caracteres que se confundem quando a senha é ditada ou copiada à mão.
ALFABETO = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def pessoas_da_empresa(responsavel):
    """Responsáveis e membros que pertencem à mesma empresa (ou às mesmas unidades) do responsável."""
    from .models import Usuario
    if responsavel.unidade_id:
        # Responsável limitado a uma unidade só mexe nas pessoas dessa unidade.
        filtro = Q(unidade_id=responsavel.unidade_id)
    else:
        filtro = Q(unidade_id__in=responsavel.unidades_ids())
        if responsavel.locatario_id:
            filtro |= Q(locatario_id=responsavel.locatario_id)
    return Usuario.objects.filter(filtro, perfil__in=Usuario.PERFIS_DA_UNIDADE)


def garantir_pessoa(usuario):
    """Quem recebe acesso por uma empresa também entra na lista de pessoas dela (uma vez só)."""
    from cadastros.models import PessoaEmpresa
    empresa_id = usuario.locatario_id or (usuario.unidade.locatario_id if usuario.unidade_id else None)
    if usuario.perfil not in ('locatario', 'membro') or empresa_id is None:
        return None
    pessoa, _ = PessoaEmpresa.objects.get_or_create(usuario=usuario, defaults=dict(
        locatario_id=empresa_id, nome=(usuario.get_full_name() or usuario.username),
        telefone=usuario.telefone, email=usuario.email))
    return pessoa


def gerar_senha_provisoria(tamanho=10):
    return ''.join(secrets.choice(ALFABETO) for _ in range(tamanho))


def definir_senha_provisoria(usuario):
    """Troca a senha por uma provisória e obriga o usuário a escolher a dele no próximo acesso."""
    senha = gerar_senha_provisoria()
    usuario.set_password(senha)
    usuario.trocar_senha = True
    usuario.save(update_fields=['password', 'trocar_senha'])
    return senha
