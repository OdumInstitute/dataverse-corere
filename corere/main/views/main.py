import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from corere.main import models as m
from corere.main import constants as c
from corere.main.views.datatables import helper_manuscript_columns, helper_submission_columns
from corere.main.forms import * #TODO: bad practice and I don't use them all
from django.contrib.auth.models import Permission, Group
from django_fsm import has_transition_perm, TransitionNotAllowed
from django.http import Http404
from django.contrib.auth.decorators import login_required
from corere.main.gitlab import gitlab_delete_file, gitlab_submission_delete_all_files
from corere.main.binderhub import binder_build_load 
from guardian.shortcuts import assign_perm, remove_perm
from corere.main.templatetags.auth_extras import has_group

logger = logging.getLogger(__name__)

def index(request):
    if request.user.is_authenticated:
        args = {'user':     request.user, 
                'root_object_title':  request.user.current_manuscript.title if request.user.current_manuscript else None, 
                'selected_manuscript_id':  request.user.current_manuscript.id if request.user.current_manuscript else None, 
                'manuscript_columns':  helper_manuscript_columns(request.user),
                'submission_columns':  helper_submission_columns(request.user),
                'GROUP_ROLE_EDITOR': c.GROUP_ROLE_EDITOR,
                'GROUP_ROLE_AUTHOR': c.GROUP_ROLE_AUTHOR,
                'GROUP_ROLE_VERIFIER': c.GROUP_ROLE_VERIFIER,
                'GROUP_ROLE_CURATOR': c.GROUP_ROLE_CURATOR,
                'ADD_MANUSCRIPT_PERM_STRING': c.perm_path(c.PERM_MANU_ADD_M)
                }
        return render(request, "main/index.html", args)
    else:
        return render(request, "main/login.html")

@login_required
def open_binder(request, id=None):
    manuscript = get_object_or_404(m.Manuscript, id=id)
    if(not request.user.has_any_perm(c.PERM_MANU_VIEW_M, manuscript)):
        logger.warning("User id:{0} attempted to launch binder for Manuscript id:{1} which they had no permission to and should not be able to see".format(request.user.id, id))
        raise Http404()

    binder_url = binder_build_load(manuscript)
    return redirect(binder_url)

#TODO: Error if both manuscript and submission id is provided
#TODO: Make this more efficient, I think we could avoid pulling the object itself
@login_required
def delete_file(request, manuscript_id=None, submission_id=None):
    if request.method == 'POST':
        #TODO: Should send the post body
        file_path = request.GET.get('file_path')
        if(not file_path):
            raise Http404()
        if(manuscript_id):
            obj_type = "manuscript"
            obj = get_object_or_404(m.Manuscript, id=manuscript_id) # do we need this or could we have just passed the id?
            git_id = obj.gitlab_manuscript_id
        elif(submission_id):
            obj_type = "submission"
            obj = get_object_or_404(m.Submission, id=submission_id) # do we need this or could we have just passed the id?
            git_id = obj.manuscript.gitlab_submissions_id
        if(not has_transition_perm(obj.edit_noop, request.user)):
            if(manuscript_id):
                logger.warning("User id:{0} attempted to delete gitlab file path:{1} on manuscript id:{2} which is either not editable at this point, or they have no permission to".format(request.user.id, file_path, manuscript_id))
            else:
                logger.warning("User id:{0} attempted to delete gitlab file path:{1} on submission id:{2} which is either not editable at this point, or they have no permission to".format(request.user.id, file_path, submission_id))
            raise Http404()
        gitlab_delete_file(obj_type, obj, git_id, file_path)

        return redirect('./editfiles') #go to the edit files page again

@login_required
def delete_all_submission_files(request, submission_id):
    if request.method == 'POST':
        submission = get_object_or_404(m.Submission, id=submission_id)
        if(not has_transition_perm(submission.edit_noop, request.user)):
            logger.warning("User id:{0} attempted to delete all gitlab files on manuscript id:{1} which is either not editable at this point, or they have no permission to".format(request.user.id, submission_id))
            raise Http404()
        gitlab_submission_delete_all_files(submission)

        return redirect('./editfiles') #go to the edit files page again

@login_required()
def site_actions(request):
    if(has_group(request.user, c.GROUP_ROLE_CURATOR)):
        return render(request, 'main/site_actions.html', {'page_header': "site_actions"})
    else:
        raise Http404()

@login_required()
def select_manuscript(request, id):
    request.user.current_manuscript = get_object_or_404(m.Manuscript, id=id)
    request.user.save()
    return redirect("/")

@login_required()
def clear_selected_manuscript(request):
    request.user.current_manuscript = None
    request.user.save()
    return redirect("/")