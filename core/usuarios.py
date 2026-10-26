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


def gerar_senha_provisoria(tamanho=10):
    return ''.join(secrets.choice(ALFABETO) for _ in range(tamanho))


def definir_senha_provisoria(usuario):
    """Troca a senha por uma provisória e obriga o usuário a escolher a dele no próximo acesso."""
    senha = gerar_senha_provisoria()
    usuario.set_password(senha)
    usuario.trocar_senha = True
    usuario.save(update_fields=['password', 'trocar_senha'])
    return senha
