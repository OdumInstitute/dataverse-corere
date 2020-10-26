import logging
import uuid
from . import constants as c
from django.db import models
from django.contrib.auth.models import AbstractUser, Group
from django.contrib.postgres.fields import JSONField
from django.core.serializers.json import DjangoJSONEncoder
from django.core.exceptions import FieldError
from django_fsm import FSMField, transition, RETURN_VALUE, has_transition_perm, TransitionNotAllowed
from django.db.models import Q
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db.models.signals import post_delete
from guardian.shortcuts import get_users_with_perms, assign_perm
from simple_history.models import HistoricalRecords
from simple_history.utils import update_change_reason
from corere.main import constants as c
from corere.main.gitlab import gitlab_create_manuscript_repo, gitlab_create_submissions_repo, gitlab_create_submission_branch, helper_get_submission_branch_name
from corere.main.middleware import local
from corere.main.utils import fsm_check_transition_perm

logger = logging.getLogger(__name__)  
####################################################

class AbstractCreateUpdateModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    creator = models.ForeignKey('User', on_delete=models.SET_NULL, related_name="creator_%(class)ss", blank=True, null=True)
    last_editor = models.ForeignKey('User', on_delete=models.SET_NULL, related_name="last_editor_%(class)ss", blank=True, null=True)

    def save(self, *args, **kwargs):
        if hasattr(local, 'user'):
            if(self.pk is None):
                self.creator = local.user
            else:
                self.last_editor = local.user
        return super(AbstractCreateUpdateModel, self).save(*args, **kwargs)

    class Meta:
        abstract = True

# Adding an additional field to our histories for changes. We populate this after save with a post_save signal        
class AbstractHistoryWithChanges(models.Model):
    history_change_list = models.TextField(blank=False, null=False, default="")

    class Meta:
        abstract = True

####################################################

class User(AbstractUser):
    # This model inherits these fields from abstract user:
    # username, email, first_name, last_name, date_joined and last_login, password, is_superuser, is_staff and is_active

    # See apps.py/signals.py for the instantiation of CoReRe's default User groups/permissions

    invite_key = models.CharField(max_length=64, blank=True) # MAD: Should this be encrypted?
    invited_by = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True)
    gitlab_id = models.IntegerField(blank=True, null=True)
    history = HistoricalRecords(bases=[AbstractHistoryWithChanges,], excluded_fields=['current_manuscript'])
    current_manuscript = models.ForeignKey('Manuscript', null=True, on_delete=models.SET_NULL, related_name="manuscript_current_selecting_users")

    # Django Guardian has_perm does not check whether the user has a global perm.
    # We always want that in our project, so this function checks both
    # See for more info: https://github.com/django/django/pull/9581
    def has_any_perm(self, perm_string, obj):
        return self.has_perm(c.perm_path(perm_string)) or self.has_perm(perm_string, obj)

####################################################

VERIFICATION_NEW = "new"
VERIFICATION_NOT_ATTEMPTED = "not_attempted" # The name of this is vague
VERIFICATION_MINOR_ISSUES = "minor_issues"
VERIFICATION_MAJOR_ISSUES = "major_issues"
VERIFICATION_SUCCESS_W_MOD = "success_w_mod"
VERIFICATION_SUCCESS = "success"

VERIFICATION_RESULT_CHOICES = (
    (VERIFICATION_NEW, 'New'),
    (VERIFICATION_NOT_ATTEMPTED, 'Not Attempted'),
    (VERIFICATION_MINOR_ISSUES, 'Minor Issues'),
    (VERIFICATION_MAJOR_ISSUES, 'Major Issues'),
    (VERIFICATION_SUCCESS_W_MOD, 'Success with Modification'),
    (VERIFICATION_SUCCESS, 'Success'),
)

class Verification(AbstractCreateUpdateModel):
    _status = FSMField(max_length=15, choices=VERIFICATION_RESULT_CHOICES, default=VERIFICATION_NEW)
    software = models.TextField()
    submission = models.OneToOneField('Submission', on_delete=models.CASCADE, related_name='submission_verification')
    history = HistoricalRecords(bases=[AbstractHistoryWithChanges,])

    class Meta:
        default_permissions = ()

    def save(self, *args, **kwargs):
        try:
            if(self.submission._status != SUBMISSION_IN_PROGRESS_VERIFICATION):
                raise FieldError('A verification cannot be added to a submission unless its status is: ' + SUBMISSION_IN_PROGRESS_VERIFICATION)
        except Verification.submission.RelatedObjectDoesNotExist:
            pass #this is caught in super
        super(Verification, self).save(*args, **kwargs)

    ##### django-fsm (workflow) related functions #####

    def can_edit(self):
        if(self.submission._status == SUBMISSION_IN_PROGRESS_VERIFICATION ):
            return True
        return False

    #Does not actually change status, used just for permission checking
    @transition(field=_status, source='*', target=RETURN_VALUE(), conditions=[can_edit],
        permission=lambda instance, user: user.has_any_perm(c.PERM_MANU_VERIFY,instance.submission.manuscript))
    def edit_noop(self):
        return self._status

    #-----------------------

    #Does not actually change status, used just for permission checking
    @transition(field=_status, source='*', target=RETURN_VALUE(), conditions=[],
        permission=lambda instance, user: ((instance.submission._status == SUBMISSION_IN_PROGRESS_VERIFICATION and user.has_any_perm(c.PERM_MANU_VERIFY,instance.submission.manuscript))
                                            or (instance.submission._status != SUBMISSION_IN_PROGRESS_VERIFICATION and user.has_any_perm(c.PERM_MANU_VIEW_M,instance.submission.manuscript))) )
    def view_noop(self):
        return self._status

####################################################

EDITION_NEW = 'new'
EDITION_ISSUES = 'issues'
EDITION_NO_ISSUES = 'no_issues'

EDITION_RESULT_CHOICES = (
    (EDITION_NEW, 'New'),
    (EDITION_ISSUES, 'Issues'),
    (EDITION_NO_ISSUES, 'No Issues'),
)

class Edition(AbstractCreateUpdateModel):
    _status = FSMField(max_length=15, choices=EDITION_RESULT_CHOICES, default=EDITION_NEW)
    submission = models.OneToOneField('Submission', on_delete=models.CASCADE, related_name='submission_edition')
    history = HistoricalRecords(bases=[AbstractHistoryWithChanges,])

    class Meta:
        default_permissions = ()

    def save(self, *args, **kwargs):
        try:
            if(self.submission._status != SUBMISSION_IN_PROGRESS_EDITION):
                raise FieldError('A curation cannot be added to a submission unless its status is: ' + SUBMISSION_IN_PROGRESS_EDITION)
        except Edition.submission.RelatedObjectDoesNotExist:
            pass #this is caught in super
        super(Edition, self).save(*args, **kwargs)

    ##### django-fsm (workflow) related functions #####

    def can_edit(self):
        if(self.submission._status == SUBMISSION_IN_PROGRESS_EDITION ):
            return True
        return False

    #approve_manuscript_submissions
    #Does not actually change status, used just for permission checking
    @transition(field=_status, source='*', target=RETURN_VALUE(), conditions=[can_edit],
        permission=lambda instance, user: user.has_any_perm(c.PERM_MANU_APPROVE,instance.submission.manuscript))
    def edit_noop(self):
        return self._status

    #-----------------------
    
    #Does not actually change status, used just for permission checking
    @transition(field=_status, source='*', target=RETURN_VALUE(), conditions=[],
        permission=lambda instance, user: ((instance.submission._status == SUBMISSION_IN_PROGRESS_EDITION and user.has_any_perm(c.PERM_MANU_APPROVE,instance.submission.manuscript))
                                            or (instance.submission._status != SUBMISSION_IN_PROGRESS_EDITION and user.has_any_perm(c.PERM_MANU_VIEW_M,instance.submission.manuscript))) )
    def view_noop(self):
        return self._status

####################################################

CURATION_NEW = 'new'
CURATION_INCOM_MATERIALS = 'incom_materials'
CURATION_MAJOR_ISSUES = 'major_issues'
CURATION_MINOR_ISSUES = 'minor_issues'
CURATION_NO_ISSUES = 'no_issues'

CURATION_RESULT_CHOICES = (
    (CURATION_NEW, 'New'),
    (CURATION_INCOM_MATERIALS, 'Incomplete Materials'),
    (CURATION_MAJOR_ISSUES, 'Major Issues'),
    (CURATION_MINOR_ISSUES, 'Minor Issues'),
    (CURATION_NO_ISSUES, 'No Issues'),
)

class Curation(AbstractCreateUpdateModel):
    _status = FSMField(max_length=15, choices=CURATION_RESULT_CHOICES, default=CURATION_NEW)
    submission = models.OneToOneField('Submission', on_delete=models.CASCADE, related_name='submission_curation')
    history = HistoricalRecords(bases=[AbstractHistoryWithChanges,])

    class Meta:
        default_permissions = ()

    def save(self, *args, **kwargs):
        try:
            if(self.submission._status != SUBMISSION_IN_PROGRESS_CURATION):
                raise FieldError('A curation cannot be added to a submission unless its status is: ' + SUBMISSION_IN_PROGRESS_CURATION)
        except Curation.submission.RelatedObjectDoesNotExist:
            pass #this is caught in super
        super(Curation, self).save(*args, **kwargs)

    ##### django-fsm (workflow) related functions #####

    def can_edit(self):
        if(self.submission._status == SUBMISSION_IN_PROGRESS_CURATION ):
            return True
        return False

    #Does not actually change status, used just for permission checking
    @transition(field=_status, source='*', target=RETURN_VALUE(), conditions=[can_edit],
        permission=lambda instance, user: user.has_any_perm(c.PERM_MANU_CURATE,instance.submission.manuscript))
    def edit_noop(self):
        return self._status

    #-----------------------
    
    #Does not actually change status, used just for permission checking
    @transition(field=_status, source='*', target=RETURN_VALUE(), conditions=[],
        permission=lambda instance, user: ((instance.submission._status == SUBMISSION_IN_PROGRESS_CURATION and user.has_any_perm(c.PERM_MANU_CURATE,instance.submission.manuscript))
                                            or (instance.submission._status != SUBMISSION_IN_PROGRESS_CURATION and user.has_any_perm(c.PERM_MANU_VIEW_M,instance.submission.manuscript))) )
    def view_noop(self):
        return self._status

####################################################

# Before we were just doing new/submitted as technically you can learn the status of the submission from its attached curation/verification.
# But its much easier to find out if any submissions are in progress this way. Maybe we'll switch back to the single point of truth later.

SUBMISSION_NEW = 'new'
SUBMISSION_IN_PROGRESS_EDITION = 'in_progress_edition'
SUBMISSION_IN_PROGRESS_CURATION = 'in_progress_curation'
SUBMISSION_IN_PROGRESS_VERIFICATION = 'in_progress_verification'
SUBMISSION_REVIEWED_AWAITING_REPORT = 'reviewed_awaiting_report'
SUBMISSION_REVIEWED_REPORT_AWAITING_APPROVAL = 'reviewed_awaiting_approve'
SUBMISSION_RETURNED = 'returned'

SUBMISSION_RESULT_CHOICES = (
    (SUBMISSION_NEW, 'New'),
    (SUBMISSION_IN_PROGRESS_EDITION, 'In Progress - Edition'),
    (SUBMISSION_IN_PROGRESS_CURATION, 'In Progress - Curation'),
    (SUBMISSION_IN_PROGRESS_VERIFICATION, 'In Progress - Verification'),
    (SUBMISSION_REVIEWED_AWAITING_REPORT, 'Reviewed - Awaiting Report'),
    (SUBMISSION_REVIEWED_REPORT_AWAITING_APPROVAL, 'Reviewed - Report Awaiting Approval'),
    (SUBMISSION_RETURNED, 'Returned'),
)

#TODO: We should probably have a "uniqueness" check on the object level for submissions incase two users click submit at the same time.
#Our views do check transition permissions first so it'd have to be at the exact same time.
#May also be needed for curation/verification, otherwise you may end up with one that is an orphan.
class Submission(AbstractCreateUpdateModel):
    #Submission does not have a status in itself, its state is inferred by status of curation/verification/manuscript
    _status = FSMField(max_length=25, choices=SUBMISSION_RESULT_CHOICES, default=SUBMISSION_NEW)
    manuscript = models.ForeignKey('Manuscript', on_delete=models.CASCADE, related_name="manuscript_submissions")
    version = models.IntegerField()
    history = HistoricalRecords(bases=[AbstractHistoryWithChanges,])

    class Meta:
        default_permissions = ()

    def save(self, *args, **kwargs):
        first_save = False
        if not self.pk: #only first save. Nessecary for submission in progress check but also to allow admin editing of submissions
            first_save = True
            try:
                if(Submission.objects.filter(manuscript=self.manuscript).exclude(_status=SUBMISSION_RETURNED).count() > 0):
                    raise FieldError('A submission is already in progress for this manuscript')
                if self.manuscript._status != MANUSCRIPT_AWAITING_INITIAL and self.manuscript._status != MANUSCRIPT_AWAITING_RESUBMISSION:
                    raise FieldError('A submission cannot be created unless a manuscript status is set to await it')
            except Submission.manuscript.RelatedObjectDoesNotExist:
                pass #this is caught in super

            try:
                gitlab_ref_branch = helper_get_submission_branch_name(self.manuscript) #we get the previous branch before saving
            except ValueError:
                gitlab_ref_branch = 'master' #when there are no submissions, we base off master (empty)

            self.version = Submission.objects.filter(manuscript=self.manuscript).count() + 1

        super(Submission, self).save(*args, **kwargs)
        branch = helper_get_submission_branch_name(self.manuscript) #we call the same function after save as now the name should point to what we want for the new branch
        if first_save:
            gitlab_create_submission_branch(self.manuscript, self.manuscript.gitlab_submissions_id, branch, gitlab_ref_branch)

    ##### django-fsm (workflow) related functions #####

    #Does not actually change status, used just for permission checking
    @transition(field=_status, source=SUBMISSION_NEW, target=RETURN_VALUE(), conditions=[],
        permission=lambda instance, user: user.has_any_perm(c.PERM_MANU_ADD_SUBMISSION,instance.manuscript))
    def edit_noop(self):
        return self._status

    #-----------------------

    #Does not actually change status, used just for permission checking
    @transition(field=_status, source='*', target=RETURN_VALUE(), conditions=[],
        permission=lambda instance, user: ((instance._status == SUBMISSION_NEW and user.has_any_perm(c.PERM_MANU_ADD_SUBMISSION,instance.manuscript))
                                            or (instance._status != SUBMISSION_NEW and user.has_any_perm(c.PERM_MANU_VIEW_M,instance.manuscript))) )
    def view_noop(self):
        return self._status

    #-----------------------

    def can_submit(self):
        return True

    @transition(field=_status, source=SUBMISSION_NEW, target=SUBMISSION_IN_PROGRESS_EDITION, on_error=SUBMISSION_NEW, conditions=[can_submit],
                permission=lambda instance, user: user.has_any_perm(c.PERM_MANU_ADD_SUBMISSION,instance.manuscript)) #MAD: Used same perm as add, do we want that?
    def submit(self, user):
        if has_transition_perm(self.manuscript.review, user): #checking here because we need the user
            self.manuscript.review()
            self.manuscript.save()
        else:
            raise Exception
        pass

    #-----------------------

    def can_add_edition(self):
        if(self.manuscript._status != MANUSCRIPT_REVIEWING):   
            return False
        if(Edition.objects.filter(submission=self).count() > 0): #Using a query because its ok if a new object exists but isn't saved
            return False
        return True

    #Does not actually change status, used just for permission checking
    @transition(field=_status, source=[SUBMISSION_IN_PROGRESS_EDITION], target=RETURN_VALUE(), conditions=[can_add_edition],
        permission=lambda instance, user: user.has_any_perm(c.PERM_MANU_APPROVE,instance.manuscript))
    #TODO: We should actually add the edition to the submission via a parameter instead of being a noop. Also do similar in other places
    def add_edition_noop(self):
        return self._status

    #-----------------------

    def can_add_curation(self):
        if(self.manuscript._status != MANUSCRIPT_PROCESSING):   
            return False
        if(Curation.objects.filter(submission=self).count() > 0): #Using a query because its ok if a new object exists but isn't saved
            return False

        return True

    #Does not actually change status, used just for permission checking
    @transition(field=_status, source=[SUBMISSION_IN_PROGRESS_CURATION], target=RETURN_VALUE(), conditions=[can_add_curation],
        permission=lambda instance, user: user.has_any_perm(c.PERM_MANU_CURATE,instance.manuscript))
    #TODO: We should actually add the curation to the submission via a parameter instead of being a noop. Also do similar in other places
    def add_curation_noop(self):
        return self._status

    #-----------------------

    def can_add_verification(self):
        if(self.manuscript._status != MANUSCRIPT_PROCESSING):       
            return False
        try:
            if(self.submission_curation._status != CURATION_NO_ISSUES):
                #print("The curation had issues, so shouldn't be verified")
                return False
        except Submission.submission_curation.RelatedObjectDoesNotExist:
            return False
        if(Verification.objects.filter(submission=self).count() > 0): #Using a query because its ok if a new object exists but isn't saved
            return False
        return True

    #Does not actually change status, used just for permission checking
    @transition(field=_status, source=[SUBMISSION_IN_PROGRESS_VERIFICATION], target=RETURN_VALUE(), conditions=[can_add_verification],
        permission=lambda instance, user: user.has_any_perm(c.PERM_MANU_VERIFY,instance.manuscript))
    def add_verification_noop(self):
        return self._status

    #-----------------------

    #TODO: Change name?
    def can_submit_edition(self):
        #Note, the logic in here is decided whether you can even do a review, not whether its accepted
        try:
            if(self.submission_edition._status == EDITION_NEW):
                return False
        except Submission.submission_edition.RelatedObjectDoesNotExist:
            return False
        return True

    @transition(field=_status, source=[SUBMISSION_IN_PROGRESS_EDITION], target=RETURN_VALUE(), conditions=[can_submit_edition],
                permission=lambda instance, user: ( user.has_any_perm(c.PERM_MANU_APPROVE,instance.manuscript)))
    def submit_edition(self):
        #TODO: Call manuscript.process
        if(self.submission_edition._status == EDITION_NO_ISSUES):
            self.manuscript.process()
            self.manuscript.save()
            return SUBMISSION_IN_PROGRESS_CURATION
        else:
            self.manuscript._status = MANUSCRIPT_AWAITING_RESUBMISSION
            self.manuscript.save()
            return SUBMISSION_RETURNED

    #-----------------------

    def can_review_curation(self):
        try:
            if(self.submission_curation._status == CURATION_NEW):
                return False
        except Submission.submission_curation.RelatedObjectDoesNotExist:
            return False
        return True

    @transition(field=_status, source=[SUBMISSION_IN_PROGRESS_CURATION], target=RETURN_VALUE(), conditions=[can_review_curation],
                permission=lambda instance, user: ( user.has_any_perm(c.PERM_MANU_CURATE,instance.manuscript)))
    def review_curation(self):
        try:
            if(self.submission_curation._status == CURATION_NO_ISSUES):
                return SUBMISSION_IN_PROGRESS_VERIFICATION
        except Submission.submission_curation.RelatedObjectDoesNotExist:
            return SUBMISSION_IN_PROGRESS_CURATION
        
        # self.manuscript._status = MANUSCRIPT_AWAITING_RESUBMISSION
        # self.manuscript.save()
        return SUBMISSION_REVIEWED_AWAITING_REPORT

    #-----------------------

    def can_review_verification(self):
        try:
            if(self.submission_verification._status == VERIFICATION_NEW):
                return False
        except Submission.submission_verification.RelatedObjectDoesNotExist:
            return False
        return True

    @transition(field=_status, source=[SUBMISSION_IN_PROGRESS_VERIFICATION], target=RETURN_VALUE(), conditions=[can_review_verification],
                permission=lambda instance, user: ( user.has_any_perm(c.PERM_MANU_VERIFY,instance.manuscript)))
    def review_verification(self):
        try:
            if(self.submission_verification._status == VERIFICATION_SUCCESS): #just checking the object exists, crude
                pass
            #return SUBMISSION_REVIEWED_AWAITING_REPORT
        except Submission.submission_verification.RelatedObjectDoesNotExist:
            return SUBMISSION_IN_PROGRESS_VERIFICATION
        # self.manuscript._status = MANUSCRIPT_AWAITING_RESUBMISSION
        # self.manuscript.save()
        return SUBMISSION_REVIEWED_AWAITING_REPORT

    #-----------------------
    
    def can_generate_report(self):
        return True

    @transition(field=_status, source=SUBMISSION_REVIEWED_AWAITING_REPORT, target=SUBMISSION_REVIEWED_REPORT_AWAITING_APPROVAL, conditions=[can_generate_report],
            permission=lambda instance, user: ( user.has_any_perm(c.PERM_MANU_CURATE,instance.manuscript)))
    def generate_report(self):
        pass

    #-----------------------
    
    #TODO: It would be better to have this logic as a manuscript transition. 
    # Its somewhat annoying to get to the latest submission from the manuscript, so for now it'll remain here.
    def can_return_submission(self):
        return True

    @transition(field=_status, source=SUBMISSION_REVIEWED_REPORT_AWAITING_APPROVAL, target=SUBMISSION_RETURNED, conditions=[can_return_submission],
            permission=lambda instance, user: ( user.has_any_perm(c.PERM_MANU_APPROVE,instance.manuscript)))
    def return_submission(self):
        if(self.submission_curation._status == CURATION_NO_ISSUES):
            if(self.submission_verification._status == VERIFICATION_SUCCESS):
                self.manuscript._status = MANUSCRIPT_COMPLETED
                ## We decided to leave completed manuscripts in the list and toggle their visibility
                # Delete existing groups when done for clean-up and reporting
                # Group.objects.get(name=c.GROUP_MANUSCRIPT_EDITOR_PREFIX + " " + str(self.manuscript.id)).delete()
                # Group.objects.get(name=c.GROUP_MANUSCRIPT_AUTHOR_PREFIX + " " + str(self.manuscript.id)).delete()
                # Group.objects.get(name=c.GROUP_MANUSCRIPT_VERIFIER_PREFIX + " " + str(self.manuscript.id)).delete()
                # Group.objects.get(name=c.GROUP_MANUSCRIPT_CURATOR_PREFIX + " " + str(self.manuscript.id)).delete()

                self.manuscript.save()
                return
            
        self.manuscript._status = MANUSCRIPT_AWAITING_RESUBMISSION
        self.manuscript.save()
        return

####################################################

#model states
MANUSCRIPT_NEW = 'new' 
MANUSCRIPT_AWAITING_INITIAL = 'awaiting_init'
MANUSCRIPT_AWAITING_RESUBMISSION = 'awaiting_resub'
MANUSCRIPT_REVIEWING = 'reviewing'
MANUSCRIPT_PROCESSING = 'processing'
MANUSCRIPT_COMPLETED = 'completed'

MANUSCRIPT_STATUS_CHOICES = (
    (MANUSCRIPT_NEW, 'New'),
    (MANUSCRIPT_AWAITING_INITIAL, 'Awaiting Initial Submission'),
    (MANUSCRIPT_AWAITING_RESUBMISSION, 'Awaiting Resubmission'),
    (MANUSCRIPT_REVIEWING, 'Reviewing Submission'),
    (MANUSCRIPT_PROCESSING, 'Processing Submission'),
    (MANUSCRIPT_COMPLETED, 'Completed'),
)

class Manuscript(AbstractCreateUpdateModel):
    uuid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False) #currently only used for naming a file folder on upload. Needed as id doesn't exist until after create
    pub_id = models.CharField(max_length=200, default="", db_index=True)
    title = models.TextField(max_length=200, blank=False, null=False, default="")
    doi = models.CharField(max_length=200, default="", db_index=True)
    open_data = models.BooleanField(default=False)
    environment_info = JSONField(encoder=DjangoJSONEncoder, default=list, blank=True, null=True)
    gitlab_submissions_id = models.IntegerField(blank=True, null=True) #Storing the repo for submission files (all submissions)
    gitlab_submissions_path = models.CharField(max_length=255, blank=True, null=True) #Binderhub needs path, not id. 255 is a gitlab requirement
    gitlab_manuscript_id = models.IntegerField(blank=True, null=True) #Storing the repo for manuscript files
    gitlab_manuscript_path = models.CharField(max_length=255, blank=True, null=True) #Not sure we'll ever use this as we only added it for binderhub, but tracking it for completeness
    _status = FSMField(max_length=15, choices=MANUSCRIPT_STATUS_CHOICES, default=MANUSCRIPT_NEW)
    history = HistoricalRecords(bases=[AbstractHistoryWithChanges,])

    def __str__(self):
        return '{0}: {1}'.format(self.id, self.title)

    class Meta:
        permissions = [
            #TODO: This includes default CRUD permissions. We could switch it to be explicit (other objects too)
            (c.PERM_MANU_ADD_AUTHORS, 'Can manage authors on manuscript'),
            (c.PERM_MANU_REMOVE_AUTHORS, 'Can manage authors on manuscript'), # we needed more granularity for authors
            (c.PERM_MANU_MANAGE_EDITORS, 'Can manage editors on manuscript'),
            (c.PERM_MANU_MANAGE_CURATORS, 'Can manage curators on manuscript'),
            (c.PERM_MANU_MANAGE_VERIFIERS, 'Can manage verifiers on manuscript'),
            (c.PERM_MANU_ADD_SUBMISSION, 'Can add submission to manuscript'),
            #('review_submission_on_manuscript', 'Can review submission on manuscript'),
            # We track permissions of objects under the manuscript at the manuscript level, as we don't need to be more granular
            # Technically curation/verification are added to a submission
            (c.PERM_MANU_APPROVE, 'Can review submissions for processing'),
            (c.PERM_MANU_CURATE, 'Can curate manuscript/submission'),
            (c.PERM_MANU_VERIFY, 'Can verify manuscript/submission'),
        ]

    def save(self, *args, **kwargs):
        first_save = False
        if not self.pk:
            first_save = True
        super(Manuscript, self).save(*args, **kwargs)
        if first_save:
            # Note these works alongside global permissions defined in signals.py
            # TODO: Make this concatenation standardized
            group_manuscript_editor, created = Group.objects.get_or_create(name=c.GROUP_MANUSCRIPT_EDITOR_PREFIX + " " + str(self.id))
            assign_perm(c.PERM_MANU_CHANGE_M, group_manuscript_editor, self) 
            assign_perm(c.PERM_MANU_DELETE_M, group_manuscript_editor, self) 
            assign_perm(c.PERM_MANU_VIEW_M, group_manuscript_editor, self) 
            assign_perm(c.PERM_MANU_ADD_AUTHORS, group_manuscript_editor, self) 
            assign_perm(c.PERM_MANU_APPROVE, group_manuscript_editor, self)

            group_manuscript_author, created = Group.objects.get_or_create(name=c.GROUP_MANUSCRIPT_AUTHOR_PREFIX + " " + str(self.id))
            #assign_perm(c.PERM_MANU_CHANGE_M, group_manuscript_author, self)
            assign_perm(c.PERM_MANU_VIEW_M, group_manuscript_author, self) 
            assign_perm(c.PERM_MANU_ADD_AUTHORS, group_manuscript_author, self) 
            assign_perm(c.PERM_MANU_ADD_SUBMISSION, group_manuscript_author, self) 

            group_manuscript_curator, created = Group.objects.get_or_create(name=c.GROUP_MANUSCRIPT_CURATOR_PREFIX + " " + str(self.id))
            assign_perm(c.PERM_MANU_CHANGE_M, group_manuscript_curator, self) 
            assign_perm(c.PERM_MANU_VIEW_M, group_manuscript_curator, self) 
            assign_perm(c.PERM_MANU_CURATE, group_manuscript_curator, self) 

            group_manuscript_verifier, created = Group.objects.get_or_create(name=c.GROUP_MANUSCRIPT_VERIFIER_PREFIX + " " + str(self.id))
            #assign_perm(c.PERM_MANU_CHANGE_M, group_manuscript_verifier, self) 
            assign_perm(c.PERM_MANU_VIEW_M, group_manuscript_verifier, self) 
            assign_perm(c.PERM_MANU_VERIFY, group_manuscript_verifier, self) 

            group_manuscript_editor.user_set.add(local.user) #TODO: Should be dynamic on role or more secure, but right now only editors create manuscripts

            gitlab_create_manuscript_repo(self)
            gitlab_create_submissions_repo(self)

    def is_complete(self):
        return self._status == MANUSCRIPT_COMPLETED
            
    ##### django-fsm (workflow) related functions #####

    #Conditions: Authors needed, files uploaded [NOT DONE]
    def can_begin(self):
        # Are there any authors assigned to the manuscript?
        group_string = name=c.GROUP_MANUSCRIPT_AUTHOR_PREFIX + " " + str(self.id)
        count = User.objects.filter(groups__name=group_string).count()
        if(count < 1):
            return False
        return True

    @transition(field=_status, source=MANUSCRIPT_NEW, target=MANUSCRIPT_AWAITING_INITIAL, conditions=[can_begin],
                permission=lambda instance, user: user.has_any_perm(c.PERM_MANU_CHANGE_M,instance))
    def begin(self):
        pass #Here add any additional actions related to the state change

    #-----------------------

    def can_add_submission(self):
        # Technically we don't need to check 'in_progress' as in that case the manuscript will be processing, but redundancy is ok
        #try:

        if (self.manuscript_submissions.filter(Q(_status=SUBMISSION_NEW)| Q(_status=SUBMISSION_IN_PROGRESS_EDITION)
                                             | Q(_status=SUBMISSION_IN_PROGRESS_CURATION)| Q(_status=SUBMISSION_IN_PROGRESS_VERIFICATION)).count() != 0):
            return False
        return True

    #Does not actually change status, used just for permission checking
    @transition(field=_status, source=[MANUSCRIPT_AWAITING_INITIAL, MANUSCRIPT_AWAITING_RESUBMISSION], target=RETURN_VALUE(), conditions=[can_add_submission],
                permission=lambda instance, user: user.has_any_perm(c.PERM_MANU_ADD_SUBMISSION,instance))
    def add_submission_noop(self):
        return self._status

    #-----------------------

    #Conditions: Submission with status of new
    def can_review(self):
        if(self.manuscript_submissions.filter(_status=SUBMISSION_NEW).count() != 1):
            return False
        return True

    # Perm: ability to create/edit a submission
    @transition(field=_status, source=[MANUSCRIPT_AWAITING_INITIAL, MANUSCRIPT_AWAITING_RESUBMISSION], target=MANUSCRIPT_REVIEWING, conditions=[can_review],
                permission=lambda instance, user: user.has_any_perm(c.PERM_MANU_ADD_SUBMISSION,instance))
    def review(self):
        #update submission status here?
        pass #Here add any additional actions related to the state change

    #-----------------------

    #Conditions: Submission with status of new
    #TODO: Add the above? Not sure if its still needed
    def can_process(self):
        return True

    # Perm: ability to create/edit a submission
    @transition(field=_status, source=[MANUSCRIPT_REVIEWING], target=MANUSCRIPT_PROCESSING, conditions=[can_process],
                permission=lambda instance, user: user.has_any_perm(c.PERM_MANU_APPROVE, instance))
    def process(self):
        #update submission status here?
        pass #Here add any additional actions related to the state change

    #-----------------------

    #Does not actually change status, used just for permission checking
    @transition(field=_status, source=[MANUSCRIPT_NEW, MANUSCRIPT_AWAITING_INITIAL, MANUSCRIPT_AWAITING_RESUBMISSION], target=RETURN_VALUE(), conditions=[],
        permission=lambda instance, user: user.has_any_perm(c.PERM_MANU_CHANGE_M,instance))
    def edit_noop(self):
        return self._status

    #-----------------------

    #TODO: Address limitation with having only one manuscript version. If its being edited as part of a resubmission what do we do about viewing?
    #      Do we just allow viewing whatever is there? Do we block it during certain states. Maybe it'll be less of an issue if we can put all versioned data in the submission?
    #
    #Does not actually change status, used just for permission checking
    @transition(field=_status, source='*', target=RETURN_VALUE(), conditions=[],
        permission=lambda instance, user: (#(instance._status == MANUSCRIPT_NEW and user.has_any_perm(c.PERM_MANU_ADD_M,instance.manuscript)) or #add_manuscript means any other editor can see it (even from a different pub...)
                                            (instance._status != MANUSCRIPT_NEW and user.has_any_perm(c.PERM_MANU_VIEW_M,instance))) )
    def view_noop(self):
        return self._status

@receiver(post_delete, sender=Manuscript, dispatch_uid='manuscript_delete_groups_signal')
def delete_manuscript_groups(sender, instance, using, **kwargs):
    Group.objects.get(name=c.GROUP_MANUSCRIPT_EDITOR_PREFIX + " " + str(instance.id)).delete()
    Group.objects.get(name=c.GROUP_MANUSCRIPT_AUTHOR_PREFIX + " " + str(instance.id)).delete()
    Group.objects.get(name=c.GROUP_MANUSCRIPT_CURATOR_PREFIX + " " + str(instance.id)).delete()
    Group.objects.get(name=c.GROUP_MANUSCRIPT_VERIFIER_PREFIX + " " + str(instance.id)).delete()


####################################################

FILE_TAG_CODE = 'code'
FILE_TAG_DATA = 'data'
FILE_TAG_DOCUMENTATION = 'documentation'

FILE_TAG_CHOICES = (
    (FILE_TAG_CODE, 'code'),
    (FILE_TAG_DATA, 'data'),
    (FILE_TAG_DOCUMENTATION, 'documentation'),
)

class GitlabFile(AbstractCreateUpdateModel):
    gitlab_blob_id = models.CharField(max_length=40) # SHA-1 hash of a blob or subtree with its associated mode, type, and filename. 
    gitlab_sha256 = models.CharField(max_length=64) #, default="", )
    gitlab_path = models.TextField(max_length=4096, blank=True, null=True)
    gitlab_date = models.DateTimeField()
    gitlab_size = models.IntegerField()
    tag = models.CharField(max_length=14, choices=FILE_TAG_CHOICES) 
    description = models.TextField(max_length=1024, default="")

    #linked = models.BooleanField(default=True)
    parent_submission = models.ForeignKey(Submission, null=True, blank=True, on_delete=models.CASCADE, related_name='file_submission')
    parent_manuscript = models.ForeignKey(Manuscript, null=True, blank=True, on_delete=models.CASCADE, related_name='file_manuscript')

    class Meta:
        indexes = [
            models.Index(fields=['gitlab_blob_id', 'parent_submission']), #one index for the combination of the two fields #TODO: May be unneeded
            models.Index(fields=['gitlab_path', 'parent_submission']), #one index for the combination of the two fields
        ]

    @property
    def parent(self):
        if self.parent_submission_id is not None:
            return self.parent_submission
        if self.parent_manuscript_id is not None:
            return self.parent_submission
        raise AssertionError("Neither 'parent_submission', 'parent_edition', 'parent_curation', 'parent_verification' or 'parent_file' is set")

    def save(self, *args, **kwargs):
        parents = 0
        parents += (self.parent_submission_id is not None)
        parents += (self.parent_manuscript_id is not None)
        if(parents > 1):
            raise AssertionError("Multiple parents set")

        super(GitlabFile, self).save(*args, **kwargs)

#Note: If you add required fields here or in the form, you'll need to disable them. See unused_code.py
class Note(AbstractCreateUpdateModel):
    text = models.TextField(default="", blank=True)
    history = HistoricalRecords(bases=[AbstractHistoryWithChanges,])
    note_replied_to = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='note_responses')

    parent_submission = models.ForeignKey(Submission, null=True, blank=True, on_delete=models.CASCADE, related_name='notes')
    parent_edition = models.ForeignKey(Edition, null=True, blank=True, on_delete=models.CASCADE, related_name='notes')
    parent_curation = models.ForeignKey(Curation, null=True, blank=True, on_delete=models.CASCADE, related_name='notes')
    parent_verification = models.ForeignKey(Verification, null=True, blank=True, on_delete=models.CASCADE, related_name='notes')
    parent_file = models.ForeignKey(GitlabFile, null=True, blank=True, on_delete=models.CASCADE, related_name='notes')

    #note this is not a "parent" relationship like above
    manuscript = models.ForeignKey(Manuscript, on_delete=models.CASCADE)

    @property
    def parent(self):
        if self.parent_submission_id is not None:
            return self.parent_submission
        if self.parent_edition_id is not None:
            return self.parent_edition
        if self.parent_curation_id is not None:
            return self.parent_curation
        if self.parent_verification_id is not None:
            return self.parent_verification
        if self.parent_file_id is not None:
            return self.parent_file
        raise AssertionError("Neither 'parent_submission', 'parent_edition', 'parent_curation', 'parent_verification' or 'parent_file' is set")
    
    def save(self, *args, **kwargs):
        parents = 0
        parents += (self.parent_submission_id is not None)
        parents += (self.parent_edition_id is not None)
        parents += (self.parent_curation_id is not None)
        parents += (self.parent_verification_id is not None)
        parents += (self.parent_file_id is not None)
        if(parents > 1):
            raise AssertionError("Multiple parents set")

        first_save = False
        if not self.pk:
            first_save = True
            if(self.manuscript_id is None):
                if self.parent_submission_id is not None:
                    self.manuscript = self.parent_submission.manuscript
                elif self.parent_file_id is not None:
                    self.manuscript = self.parent_file.parent_submission.manuscript
                else:
                    self.manuscript = self.parent.submission.manuscript
        super(Note, self).save(*args, **kwargs)
        if first_save and local.user != None and local.user.is_authenticated: #maybe redundant
            assign_perm(c.PERM_NOTE_VIEW_N, local.user, self) 
            assign_perm(c.PERM_NOTE_CHANGE_N, local.user, self) 
            assign_perm(c.PERM_NOTE_DELETE_N, local.user, self) 

    #TODO: If implementing fsm can_edit, base it upon the creator of the note

############### POST-SAVE ################

# post-save signal to update history with list of fields changed
@receiver(post_save, sender=Manuscript, dispatch_uid="add_history_info_manuscript")
@receiver(post_save, sender=User, dispatch_uid="add_history_info_user")
@receiver(post_save, sender=Submission, dispatch_uid="add_history_info_submission")
@receiver(post_save, sender=Curation, dispatch_uid="add_history_info_curation")
@receiver(post_save, sender=Verification, dispatch_uid="add_history_info_verification")
@receiver(post_save, sender=Note, dispatch_uid="add_history_info_note")
def add_history_info(sender, instance, **kwargs):
    try:
        new_record, old_record = instance.history.order_by('-history_date')[:2]
        delta = new_record.diff_against(old_record)
        new_record.history_change_list = str(delta.changed_fields)
        new_record.save()
    except ValueError:
        pass #On new object creation there are not 2 records to do a history diff on.
