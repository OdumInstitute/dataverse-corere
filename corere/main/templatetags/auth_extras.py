from django import template
from django.contrib.auth.models import Group, Permission

register = template.Library()

#TODO: Deprecate this? Use perms always?
#      Worth noting that we don't want to check anything object based here currently,
#           we just used these for initial button display and group/perms is good for that
@register.filter(name='has_group')
def has_group(user, group_name): 
    group = Group.objects.get(name=group_name) 
    return True if group in user.groups.all() else False

@register.filter(name='has_global_perm')
def has_global_perm(user, perm_name):
    print(perm_name)
    print(user.has_perm(perm_name)) 
    return user.has_perm(perm_name)
    #perm = Permission.objects.get(name=perm_name) 
    #return True if perm in user.permissions.all() else False