from django.db import migrations


def criar_pessoas(apps, schema_editor):
    """Quem já tem acesso por uma empresa passa a constar também na lista de pessoas dela."""
    Usuario = apps.get_model('core', 'Usuario')
    Pessoa = apps.get_model('cadastros', 'PessoaEmpresa')
    for u in Usuario.objects.filter(perfil__in=['locatario', 'membro']).select_related('unidade'):
        empresa_id = u.locatario_id or (u.unidade.locatario_id if u.unidade_id else None)
        if empresa_id is None or Pessoa.objects.filter(usuario_id=u.pk).exists():
            continue
        Pessoa.objects.create(
            locatario_id=empresa_id, nome=(f'{u.first_name} {u.last_name}'.strip() or u.username),
            telefone=u.telefone, email=u.email, usuario_id=u.pk, ativo=u.is_active)


class Migration(migrations.Migration):
    dependencies = [
        ('cadastros', '0005_pessoas_da_empresa'),
        ('core', '0005_alter_usuario_locatario_alter_usuario_perfil'),
    ]
    operations = [migrations.RunPython(criar_pessoas, migrations.RunPython.noop)]
