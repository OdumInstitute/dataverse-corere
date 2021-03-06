import logging, os, requests 
import git
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from corere.main import models as m
from corere.main import forms as f #TODO: bad practice and I don't use them all
from .. import constants as c 
from corere.main import docker as d
from guardian.shortcuts import assign_perm, remove_perm, get_perms
from guardian.mixins import LoginRequiredMixin, PermissionRequiredMixin
from guardian.core import ObjectPermissionChecker
from django_fsm import has_transition_perm, TransitionNotAllowed
from django.http import Http404
from corere.main.utils import fsm_check_transition_perm, get_role_name_for_form
from django.contrib.auth.models import Group
#from django.contrib.auth.mixins import LoginRequiredMixin #TODO: Did we need both? I don't think so.
from django.views import View
from corere.main import git as g
logger = logging.getLogger(__name__)  
from django.http import HttpResponse
from django.db.models import Max
#from guardian.decorators import permission_required_or_404

########################################## GENERIC + MIXINS ##########################################

#TODO: "transition_method_name" is a bit misleading. We are (over)using transitions to do perm checks, but the no-ops aren't actually transitioning

#To use this at the very least you'll need to use the GetOrGenerateObjectMixin.
class GenericCorereObjectView(View):
    form = None
    form_dict = None
    model = None
    template = 'main/form_object_generic.html'
    redirect = '..'
    read_only = False
    message = None
    http_method_names = ['get', 'post'] #Used by the base View class
    #For GetOrGenerateObjectMixin, instantiated here so they don't override.
    parent_reference_name = None
    parent_id_name = None
    parent_model = None
    #TODO: Move definitions into mixins? Will that blow up?
    #NOTE: that these do not clear on their own and have to be cleared manually. There has to be a better way...
    #      If you don't clear them you get duplicate notes etc
    repo_dict_gen = []
    file_delete_url = None
    #TODO: This is too much. Need a better way to deal with these params. Also some are for manuscript and some are for submission
    helper = f.GenericFormSetHelper()
    page_header = ""
    note_formset = None
    note_helper = None
    
    create = False #Used by default template

    def dispatch(self, request, *args, **kwargs): 
        try:
            self.form = self.form(self.request.POST or None, self.request.FILES or None, instance=self.object)
        except TypeError as e: #Added so that progress and other calls that don't use forms can work. TODO: implement better
            pass
        return super(GenericCorereObjectView, self).dispatch(request,*args, **kwargs)

    #NOTE: Both get/post has a lot of logic to deal with whether notes are/aren't defined. We should probably handled this in a different way.
    # Maybe find a way to pass the extra import in all the child views, maybe with different templates?

    #The generic get/post is used by submission file views.

    def get(self, request, *args, **kwargs):
        if(isinstance(self.object, m.Manuscript)):
            root_object_title = self.object.title
        else:
            root_object_title = self.object.manuscript.title

        context = {'form': self.form, 'helper': self.helper, 'read_only': self.read_only, "obj_type": self.object_friendly_name, "create": self.create,
            'repo_dict_gen': self.repo_dict_gen, 'file_delete_url': self.file_delete_url, 'page_header': self.page_header, 'root_object_title': root_object_title}

        return render(request, self.template, context)

    def post(self, request, *args, **kwargs):
        print(self.redirect)
        if(isinstance(self.object, m.Manuscript)):
            root_object_title = self.object.title
        else:
            root_object_title = self.object.manuscript.title

        if self.form.is_valid():
            if not self.read_only:
                self.form.save() #Note: this is what saves a newly created model instance
            messages.add_message(request, messages.SUCCESS, self.message)
            return redirect(self.redirect)
        else:
            logger.debug(self.form.errors)

        context = {'form': self.form, 'helper': self.helper, 'read_only': self.read_only, "obj_type": self.object_friendly_name, "create": self.create,
            'repo_dict_gen': self.repo_dict_gen, 'file_delete_url': self.file_delete_url, 'page_header': self.page_header, 'root_object_title': root_object_title}

        return render(request, self.template, context)

class GitFilesMixin(object):
    def dispatch(self, request, *args, **kwargs):
        if(isinstance(self.object, m.Manuscript)):
            self.repo_dict_gen = g.get_manuscript_files_list(self.object)
            self.file_delete_url = "/manuscript/"+str(self.object.id)+"/deletefile/?file_path="
            self.file_download_url = "/manuscript/"+str(self.object.id)+"/downloadfile/?file_path="
        elif(isinstance(self.object, m.Submission)):
            self.repo_dict_gen = g.get_submission_files_list(self.object.manuscript)
            self.file_delete_url = "/submission/"+str(self.object.id)+"/deletefile/?file_path="
            self.file_download_url = "/submission/"+str(self.object.id)+"/downloadfile/?file_path="
            print(self.file_download_url)
        else:
            logger.error("Attempted to load Git file for an object which does not have git files") #TODO: change error
            raise Http404()

        return super(GitFilesMixin, self).dispatch(request, *args, **kwargs)

#We need to get the object first before django-guardian checks it.
#For some reason django-guardian doesn't do it in its dispatch and the function it calls does not get the args we need
#Maybe I'm missing something but for now this is the way its happening
#
#Note: this does not save a newly created model in itself, which is good for when we need to check transition perms, etc
class GetOrGenerateObjectMixin(object):
    #TODO: This gets called on every get, do we need to generate the messages this early?
    def dispatch(self, request, *args, **kwargs):
        if kwargs.get('id'):
            self.object = get_object_or_404(self.model, id=kwargs.get('id'))
            self.message = 'Your '+self.object_friendly_name + ': ' + str(self.object.id) + ' has been updated!'
        elif not self.read_only:
            self.object = self.model()
            if(self.parent_model is not None):
                print("The object create method I didn't want called after create got called")
                setattr(self.object, self.parent_reference_name, get_object_or_404(self.parent_model, id=kwargs.get(self.parent_id_name)))
                if(self.parent_reference_name == "submission"):
                    setattr(self.object, "manuscript", self.object.submission.manuscript)
            self.message = 'Your new '+self.object_friendly_name +' has been created!'
        else:
            logger.error("Error with GetOrGenerateObjectMixin dispatch")
        return super(GetOrGenerateObjectMixin, self).dispatch(request, *args, **kwargs)
    
# class ChooseRoleFormMixin(object):
#     def dispatch(self, request, *args, **kwargs):
#         print("CHOOSE ROLE")
#         if(isinstance(self.object, m.Manuscript)):
#             self.form = self.form_dict[get_role_name_for_form(request.user, self.object, request.session)]
#         else:
#             self.form = self.form_dict[get_role_name_for_form(request.user, self.object.manuscript, request.session)]
#         return super(ChooseRoleFormMixin, self).dispatch(request,*args, **kwargs)

#A mixin that calls Django fsm has_transition_perm for an object
#It expects that the object has been grabbed already, for example by GetCreateObjectMixin    
#TODO: Is this specifically for noop transitions? if so we should name it that way.
class TransitionPermissionMixin(object):
    transition_on_parent = False
    def dispatch(self, request, *args, **kwargs):
        if(self.transition_on_parent):
            parent_object = getattr(self.object, self.parent_reference_name)
            transition_method = getattr(parent_object, self.transition_method_name)
        else:
            transition_method = getattr(self.object, self.transition_method_name)
        logger.debug("User perms on object: " + str(get_perms(request.user, self.object))) #DEBUG
        if(not has_transition_perm(transition_method, request.user)):
            logger.debug("PermissionDenied")
            raise Http404()
        return super(TransitionPermissionMixin, self).dispatch(request, *args, **kwargs)    
    pass

#via https://gist.github.com/ceolson01/206139a093b3617155a6 , with edits
class GroupRequiredMixin(object):
    def dispatch(self, request, *args, **kwargs):
        if(len(self.groups_required)>0):
            if not request.user.is_authenticated:
                raise Http404()
            else:
                user_groups = []
                for group in request.user.groups.values_list('name', flat=True):
                    user_groups.append(group)
                if len(set(user_groups).intersection(self.groups_required)) <= 0:
                    raise Http404()
        return super(GroupRequiredMixin, self).dispatch(request, *args, **kwargs)

############################################# MANUSCRIPT #############################################

# Do not call directly
# We pass m_status here to provide the "progess" option. We want this to show up even if something is missing (e.g. no files) so that we can tell the user.
# ... but maybe this should be another case in the model
class GenericManuscriptView(GenericCorereObjectView):
    object_friendly_name = 'manuscript'
    model = m.Manuscript
    template = 'main/form_object_manuscript.html'
    author_formset = None
    data_source_formset = None
    keyword_formset = None 
    role_name = None
    from_submission = False
    create = False

    def dispatch(self, request, *args, **kwargs):
        if self.read_only:
            #All Manuscript fields are visible to all users, so no role-based forms
            self.form = f.ReadOnlyManuscriptForm
            if self.request.user.is_superuser or not self.create:
                self.author_formset = f.ReadOnlyAuthorFormSet
                self.data_source_formset = f.ReadOnlyDataSourceFormSet
                self.keyword_formset = f.ReadOnlyKeywordFormSet
        else:
            self.role_name = get_role_name_for_form(request.user, self.object, request.session, self.create)
            self.form = f.ManuscriptForms[self.role_name]
            if self.request.user.is_superuser or not self.create:
                self.author_formset = f.AuthorManuscriptFormsets[self.role_name]
                self.data_source_formset = f.DataSourceManuscriptFormsets[self.role_name]
                self.keyword_formset = f.KeywordManuscriptFormsets[self.role_name]

        return super(GenericManuscriptView, self).dispatch(request,*args, **kwargs)

    def get(self, request, *args, **kwargs):
        if(isinstance(self.object, m.Manuscript)):
            root_object_title = self.object.title
        else:
            root_object_title = self.object.manuscript.title

        print(self.from_submission)
        if(self.from_submission):
            messages.add_message(request, messages.INFO, "First, please fill out the additional info regarding your Manuscript.")

        context = {'form': self.form, 'read_only': self.read_only, "obj_type": self.object_friendly_name, "create": self.create, 'from_submission': self.from_submission, 'repo_dict_gen': self.repo_dict_gen, 'file_delete_url': self.file_delete_url, 
            'm_status':self.object._status, 'page_header': self.page_header, 'root_object_title': root_object_title, 'helper': self.helper, 'manuscript_helper': f.ManuscriptFormHelper(), }#'role_name': self.role_name, 

        if self.request.user.is_superuser or not self.create:
            context['author_formset'] = self.author_formset(instance=self.object, prefix="author_formset")
            context['author_inline_helper'] = f.GenericInlineFormSetHelper(form_id='author')
            context['data_source_formset'] = self.data_source_formset(instance=self.object, prefix="data_source_formset")
            context['data_source_inline_helper'] = f.GenericInlineFormSetHelper(form_id='data_source')
            context['keyword_formset'] = self.keyword_formset(instance=self.object, prefix="keyword_formset")
            context['keyword_inline_helper'] = f.GenericInlineFormSetHelper(form_id='keyword')

        return render(request, self.template, context)

    def post(self, request, *args, **kwargs):
        if self.request.user.is_superuser or not self.create:
            self.author_formset = self.author_formset(request.POST, instance=self.object, prefix="author_formset")
            self.data_source_formset = self.data_source_formset(request.POST, instance=self.object, prefix="data_source_formset")
            self.keyword_formset = self.keyword_formset(request.POST, instance=self.object, prefix="keyword_formset")

        if(isinstance(self.object, m.Manuscript)):
            root_object_title = self.object.title
        else:
            root_object_title = self.object.manuscript.title

        if not self.read_only and self.form.is_valid() \
            and (not self.author_formset or self.author_formset.is_valid()) and (not self.data_source_formset or self.data_source_formset.is_valid()) and (not self.keyword_formset or self.keyword_formset.is_valid()):
            self.form.save()
            if(self.author_formset):
                self.author_formset.save()
            if(self.data_source_formset):
                self.data_source_formset.save()
            if(self.keyword_formset):
                self.keyword_formset.save()

            if request.POST.get('submit_continue'):
                messages.add_message(request, messages.SUCCESS, self.message)
                #return redirect('manuscript_addauthor', id=self.object.id)
                return redirect('manuscript_uploadfiles', id=self.object.id)
            elif request.POST.get('submit_continue_submission'):
                messages.add_message(request, messages.SUCCESS, self.message)
                return redirect('manuscript_createsubmission', manuscript_id=self.object.id)
            else:
                return redirect(self.redirect)
        else:
            logger.debug(self.form.errors)      
            logger.debug(self.author_formset.errors)
            logger.debug(self.data_source_formset.errors)
            logger.debug(self.keyword_formset.errors)  

        context = {'form': self.form, 'read_only': self.read_only, "obj_type": self.object_friendly_name, "create": self.create, 'from_submission': self.from_submission, 'repo_dict_gen': self.repo_dict_gen, 'file_delete_url': self.file_delete_url, 
            'm_status':self.object._status, 'page_header': self.page_header, 'root_object_title': root_object_title, 'helper': self.helper, 'manuscript_helper': f.ManuscriptFormHelper()}

        if self.request.user.is_superuser or not self.create:
            context['author_formset'] = self.author_formset
            context['author_inline_helper'] = f.GenericInlineFormSetHelper(form_id='author')
            context['data_source_formset'] = self.data_source_formset
            context['data_source_inline_helper'] = f.GenericInlineFormSetHelper(form_id='data_source')
            context['keyword_formset'] = self.keyword_formset
            context['keyword_inline_helper'] = f.GenericInlineFormSetHelper(form_id='keyword')

        return render(request, self.template, context)
           
#NOTE: LoginRequiredMixin has to be the leftmost. So we have to put it on every "real" view. Yes it sucks.
class ManuscriptCreateView(LoginRequiredMixin, GetOrGenerateObjectMixin, PermissionRequiredMixin, GenericManuscriptView):
    permission_required = c.perm_path(c.PERM_MANU_ADD_M)
    accept_global_perms = True
    return_403 = True
    page_header = "Create New Manuscript"
    create = True
    redirect = "/"

class ManuscriptEditView(LoginRequiredMixin, GetOrGenerateObjectMixin, TransitionPermissionMixin, GenericManuscriptView):
    #template = 'main/form_object_manuscript.html'
    transition_method_name = 'edit_noop'
    page_header = "Edit Manuscript"

class ManuscriptCompleteView(LoginRequiredMixin, GetOrGenerateObjectMixin, TransitionPermissionMixin, GenericManuscriptView):
    #template = 'main/form_object_manuscript.html'
    transition_method_name = 'edit_noop'
    page_header = "Edit Manuscript"
    from_submission = True

class ManuscriptReadView(LoginRequiredMixin, GetOrGenerateObjectMixin, TransitionPermissionMixin, GitFilesMixin, GenericManuscriptView):
    #form_dict = f.ReadOnlyManuscriptForm #TODO: IMPLEMENT READONLY
    transition_method_name = 'view_noop'
    page_header = "View Manuscript"
    http_method_names = ['get']
    read_only = True

class ManuscriptUploadFilesView(LoginRequiredMixin, GetOrGenerateObjectMixin, TransitionPermissionMixin, GitFilesMixin, GenericManuscriptView):
    form = f.ManuscriptFilesForm #TODO: Delete this if we really don't need a form?
    template = 'main/not_form_upload_files.html'
    transition_method_name = 'edit_noop'
    page_header = "Upload Files for Manuscript"

    def get(self, request, *args, **kwargs):
        return render(request, self.template, {'form': self.form, 'helper': self.helper, 'read_only': self.read_only, 'm_status':self.object._status, 
            'root_object_title': self.object.title, 'repo_dict_gen': list(self.repo_dict_gen), 'file_delete_url': self.file_delete_url, 'file_download_url': self.file_download_url, 
            'obj_id': self.object.id, "obj_type": self.object_friendly_name, "repo_branch":"master", 'page_header': self.page_header,
            })

    def post(self, request, *args, **kwargs):
         if request.POST.get('submit_continue'):
            if list(self.repo_dict_gen): #this is hacky because you can only read a generator once.
                return redirect('manuscript_addauthor', id=self.object.id)
            else:
                self.message = 'You must upload some files to the manuscript!'
                messages.add_message(request, messages.ERROR, self.message)

                return render(request, self.template, {'form': self.form, 'helper': self.helper, 'read_only': self.read_only, 'm_status':self.object._status, 
                    'root_object_title': self.object.title, 'repo_dict_gen': [], 'file_delete_url': self.file_delete_url, 'file_download_url': self.file_download_url, 
                    'obj_id': self.object.id, "obj_type": self.object_friendly_name, "repo_branch":"master", 'page_header': self.page_header,
                    })

class ManuscriptUploaderView(LoginRequiredMixin, GetOrGenerateObjectMixin, TransitionPermissionMixin, GenericManuscriptView):
    form = f.ManuscriptFilesForm #TODO: Delete this if we really don't need a form?
    template = 'main/not_form_upload_files.html'
    transition_method_name = 'edit_noop'
    page_header = "Upload Files for Manuscript"
    http_method_names = ['post']

    #TODO: Should we making sure these files are safe?
    def post(self, request, *args, **kwargs):
        if not self.read_only:
            file = request.FILES.get('file')
            fullPath = request.POST.get('fullPath','')
            path = fullPath.rsplit(file.name)[0] #returns '' if fullPath is blank, e.g. file is on root
            g.store_manuscript_file(self.object, file, path)
            return HttpResponse(status=200)

class ManuscriptDownloadFileView(LoginRequiredMixin, GetOrGenerateObjectMixin, TransitionPermissionMixin, GenericManuscriptView):
    http_method_names = ['get']
    transition_method_name = 'edit_noop'

    def get(self, request, *args, **kwargs):
        file_path = request.GET.get('file_path')
        if(not file_path):
            raise Http404()

        return g.download_manuscript_file(self.object, file_path)

class ManuscriptDeleteFileView(LoginRequiredMixin, GetOrGenerateObjectMixin, TransitionPermissionMixin, GenericManuscriptView):
    http_method_names = ['post']
    transition_method_name = 'edit_noop'

    def post(self, request, *args, **kwargs):
        file_path = request.GET.get('file_path')
        if(not file_path):
            raise Http404()
        g.delete_manuscript_file(self.object, file_path)
        
        return HttpResponse(status=200)

#TODO: Pass less parameters, especially token stuff. Could combine with ManuscriptUploadFilesView, but how to handle parameters with that...
class ManuscriptReadFilesView(LoginRequiredMixin, GetOrGenerateObjectMixin, TransitionPermissionMixin, GitFilesMixin, GenericManuscriptView):
    form = f.ManuscriptFilesForm #TODO: Delete this if we really don't need a form?
    template = 'main/not_form_upload_files.html'
    transition_method_name = 'view_noop'
    page_header = "View Files for Manuscript"
    http_method_names = ['get']
    read_only = True

    def get(self, request, *args, **kwargs):
        return render(request, self.template, {'form': self.form, 'helper': self.helper, 'read_only': self.read_only, 
            'root_object_title': self.object.title, 'repo_dict_gen': self.repo_dict_gen, 'file_delete_url': self.file_delete_url, 
            'obj_id': self.object.id, "obj_type": self.object_friendly_name, "repo_branch":"master", 'page_header': self.page_header
            })

class ManuscriptFilesListAjaxView(LoginRequiredMixin, GetOrGenerateObjectMixin, TransitionPermissionMixin, GitFilesMixin, GenericManuscriptView):
    template = 'main/file_list.html'
    transition_method_name = 'edit_noop'

    def get(self, request, *args, **kwargs):
        return render(request, self.template, {'read_only': self.read_only, 'page_header': self.page_header,
            'file_delete_url': self.file_delete_url, 'file_download_url': self.file_download_url, 
            'root_object_title': self.object.title, 'repo_dict_gen': self.repo_dict_gen, 'file_delete_url': self.file_delete_url, 'obj_id': self.object.id, "obj_type": self.object_friendly_name})

#Does not use TransitionPermissionMixin as it does the check internally. Maybe should switch
#This and the other "progressviews" could be made generic, but I get the feeling we'll want to customize all the messaging and then it'll not really be worth it
class ManuscriptProgressView(LoginRequiredMixin, GetOrGenerateObjectMixin, GenericManuscriptView):
    def post(self, request, *args, **kwargs):
        try:
            if not fsm_check_transition_perm(self.object.begin, request.user): 
                print(str(self.object))
                logger.error("PermissionDenied")
                raise Http404()
            try:
                self.object.begin()
                self.object.save()
            except TransitionNotAllowed as e:
                logger.error("TransitionNotAllowed: " + str(e))
                raise
            self.message = 'Your '+self.object_friendly_name + ': ' + str(self.object.id) + ' was handed to authors!'
            messages.add_message(request, messages.SUCCESS, self.message)
        except (TransitionNotAllowed):
            self.message = 'Object '+self.object_friendly_name + ': ' + str(self.object.id) + ' could not be handed to authors, please contact the administrator.'
            messages.add_message(request, messages.ERROR, self.message)
        return redirect('/manuscript/'+str(self.object.id))

class ManuscriptReportView(LoginRequiredMixin, GetOrGenerateObjectMixin, GenericManuscriptView):
    template = 'main/manuscript_report.html'
    def get(self, request, *args, **kwargs):
        #This should ensure the user has read access
        #What data do we need to pull? Just the manuscript? Eh probably gotta do more lifting here
        return render(request, self.template, {'manuscript': self.object})

############################################# SUBMISSION #############################################

# Do not call directly. Used for the main submission form
class GenericSubmissionFormView(GenericCorereObjectView):
    parent_reference_name = 'manuscript'
    parent_id_name = "manuscript_id"
    parent_model = m.Manuscript
    object_friendly_name = 'submission'
    model = m.Submission
    note_formset = f.NoteSubmissionFormset
    note_helper = f.NoteFormSetHelper()
    prev_sub_vmetadata = None

    edition_formset = None
    curation_formset = None
    verification_formset = None
    v_metadata_formset = None
    v_metadata_software_formset = None
    v_metadata_badge_formset = None
    v_metadata_audit_formset = None

    def get(self, request, *args, **kwargs):     
        root_object_title = self.object.manuscript.title
        context = {'form': self.form, 'helper': self.helper, 'read_only': self.read_only, "obj_type": self.object_friendly_name, "create": self.create, 'inline_helper': f.GenericInlineFormSetHelper(),
            'repo_dict_gen': self.repo_dict_gen, 'file_delete_url': self.file_delete_url, 'page_header': self.page_header, 'root_object_title': root_object_title, 's_status':self.object._status, 'parent_id': self.object.manuscript.id,
            'v_metadata_software_inline_helper': f.GenericInlineFormSetHelper(form_id='v_metadata_software'), 'v_metadata_badge_inline_helper': f.GenericInlineFormSetHelper(form_id='v_metadata_badge'), 'v_metadata_audit_inline_helper': f.GenericInlineFormSetHelper(form_id='v_metadata_audit') }

        if(self.note_formset is not None):
            checkers = [ObjectPermissionChecker(Group.objects.get(name=c.GROUP_ROLE_AUTHOR)), ObjectPermissionChecker(Group.objects.get(name=c.GROUP_ROLE_EDITOR)),
                ObjectPermissionChecker(Group.objects.get(name=c.GROUP_ROLE_CURATOR)), ObjectPermissionChecker(Group.objects.get(name=c.GROUP_ROLE_VERIFIER))]
            notes = m.Note.objects.filter(parent_submission=self.object)
            for checker in checkers:
                checker.prefetch_perms(notes)
            sub_files = self.object.submission_files.all().order_by('path','name')

            context['note_formset'] = self.note_formset(instance=self.object, prefix="note_formset", 
                form_kwargs={'checkers': checkers, 'manuscript': self.object.manuscript, 'submission': self.object, 'sub_files': sub_files}) #TODO: This was set to `= formset`, maybe can delete that variable now?
        if(self.edition_formset is not None):
            context['edition_formset'] = self.edition_formset(instance=self.object, prefix="edition_formset")
        if(self.curation_formset is not None):
            context['curation_formset'] = self.curation_formset(instance=self.object, prefix="curation_formset")
        if(self.verification_formset is not None):
            context['verification_formset'] = self.verification_formset(instance=self.object, prefix="verification_formset")
        if(self.v_metadata_formset is not None):
            context['v_metadata_formset'] = self.v_metadata_formset(instance=self.object, prefix="v_metadata_formset", form_kwargs={'previous_vmetadata': self.prev_sub_vmetadata})
        try:
            if(self.v_metadata_software_formset is not None):
                context['v_metadata_software_formset'] = self.v_metadata_software_formset(instance=self.object.submission_vmetadata, prefix="v_metadata_software_formset")
            if(self.v_metadata_badge_formset is not None):
                context['v_metadata_badge_formset'] = self.v_metadata_badge_formset(instance=self.object.submission_vmetadata, prefix="v_metadata_badge_formset")
            if(self.v_metadata_audit_formset is not None):
                context['v_metadata_audit_formset'] = self.v_metadata_audit_formset(instance=self.object.submission_vmetadata, prefix="v_metadata_audit_formset")
        except self.model.submission_vmetadata.RelatedObjectDoesNotExist: #With a new submission, submission_vmetadata does not exist yet
            if(self.v_metadata_software_formset is not None):
                context['v_metadata_software_formset'] = self.v_metadata_software_formset(prefix="v_metadata_software_formset")
            if(self.v_metadata_badge_formset is not None):
                context['v_metadata_badge_formset'] = self.v_metadata_badge_formset(prefix="v_metadata_badge_formset")
            if(self.v_metadata_audit_formset is not None):
                context['v_metadata_audit_formset'] = self.v_metadata_audit_formset(prefix="v_metadata_audit_formset")

        if(self.note_helper is not None):
            context['note_helper'] = self.note_helper

        return render(request, self.template, context)

    def post(self, request, *args, **kwargs):
        self.redirect = "/manuscript/"+str(self.object.manuscript.id)

        root_object_title = self.object.manuscript.title

        if(self.note_formset):
            checkers = [ObjectPermissionChecker(Group.objects.get(name=c.GROUP_ROLE_AUTHOR)), ObjectPermissionChecker(Group.objects.get(name=c.GROUP_ROLE_EDITOR)),
                ObjectPermissionChecker(Group.objects.get(name=c.GROUP_ROLE_CURATOR)), ObjectPermissionChecker(Group.objects.get(name=c.GROUP_ROLE_VERIFIER))]
            notes = m.Note.objects.filter(parent_submission=self.object)
            for checker in checkers:
                checker.prefetch_perms(notes)
            sub_files = self.object.submission_files.all().order_by('path','name')

            self.note_formset = self.note_formset(request.POST, instance=self.object, prefix="note_formset", 
                form_kwargs={'checkers': checkers, 'manuscript': self.object.manuscript, 'submission': self.object, 'sub_files': sub_files}) #TODO: This was set to `= formset`, maybe can delete that variable now?
        if(self.edition_formset):
            self.edition_formset = self.edition_formset(request.POST, instance=self.object, prefix="edition_formset")
        if(self.curation_formset):
            self.curation_formset = self.curation_formset(request.POST, instance=self.object, prefix="curation_formset")
        if(self.verification_formset):
            self.verification_formset = self.verification_formset(request.POST, instance=self.object, prefix="verification_formset")
        if(self.v_metadata_formset):
            self.v_metadata_formset = self.v_metadata_formset(request.POST, instance=self.object, prefix="v_metadata_formset")

        #TODO: not sure if we need to do this ID logic in the post    
        try:
            if(self.v_metadata_software_formset is not None):
                self.v_metadata_software_formset = self.v_metadata_software_formset(request.POST, instance=self.object.submission_vmetadata, prefix="v_metadata_software_formset")
            if(self.v_metadata_badge_formset is not None):
                self.v_metadata_badge_formset = self.v_metadata_badge_formset(request.POST, instance=self.object.submission_vmetadata, prefix="v_metadata_badge_formset")
            if(self.v_metadata_audit_formset is not None):
                self.v_metadata_audit_formset = self.v_metadata_audit_formset(request.POST, instance=self.object.submission_vmetadata, prefix="v_metadata_audit_formset")
        except self.model.submission_vmetadata.RelatedObjectDoesNotExist: #With a new submission, submission_vmetadata does not exist yet
            if(self.v_metadata_software_formset is not None):
                self.v_metadata_software_formset = self.v_metadata_software_formset(request.POST, prefix="v_metadata_software_formset")
            if(self.v_metadata_badge_formset is not None):
                self.v_metadata_badge_formset = self.v_metadata_badge_formset(request.POST, prefix="v_metadata_badge_formset")
            if(self.v_metadata_audit_formset is not None):
                self.v_metadata_audit_formset = self.v_metadata_audit_formset(request.POST, prefix="v_metadata_audit_formset")

        #This code checks whether to attempt saving, seeing that each formset that exists is valid
        #If we have to add even more formsets, we should consider creating a list of formsets to check dynamically
        if not self.read_only:
            if( self.form.is_valid() and (self.edition_formset is None or self.edition_formset.is_valid()) and (self.curation_formset is None or self.curation_formset.is_valid()) 
                and (self.verification_formset is None or self.verification_formset.is_valid()) and (self.v_metadata_formset is None or self.v_metadata_formset.is_valid()) 
                and (self.v_metadata_software_formset is None or self.v_metadata_software_formset.is_valid())
                and (self.v_metadata_badge_formset is None or self.v_metadata_badge_formset.is_valid()) and (self.v_metadata_audit_formset is None or self.v_metadata_audit_formset.is_valid()) 
                ):
                self.form.save() #Note: this is what saves a newly created model instance
                if(self.edition_formset):
                    self.edition_formset.save()
                if(self.curation_formset):
                    self.curation_formset.save()
                if(self.verification_formset):
                    self.verification_formset.save()
                if(self.v_metadata_formset):
                    self.v_metadata_formset.save()
                if(self.v_metadata_software_formset):
                    self.v_metadata_software_formset.save()
                if(self.v_metadata_badge_formset):
                    self.v_metadata_badge_formset.save()
                if(self.v_metadata_audit_formset): 
                    self.v_metadata_audit_formset.save()
                if(self.note_formset is not None and self.note_formset.is_valid()):
                    self.note_formset.save()

                try:
                    # if request.POST.get('submit_progress_submission'):
                    #     if not fsm_check_transition_perm(self.object.submit, request.user): 
                    #         logger.debug("PermissionDenied")
                    #         raise Http404()
                    #     self.object.submit(request.user)
                    #     self.object.save()
                    if request.POST.get('submit_progress_edition'):
                        if not fsm_check_transition_perm(self.object.submit_edition, request.user):
                            logger.debug("PermissionDenied")
                            raise Http404()
                        self.object.submit_edition()
                        self.object.save()
                    elif request.POST.get('submit_progress_curation'):
                        if not fsm_check_transition_perm(self.object.review_curation, request.user):
                            logger.debug("PermissionDenied")
                            raise Http404()
                        self.object.review_curation()
                        self.object.save()
                    elif request.POST.get('submit_progress_verification'):
                        if not fsm_check_transition_perm(self.object.review_verification, request.user):
                            logger.debug("PermissionDenied")
                            raise Http404()
                        self.object.review_verification()
                        self.object.save()
                except TransitionNotAllowed as e:
                    logger.error("TransitionNotAllowed: " + str(e))
                    raise

                if request.POST.get('submit_continue'):
                    messages.add_message(request, messages.SUCCESS, self.message)
                    return redirect('submission_uploadfiles', id=self.object.id)

                return redirect(self.redirect)

            else:
                if(self.edition_formset):
                    logger.debug(self.edition_formset.errors)
                if(self.curation_formset):
                    logger.debug(self.curation_formset.errors)
                if(self.verification_formset):
                    logger.debug(self.verification_formset.errors)
                if(self.v_metadata_formset):
                    logger.debug(self.v_metadata_formset.errors)
                if(self.v_metadata_software_formset):
                    logger.debug(self.v_metadata_software_formset.errors)
                if(self.v_metadata_badge_formset):
                    logger.debug(self.v_metadata_badge_formset.errors)
                if(self.v_metadata_audit_formset): 
                    logger.debug(self.v_metadata_audit_formset.errors)
        else:
            if(self.note_formset is not None and self.note_formset.is_valid()): #these can be saved even if read only
                self.note_formset.save()

        context = {'form': self.form, 'helper': self.helper, 'read_only': self.read_only, "obj_type": self.object_friendly_name, "create": self.create, 'inline_helper': f.GenericInlineFormSetHelper(),
            'repo_dict_gen': self.repo_dict_gen, 'file_delete_url': self.file_delete_url, 'page_header': self.page_header, 'root_object_title': root_object_title, 's_status':self.object._status, 'parent_id': self.object.manuscript.id,
            'v_metadata_software_inline_helper': f.GenericInlineFormSetHelper(form_id='v_metadata_software'), 'v_metadata_badge_inline_helper': f.GenericInlineFormSetHelper(form_id='v_metadata_badge'), 'v_metadata_audit_inline_helper': f.GenericInlineFormSetHelper(form_id='v_metadata_audit') }
        
        if(self.note_formset is not None):
            context['note_formset'] = self.note_formset
        if(self.edition_formset is not None):
            context['edition_formset'] = self.edition_formset
        if(self.curation_formset is not None):
            context['curation_formset'] = self.curation_formset
        if(self.verification_formset is not None):
            context['verification_formset'] = self.verification_formset
        if(self.v_metadata_formset is not None):
            context['v_metadata_formset'] = self.v_metadata_formset
        if(self.v_metadata_software_formset is not None):
            context['v_metadata_software_formset'] = self.v_metadata_software_formset
        if(self.v_metadata_badge_formset is not None):
            context['v_metadata_badge_formset'] = self.v_metadata_badge_formset
        if(self.v_metadata_audit_formset is not None):
            context['v_metadata_audit_formset'] = self.v_metadata_audit_formset

        if(self.note_helper is not None):
            context['note_helper'] = self.note_helper

        return render(request, self.template, context)

    #Custom method, called via dispatch. Copies over submission and its verification metadatas
    #This does not copy over GitFiles, those are done later in the flow
    def copy_previous_submission_contents(self, manuscript, version_id):
        print("COPY PREV SUB")
        prev_sub = m.Submission.objects.get(manuscript=manuscript, version_id=version_id)
        #self.prev_sub_vmetadata_queryset = m.VerificationMetadata.objects.get(id=prev_sub.submission_vmetadata.id)

        self.prev_sub_vmetadata =  prev_sub.submission_vmetadata
        prev_sub.pk = None
        prev_sub.id = None #Do I need to do both?
        self.object = prev_sub

        #print(prev_sub_vmetadata.__dict__)
        #prev_sub_vmetadata.pk = None
        #prev_sub_vmetadata.id = None
        #self.object.submission_vmetadata = prev_sub_vmetadata

        #Copy all verification metadatas


    #TODO: Move this to the top, after (probably) deleting add_formsets
    def dispatch(self, request, *args, **kwargs):
        #If new submission with previous submission existing, copy over data from the previous submission
        if(request.method == 'GET' and not self.object.id):
            prev_max_sub_version_id = self.object.manuscript.get_max_submission_version_id()
            if prev_max_sub_version_id:
                self.copy_previous_submission_contents(self.object.manuscript, prev_max_sub_version_id)

        role_name = get_role_name_for_form(request.user, self.object.manuscript, request.session, False)
        try:
            if(not self.read_only and (has_transition_perm(self.object.manuscript.add_submission_noop, request.user) or has_transition_perm(self.object.edit_noop, request.user))):
                self.form = f.SubmissionForms[role_name]
            elif(has_transition_perm(self.object.view_noop, request.user)):
                self.form = f.ReadOnlySubmissionForm
        except (m.Submission.DoesNotExist, KeyError):
            pass
        try:
            if(not self.read_only and (has_transition_perm(self.object.add_edition_noop, request.user) or has_transition_perm(self.object.submission_edition.edit_noop, request.user))):
                self.edition_formset = f.EditionSubmissionFormsets[role_name]
            elif(has_transition_perm(self.object.submission_edition.view_noop, request.user)):
                self.edition_formset = f.ReadOnlyEditionSubmissionFormset
        except (m.Edition.DoesNotExist, KeyError):
            pass
        try:
            if(not self.read_only and (has_transition_perm(self.object.add_curation_noop, request.user) or has_transition_perm(self.object.submission_curation.edit_noop, request.user))):
                self.curation_formset = f.CurationSubmissionFormsets[role_name]
            elif(has_transition_perm(self.object.submission_curation.view_noop, request.user)):
                self.curation_formset = f.ReadOnlyCurationSubmissionFormset
        except (m.Curation.DoesNotExist, KeyError):
            pass
        try:
            if(not self.read_only and (has_transition_perm(self.object.add_verification_noop, request.user) or has_transition_perm(self.object.submission_verification.edit_noop, request.user))):
                self.verification_formset = f.VerificationSubmissionFormsets[role_name]
            elif(has_transition_perm(self.object.submission_verification.view_noop, request.user)):
                self.verification_formset = f.ReadOnlyVerificationSubmissionFormset
        except (m.Verification.DoesNotExist, KeyError):
            pass

        try:
            if(not self.read_only and (has_transition_perm(self.object.manuscript.add_submission_noop, request.user) or has_transition_perm(self.object.edit_noop, request.user))):
                self.v_metadata_formset = f.VMetadataSubmissionFormsets[role_name]
            elif(has_transition_perm(self.object.view_noop, request.user)):
                self.v_metadata_formset = f.ReadOnlyVMetadataSubmissionFormset
        except (m.Submission.DoesNotExist, KeyError):
            pass

        try:
            if(not self.read_only and (has_transition_perm(self.object.manuscript.add_submission_noop, request.user) or has_transition_perm(self.object.edit_noop, request.user))):
                self.v_metadata_software_formset = f.VMetadataSoftwareVMetadataFormsets[role_name]
            elif(has_transition_perm(self.object.view_noop, request.user)):
                self.v_metadata_software_formset = f.ReadOnlyVMetadataSoftwareVMetadataFormset
        except (m.Submission.DoesNotExist, KeyError):
            pass

        #So the problem with these is that we enforce "curators-only" by checking add/edit for a curation. We don't have a view_curation option (because curations become public once completed) so we can't enforce view.
        try:
            if(not self.read_only and (has_transition_perm(self.object.add_curation_noop, request.user) or has_transition_perm(self.object.submission_curation.edit_noop, request.user))):
                self.v_metadata_badge_formset = f.VMetadataBadgeVMetadataFormsets[role_name]
            elif(self.read_only and (role_name is "Curator" or role_name is "Admin")): #This is hacky, should be a "transition" perm on the object
                #TODO: For some reason this (and audit) aren't actually showing up. They look to have contents and I don't think the javascript is hiding them...
                self.v_metadata_badge_formset = f.ReadOnlyVMetadataBadgeVMetadataFormset
        except (m.Submission.DoesNotExist, KeyError):
            pass
        except (m.Curation.DoesNotExist, KeyError):
            pass
        try:
            if(not self.read_only and (has_transition_perm(self.object.add_curation_noop, request.user) or has_transition_perm(self.object.submission_curation.edit_noop, request.user))):
                self.v_metadata_audit_formset = f.VMetadataAuditVMetadataFormsets[role_name]
            elif(self.read_only and (role_name is "Curator" or role_name is "Admin")): #This is hacky, should be a "transition" perm on the object
                self.v_metadata_audit_formset = f.ReadOnlyVMetadataAuditVMetadataFormset
        except (m.Submission.DoesNotExist, KeyError):
            pass
        except (m.Curation.DoesNotExist, KeyError):
            pass

        #TODO: Figure out how we should do perms for these
        #self.v_metadata_formset = f.VMetadataSubmissionFormset
        #self.v_metadata_software_formset = f.VMetadataSoftwareVMetadataFormset
        #self.v_metadata_badge_formset = f.VMetadataBadgeVMetadataFormset
        #self.v_metadata_audit_formset = f.VMetadataAuditVMetadataFormset


        return super().dispatch(request, *args, **kwargs)

        # if self.read_only:
        #     #All Manuscript fields are visible to all users, so no role-based forms
        #     self.form = f.ReadOnlyManuscriptForm
        #     self.author_formset = f.ReadOnlyAuthorFormSet
        #     self.data_source_formset = f.ReadOnlyDataSourceFormSet
        #     self.keyword_formset = f.ReadOnlyKeywordFormSet
        # else:
        #     role_name = get_role_name_for_form(request.user, self.object, request.session)
        #     self.form = f.ManuscriptForms[role_name]
        #     self.author_formset = f.AuthorManuscriptFormsets[role_name]
        #     self.data_source_formset = f.DataSourceManuscriptFormsets[role_name]
        #     self.keyword_formset = f.KeywordManuscriptFormsets[role_name]
        




        # print("=== Inline Formset Questions ===")
        # print(self.v_metadata_audit_formset.__dict__)
        #print(self.v_metadata_audit_formset.form.__dict__)
        #print(self.v_metadata_audit_formset.form.base_fields['name'].__dict__) 
        # self.v_metadata_audit_formset.form.base_fields['name'].disabled = True
        # print(self.v_metadata_audit_formset.form.base_fields['name'].__dict__) 
        # print("=== Restrictor Tests ===")
        # print(f.VMetadataSubmissionFormset.form.base_fields['host_url'].__dict__)
        # print(f.VMetadataSubmissionFormsetRestrictTest.form.base_fields['host_url'].__dict__)
        
       
        #print(f.VMetadataSubmissionFormset.form.__dict__)
        #print(f.testfactorydict['0'].form.__dict__)
        # print(f.VMetadataSubmissionFormset.__dict__)
        # print(f.testfactorydict['1'].__dict__)

class SubmissionCreateView(LoginRequiredMixin, GetOrGenerateObjectMixin, TransitionPermissionMixin, GenericSubmissionFormView):
    transition_method_name = 'add_submission_noop'
    transition_on_parent = True
    page_header = "Create New Submission"
    template = 'main/form_object_submission.html'
    create = True

    # def get(self, request, *args, **kwargs):
    #     return super(SubmissionCreateView, self).get(request,*args, **kwargs)

#Removed TransitionPermissionMixin because multiple cases can edit. We do all the checking inside the view
#TODO: Should we combine this view with the read view? There will be cases where you can edit a review but not the main form maybe?
class SubmissionEditView(LoginRequiredMixin, GetOrGenerateObjectMixin, GenericSubmissionFormView):
    transition_method_name = 'edit_noop'
    page_header = "Edit Submission"
    template = 'main/form_object_submission.html'

class SubmissionReadView(LoginRequiredMixin, GetOrGenerateObjectMixin, TransitionPermissionMixin, GitFilesMixin, GenericSubmissionFormView):
    form = f.ReadOnlySubmissionForm
    transition_method_name = 'view_noop'
    page_header = "View Submission"
    read_only = True #We still allow post because you can still create/edit notes.
    template = 'main/form_object_submission.html'

# TODO: Do we need all the parameters being passed? Especially for read?
# TODO: I'm a bit surprised this doesn't blow up when posting with invalid data. The root post is used (I think). Maybe the get is called after to render the page?
class GenericSubmissionFilesMetadataView(LoginRequiredMixin, GetOrGenerateObjectMixin, TransitionPermissionMixin, GitFilesMixin, GenericCorereObjectView):
    template = 'main/form_edit_files.html'
    helper=f.GitFileFormSetHelper()
    page_header = "Edit File Metadata for Submission"
    parent_reference_name = 'manuscript'
    parent_id_name = "manuscript_id"
    parent_model = m.Manuscript
    object_friendly_name = 'submission'
    model = m.Submission

    def get(self, request, *args, **kwargs):
        #TODO: Can we just refer to form for everything and delete a bunch of stuff?
        formset = self.form
        return render(request, self.template, {'form': self.form, 'helper': self.helper, 'read_only': self.read_only, 
            'root_object_title': self.object.manuscript.title, 'repo_dict_gen': self.repo_dict_gen, 's_status':self.object._status, 'parent_id': self.object.manuscript.id,
            'file_delete_url': self.file_delete_url, 'obj_id': self.object.id, "obj_type": self.object_friendly_name, "repo_branch":g.helper_get_submission_branch_name(self.object),
            'children_formset':formset, 'page_header': self.page_header})

    #Originally copied from GenericCorereObjectView
    def post(self, request, *args, **kwargs):
        formset = self.form
        if formset.is_valid():
            if not self.read_only:
                formset.save() #Note: this is what saves a newly created model instance
                if request.POST.get('back_save'):
                    return redirect('submission_uploadfiles', id=self.object.id)
                elif self.object._status == "new":
                    if not fsm_check_transition_perm(self.object.submit, request.user): 
                        logger.debug("PermissionDenied")
                        raise Http404()
                    self.object.submit(request.user)
                    self.object.save()
                    messages.add_message(request, messages.SUCCESS, "Your submission has been submitted!")
                else:
                    messages.add_message(request, messages.SUCCESS, self.message)

                return redirect('manuscript_landing', id=self.object.manuscript.id)
        else:
            logger.debug(formset.errors)

        return render(request, self.template, {'form': self.form, 'helper': self.helper, 'read_only': self.read_only, 
            'root_object_title': self.object.manuscript.title, 'repo_dict_gen': self.repo_dict_gen, 's_status':self.object._status, 'parent_id': self.object.manuscript.id,
            'file_delete_url': self.file_delete_url, 'obj_id': self.object.id, "obj_type": self.object_friendly_name, "repo_branch":g.helper_get_submission_branch_name(self.object),
            'parent':self.object, 'children_formset':formset, 'page_header': self.page_header})

class SubmissionEditFilesView(GenericSubmissionFilesMetadataView):
    transition_method_name = 'edit_noop'
    form = f.GitFileFormSet

class SubmissionReadFilesView(GenericSubmissionFilesMetadataView):
    transition_method_name = 'view_noop'
    form = f.GitFileReadOnlyFileFormSet
    read_only = True

#We just leverage the existing form infrastructure for perm checks etc
class SubmissionUploadFilesView(LoginRequiredMixin, GetOrGenerateObjectMixin, TransitionPermissionMixin, GitFilesMixin, GenericCorereObjectView):
    #TODO: Maybe don't need some of these, after creating uploader
    form = f.SubmissionUploadFilesForm
    template = 'main/not_form_upload_files.html'
    transition_method_name = 'edit_noop'
    page_header = "Upload Files for Submission"
    parent_reference_name = 'manuscript'
    parent_id_name = "manuscript_id"
    parent_model = m.Manuscript
    object_friendly_name = 'submission'
    model = m.Submission

    def get(self, request, *args, **kwargs):
        return render(request, self.template, {'form': self.form, 'helper': self.helper, 'read_only': self.read_only, 
            'root_object_title': self.object.manuscript.title, 'repo_dict_gen': list(self.repo_dict_gen), 's_status':self.object._status,
            'file_delete_url': self.file_delete_url, 'file_download_url': self.file_download_url, 'obj_id': self.object.id, "obj_type": self.object_friendly_name, 
            "repo_branch":g.helper_get_submission_branch_name(self.object)#,
            })

    def post(self, request, *args, **kwargs):
        if not self.read_only:
            if request.POST.get('submit_continue'):
                if list(self.repo_dict_gen): #this is hacky because you can only read a generator once.

                    #TODO: Run these async.
                    if(hasattr(self.object.manuscript, 'manuscript_containerinfo')):
                        d.refresh_notebook_stack(self.object.manuscript)
                    else:
                        d.build_manuscript_docker_stack(self.object.manuscript)
                    return redirect('submission_editfiles', id=self.object.id)
                else:
                    self.message = 'You must upload some files to the submission!'
                    messages.add_message(request, messages.ERROR, self.message)

                    return render(request, self.template, {'form': self.form, 'helper': self.helper, 'read_only': self.read_only, 
                        'root_object_title': self.object.manuscript.title, 'repo_dict_gen': [], 's_status':self.object._status,
                        'file_delete_url': self.file_delete_url, 'file_download_url': self.file_download_url, 'obj_id': self.object.id, "obj_type": self.object_friendly_name, 
                        "repo_branch":g.helper_get_submission_branch_name(self.object)
                        })

class SubmissionUploaderView(LoginRequiredMixin, GetOrGenerateObjectMixin, TransitionPermissionMixin, GitFilesMixin, GenericCorereObjectView):
    #TODO: Probably don't need some of these, after creating uploader
    form = f.SubmissionUploadFilesForm
    template = 'main/not_form_upload_files.html'
    transition_method_name = 'edit_noop'
    page_header = "Upload Files for Submission"
    parent_reference_name = 'manuscript'
    parent_id_name = "manuscript_id"
    parent_model = m.Manuscript
    object_friendly_name = 'submission'
    model = m.Submission

    #TODO: Should we making sure these files are safe?
    def post(self, request, *args, **kwargs):
        file = request.FILES.get('file')
        fullRelPath = request.POST.get('fullPath','')
        path = '/'+fullRelPath.rsplit(file.name)[0] #returns '' if fullPath is blank, e.g. file is on root
        if m.GitFile.objects.filter(parent_submission=self.object, path=path, name=file.name):
            return HttpResponse('File already exists', status=409)
        md5 = g.store_submission_file(self.object.manuscript, file, path)
        #Create new GitFile for uploaded submission file
        git_file = m.GitFile()
        #git_file.git_hash = '' #we don't store this currently
        git_file.md5 = md5
        git_file.name = file.name
        git_file.path = path
        git_file.size = file.size
        git_file.parent_submission = self.object
        git_file.save(force_insert=True)

        return HttpResponse(status=200)

class SubmissionDownloadFileView(LoginRequiredMixin, GetOrGenerateObjectMixin, TransitionPermissionMixin, GenericCorereObjectView):
    http_method_names = ['get']
    transition_method_name = 'view_noop'
    model = m.Submission
    parent_model = m.Manuscript
    object_friendly_name = 'submission'

    def get(self, request, *args, **kwargs):
        file_path = request.GET.get('file_path')
        if(not file_path):
            raise Http404()

        return g.download_submission_file(self.object, file_path)

class SubmissionDownloadAllFilesView(LoginRequiredMixin, GetOrGenerateObjectMixin, TransitionPermissionMixin, GenericCorereObjectView):
    http_method_names = ['get']
    transition_method_name = 'view_noop'
    model = m.Submission
    parent_model = m.Manuscript
    object_friendly_name = 'submission'

    def get(self, request, *args, **kwargs):
        return g.download_all_submission_files(self.object)


class SubmissionDeleteFileView(LoginRequiredMixin, GetOrGenerateObjectMixin, TransitionPermissionMixin, GenericCorereObjectView):
    http_method_names = ['post']
    transition_method_name = 'edit_noop'
    model = m.Submission
    parent_model = m.Manuscript
    object_friendly_name = 'submission'

    def post(self, request, *args, **kwargs):
        file_path = request.GET.get('file_path')
        if(not file_path):
            raise Http404()
        g.delete_submission_file(self.object.manuscript, file_path)

        folder_path, file_name = file_path.rsplit('/',1)
        folder_path = folder_path + '/'
        try:
            m.GitFile.objects.get(parent_submission=self.object, path=folder_path, name=file_name).delete()
        except m.GitFile.DoesNotExist:
            logger.warning("While deleting file " + file_path + " on submission " + str(self.object.id) + ", the associated GitFile was not found. This could be due to a previous error during upload.")

        return HttpResponse(status=200)

class SubmissionDeleteAllFilesView(LoginRequiredMixin, GetOrGenerateObjectMixin, TransitionPermissionMixin, GenericCorereObjectView):
    http_method_names = ['post']
    transition_method_name = 'edit_noop'
    model = m.Submission
    parent_model = m.Manuscript
    object_friendly_name = 'submission'

    def post(self, request, *args, **kwargs):
        for b in g.get_submission_files_list(self.object.manuscript):
            g.delete_submission_file(self.object.manuscript, b)

            folder_path, file_name = b.rsplit('/',1)
            folder_path = folder_path + '/'
            try:
                m.GitFile.objects.get(parent_submission=self.object, path=folder_path, name=file_name).delete()
            except m.GitFile.DoesNotExist:
                logger.warning("While deleting file " + b + " using delete all on submission " + str(self.object.id) + ", the associated GitFile was not found. This could be due to a previous error during upload.")

        return HttpResponse(status=200)


#Used for ajax refreshing in EditFiles
class SubmissionFilesListAjaxView(LoginRequiredMixin, GetOrGenerateObjectMixin, TransitionPermissionMixin, GitFilesMixin, GenericCorereObjectView):
    template = 'main/file_list.html'
    transition_method_name = 'edit_noop'
    parent_reference_name = 'manuscript'
    parent_id_name = "manuscript_id"
    parent_model = m.Manuscript
    object_friendly_name = 'submission'
    model = m.Submission

    def get(self, request, *args, **kwargs):
        return render(request, self.template, {'read_only': self.read_only, 
            'root_object_title': self.object.manuscript.title, 'repo_dict_gen': self.repo_dict_gen, 'file_download_url': self.file_download_url, 
            'file_delete_url': self.file_delete_url, 'obj_id': self.object.id, "obj_type": self.object_friendly_name, 'page_header': self.page_header})


#Does not use TransitionPermissionMixin as it does the check internally. Maybe should switch
class SubmissionProgressView(LoginRequiredMixin, GetOrGenerateObjectMixin, GenericCorereObjectView):
    parent_reference_name = 'manuscript'
    parent_id_name = "manuscript_id"
    parent_model = m.Manuscript
    object_friendly_name = 'submission'
    model = m.Submission
    note_formset = f.NoteSubmissionFormset
    note_helper = f.NoteFormSetHelper()

    def post(self, request, *args, **kwargs):
        print("SUBMISSION PROGRESS")
        print(self.__dict__)
        print(request.__dict__)
        try:
            if not fsm_check_transition_perm(self.object.submit, request.user): 
                logger.debug("PermissionDenied")
                raise Http404()
            try:
                self.object.submit(request.user)
                self.object.save()
            except TransitionNotAllowed as e:
                logger.error("TransitionNotAllowed: " + str(e))
                raise
            self.message = 'Your '+self.object_friendly_name + ': ' + str(self.object.id) + ' was handed to the editors for review!'
            messages.add_message(request, messages.SUCCESS, self.message)
        except (TransitionNotAllowed):
            self.message = 'Object '+self.object_friendly_name + ': ' + str(self.object.id) + ' could not be handed to editors, please contact the administrator.'
            messages.add_message(request, messages.ERROR, self.message)
        return redirect('/manuscript/'+str(self.object.manuscript.id))

class SubmissionGenerateReportView(LoginRequiredMixin, GetOrGenerateObjectMixin, GenericCorereObjectView):
    parent_reference_name = 'manuscript'
    parent_id_name = "manuscript_id"
    parent_model = m.Manuscript
    object_friendly_name = 'submission'
    model = m.Submission

    def post(self, request, *args, **kwargs):
        try:
            if not fsm_check_transition_perm(self.object.generate_report, request.user): 
                logger.debug("PermissionDenied")
                raise Http404()
            try:
                self.object.generate_report()
                self.object.save()
            except TransitionNotAllowed as e:
                logger.error("TransitionNotAllowed: " + str(e))
                raise
            self.message = 'Your '+self.object_friendly_name + ': ' + str(self.object.id) + ' was handed to the editors for return!'
            messages.add_message(request, messages.SUCCESS, self.message)
        except (TransitionNotAllowed):
            self.message = 'Object '+self.object_friendly_name + ': ' + str(self.object.id) + ' could not be handed to editors, please contact the administrator.'
            messages.add_message(request, messages.ERROR, self.message)
        return redirect('/manuscript/'+str(self.object.manuscript.id))

class SubmissionReturnView(LoginRequiredMixin, GetOrGenerateObjectMixin, GenericCorereObjectView):
    parent_reference_name = 'manuscript'
    parent_id_name = "manuscript_id"
    parent_model = m.Manuscript
    object_friendly_name = 'submission'
    model = m.Submission

    def post(self, request, *args, **kwargs):
        try:
            if not fsm_check_transition_perm(self.object.return_submission, request.user): 
                logger.debug("PermissionDenied")
                raise Http404()
            try:
                self.object.return_submission()
                self.object.save()
            except TransitionNotAllowed as e:
                logger.error("TransitionNotAllowed: " + str(e))
                raise
            self.message = 'Your '+self.object_friendly_name + ': ' + str(self.object.id) + ' was returned to the authors!'
            messages.add_message(request, messages.SUCCESS, self.message)
        except (TransitionNotAllowed):
            self.message = 'Object '+self.object_friendly_name + ': ' + str(self.object.id) + ' could not be returned to the authors, please contact the administrator.'
            messages.add_message(request, messages.ERROR, self.message)
        return redirect('/manuscript/'+str(self.object.manuscript.id))