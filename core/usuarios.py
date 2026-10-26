import secrets

# Sem 0/O, 1/l/I e outros caracteres que se confundem quando a senha é ditada ou copiada à mão.
ALFABETO = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'


def gerar_senha_provisoria(tamanho=10):
    return ''.join(secrets.choice(ALFABETO) for _ in range(tamanho))


def definir_senha_provisoria(usuario):
    """Troca a senha por uma provisória e obriga o usuário a escolher a dele no próximo acesso."""
    senha = gerar_senha_provisoria()
    usuario.set_password(senha)
    usuario.trocar_senha = True
    usuario.save(update_fields=['password', 'trocar_senha'])
    return senha
