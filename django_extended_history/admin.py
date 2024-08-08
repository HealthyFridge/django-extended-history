import base64
import json
from typing import Any

from django.contrib import admin
from django.contrib.admin.models import LogEntry
from django.contrib.admin.utils import construct_change_message
from django.contrib.auth.models import Permission
from django.db import models
from django.db.models import Q
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from json2html import json2html


def safe_pk(pk: Any):
    """Return a representation of the primary key that is safe to be serialized to JSON later."""
    return pk if isinstance(pk, (int, str, bytes, bytearray)) else str(pk)


class DjangoExtendedHistory:
    object_history_template = 'object_history.html'

    # Deprecated in Django 5.1. Keep until end-of-support for Django 4.2 LTS (April 2026)
    def log_deletion(self, request, obj, object_repr):
        """
        Log that an object will be deleted. Note that this method must be
        called before the deletion.

        The default implementation creates an admin LogEntry object.
        """
        from django.contrib.admin.models import DELETION, LogEntry
        from django.contrib.admin.options import get_content_type_for_model
        from django.core import serializers

        data = serializers.serialize("json", [ obj, ])

        return LogEntry.objects.log_action(
            user_id=request.user.pk,
            content_type_id=get_content_type_for_model(obj).pk,
            object_id=obj.pk,
            object_repr=object_repr,
            action_flag=DELETION,
            change_message=data,
        )
        
    def log_deletions(self, request, queryset):
        """
        Log that an object will be deleted. Note that this method must be
        called before the deletion.

        The default implementation creates an admin LogEntry object.
        """
        from django.contrib.admin.models import DELETION, LogEntry
        from django.core import serializers

        for obj in queryset:
            data = serializers.serialize("json", [ obj, ])

            return LogEntry.objects.log_actions(
                user_id=request.user.pk,
                queryset=[obj],
                action_flag=DELETION,
                change_message=data,
                single_object=True,
            )

    def construct_change_message(self, request, form, formsets, add=False):
        # First create the default LogEntry message
        change_message: list[object] = construct_change_message(form, formsets, add)  # type: ignore - returns List[Dict[str, Dict[str, List[str]]]]
        # Now add extra audit details
        change_details = []

        if form.changed_data:
            for field in form.changed_data:
                field_values = {}
                old_values = {}
                new_values = {}
                old_pks = []
                if form.initial and field in form.initial:
                    if form.initial[field] is not None and hasattr(form.fields[field], 'queryset'):
                        if isinstance(form.initial[field], list):
                            # is manytomany
                            old_pks = [item.pk for item in form.initial[field]]
                        else:
                            old_values["pk"] = safe_pk(form.initial[field])
                            old_values["object"] = str(form.fields[field].queryset.filter(pk=old_values["pk"]).first())
                    else:
                        old_values["value"] = str(form.initial[field])
                else:
                    old_values["value"] = None

                if field in form.cleaned_data:  # For instance password field is NOT in cleaned data: password1 and password2 are. Try change password form
                    if isinstance(form.cleaned_data[field], models.query.QuerySet):
                        # is manytomany
                        new_pks = [item.pk for item in form.cleaned_data[field].all()]
                        removed_pks = [item for item in old_pks if item not in new_pks]
                        removed = [({"pk": safe_pk(item.pk), "object": str(item)}) for item in list(form.fields[field].queryset.filter(pk__in=removed_pks).all())]
                        if removed:
                            field_values["removed"] = removed

                        added_pks = [item for item in new_pks if item not in old_pks]
                        added = [({"pk": safe_pk(item.pk), "object": str(item)}) for item in form.cleaned_data[field] if item.pk in added_pks]
                        if added:
                            field_values["added"] = added
                    else:
                        if hasattr(form.cleaned_data[field], 'pk'):
                            new_values["pk"] = safe_pk(form.cleaned_data[field].pk)
                            new_values["object"] = str(form.cleaned_data[field])
                        else:
                            new_values["value"] = str(form.cleaned_data[field])
                        field_values = {"old": old_values, "new": new_values}

                change_details.append({field: field_values})

            change_message.append({"details": change_details})

        if formsets:
            for formset in formsets:
                added_form_list = []
                if formset.new_objects:
                    for added_object in formset.new_objects:
                        added_fields_list = []
                        for field in formset.form.base_fields:
                            new_value = str(added_object.__getattribute__(field))
                            added_fields_list.append({field: {"new": new_value}})
                        added_form_list.append({str(added_object._meta.model_name): str(added_object), "fields": added_fields_list})
                    change_message.append({"added related": added_form_list})

                changed_form_set = []
                if formset.changed_objects:
                    for changed_object, changed_fields in formset.changed_objects:
                        change_form_list = []
                        for form in formset.initial_forms:

                            changed_fields_list = []
                            if form.instance != changed_object:
                                continue

                            for field in changed_fields:
                                if form.initial[field] is not None and hasattr(form.fields[field], 'queryset'):
                                    old_value = str(form.fields[field].queryset.filter(pk=form.initial[field]).first())
                                else:
                                    old_value = str(form.initial[field])
                                new_value = str(form.cleaned_data[field])

                                changed_field_content = {"old": old_value,
                                                         "new": new_value
                                                         }
                                changed_fields_list.append({field: changed_field_content})

                            change_form_list.append({str(changed_object._meta.model_name): str(changed_object), "fields": changed_fields_list})

                        changed_form_set.append(change_form_list)

                    change_message.append({"changed related": changed_form_set})

                deleted_form_set = []
                if formset.deleted_objects:
                    for deleted_object in formset.deleted_objects:
                        deleted_form_list = []
                        for form in formset.initial_forms:

                            deleted_fields_list = []
                            if form.instance != deleted_object:
                                continue

                            for field in form.instance._meta.fields:
                                if not isinstance(field, (models.AutoField, models.BigAutoField, models.SmallAutoField)):
                                    if isinstance(field, models.BinaryField):
                                        old_value = base64.b64encode(getattr(deleted_object, field.name)).decode('utf-8')
                                    else:
                                        old_value = str(getattr(deleted_object, field.name))

                                    deleted_field_content = {"old": old_value}
                                    deleted_fields_list.append({field.name: deleted_field_content})

                            deleted_form_list.append({str(deleted_object._meta.model_name): str(deleted_object), "fields": deleted_fields_list})

                        deleted_form_set.append(deleted_form_list)

                    change_message.append({"deleted related": deleted_form_set})

        return change_message


class LogEntryAdmin(admin.ModelAdmin):
    date_hierarchy = 'action_time'

    list_display = ['action_time', 'user', 'action_flag', 'content_type', 'get_url_to_obj']
    exclude = ['change_message', 'object_id', 'object_repr',]
    list_filter = ['user', 'action_time', 'action_flag']
    readonly_fields = ['action_time', 'user', 'action_flag', 'content_type', 'get_url_to_obj', 'get_change_message']
    search_fields = ['object_repr', 'change_message']

    def get_queryset(self, request):
        queryset = super(LogEntryAdmin, self).get_queryset(request)
        if not request.user.is_superuser:
            # List only those logentries to which to user has permission
            queryset = queryset.filter(Q(content_type__in=Permission.objects.filter(group__user=request.user).values('content_type')) |
                                       Q(content_type__in=Permission.objects.filter(user=request.user).values('content_type')))
        return queryset.prefetch_related('content_type').defer('change_message')

    @admin.display(description=_('change message'))
    def get_change_message(self, request):
        cm: str = request.change_message
        if cm and cm[0] == "[":
            try:
                cm = mark_safe(json2html.convert(json=cm))  # type: ignore
            except json.JSONDecodeError:
                pass
        return cm

    @admin.display(description=_('object repr'))
    def get_url_to_obj(self, request):
        if request.content_type and request.object_id:
            try:
                change_url = reverse(f'admin:{request.content_type.app_label}_{request.content_type.model}_change', args=(request.object_id,))
                return format_html('<a href="{}">{}</a>', change_url, request.object_repr)
            except NoReverseMatch:
                pass
        return request.object_repr

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


admin.site.register(LogEntry, LogEntryAdmin)
