import base64
import json
from unittest import mock

import django
from django.conf import settings as _django_settings

if not _django_settings.configured:
    _django_settings.configure(
        SECRET_KEY='test-secret-key-not-for-production',
        DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
        INSTALLED_APPS=[
            'django.contrib.admin',
            'django.contrib.auth',
            'django.contrib.contenttypes',
            'django.contrib.messages',
            'django.contrib.sessions',
            'django_extended_history',
        ],
        MIDDLEWARE=[
            'django.contrib.sessions.middleware.SessionMiddleware',
            'django.contrib.auth.middleware.AuthenticationMiddleware',
            'django.contrib.messages.middleware.MessageMiddleware',
        ],
        TEMPLATES=[{
            'BACKEND': 'django.template.backends.django.DjangoTemplates',
            'DIRS': [],
            'APP_DIRS': True,
            'OPTIONS': {'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ]},
        }],
        DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
        ROOT_URLCONF='django_extended_history._admin_urls',
    )
    django.setup()

from django.contrib.admin.models import CHANGE, DELETION, LogEntry
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User, Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import models
from django.test import TestCase, RequestFactory
from django.urls import reverse, NoReverseMatch

from django_extended_history.admin import DjangoExtendedHistory, LogEntryAdmin, safe_pk, _resolve_old_value


def make_request_with_messages(factory, user):
    """Return a GET request with messages middleware stubbed out."""
    request = factory.get('/')
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _text_field_mock():
    """Return a mock form field suitable for plain-text inputs."""
    field = mock.MagicMock()
    field.label = "Field"
    field.widget.input_type = "text"
    # No queryset attribute so hasattr check returns False
    del field.queryset
    return field


def _password_field_mock():
    field = mock.MagicMock()
    field.label = "Password"
    field.widget.input_type = "password"
    del field.queryset
    return field


# ---------------------------------------------------------------------------
# safe_pk
# ---------------------------------------------------------------------------

class TestResolveOldValue(TestCase):

    def test_none_returns_string_none(self):
        field = mock.MagicMock()
        self.assertEqual(_resolve_old_value(field, None), "None")

    def test_plain_field_returns_str(self):
        field = mock.MagicMock()
        del field.queryset
        self.assertEqual(_resolve_old_value(field, "hello"), "hello")

    def test_fk_field_returns_str_of_queryset_result(self):
        field = mock.MagicMock()
        field.queryset.filter.return_value.first.return_value = "Some Object"
        result = _resolve_old_value(field, 42)
        field.queryset.filter.assert_called_once_with(pk=42)
        self.assertEqual(result, "Some Object")

    def test_m2m_field_returns_list_of_dicts(self):
        field = mock.MagicMock()
        item1 = mock.MagicMock()
        item1.pk = 1
        item1.__str__ = lambda self: "Item 1"
        item2 = mock.MagicMock()
        item2.pk = 2
        item2.__str__ = lambda self: "Item 2"
        result = _resolve_old_value(field, [item1, item2])
        self.assertEqual(result, [{"pk": 1, "object": "Item 1"}, {"pk": 2, "object": "Item 2"}])

    def test_m2m_empty_list_returns_empty_list(self):
        field = mock.MagicMock()
        self.assertEqual(_resolve_old_value(field, []), [])


class TestSafePk(TestCase):

    def test_int(self):
        self.assertEqual(safe_pk(42), 42)

    def test_string(self):
        self.assertEqual(safe_pk("pk-value"), "pk-value")

    def test_bytes(self):
        self.assertEqual(safe_pk(b'test'), b'test')

    def test_bytearray(self):
        ba = bytearray(b'test')
        self.assertEqual(safe_pk(ba), ba)

    def test_complex_object_converted_to_str(self):
        class ComplexPk:
            def __str__(self):
                return "complex-pk"
        self.assertEqual(safe_pk(ComplexPk()), "complex-pk")


# ---------------------------------------------------------------------------
# DjangoExtendedHistory
# ---------------------------------------------------------------------------

class TestDjangoExtendedHistory(TestCase):

    def setUp(self):
        self.history = DjangoExtendedHistory()
        self.factory = RequestFactory()
        self.user = User.objects.create_superuser(
            username='testadmin', email='admin@example.com', password='password'
        )
        self.test_user = User.objects.create(username='testuser', email='user@example.com')
        self.request = make_request_with_messages(self.factory, self.user)

    def tearDown(self):
        warning_messages = [
            str(m) for m in self.request._messages
            if "Extended logging failed" in str(m)
        ]
        self.assertFalse(
            warning_messages,
            f"construct_change_message silently swallowed an exception: {warning_messages}"
        )

    # --- log_deletion ---

    def test_log_deletion_creates_log_entry(self):
        log_entry = self.history.log_deletion(self.request, self.test_user, str(self.test_user))
        self.assertIsNotNone(log_entry)
        self.assertEqual(log_entry.user_id, self.user.pk)
        self.assertEqual(log_entry.action_flag, DELETION)
        self.assertEqual(log_entry.object_id, str(self.test_user.pk))

    def test_log_deletion_change_message_contains_serialized_object(self):
        log_entry = self.history.log_deletion(self.request, self.test_user, str(self.test_user))
        self.assertTrue(log_entry.change_message.startswith('[{"model":'))

    def test_log_deletion_object_repr(self):
        log_entry = self.history.log_deletion(self.request, self.test_user, "My Repr")
        self.assertEqual(log_entry.object_repr, "My Repr")

    # --- log_deletions ---

    def test_log_deletions_creates_entry_per_object(self):
        second = User.objects.create(username='anotheruser', email='another@example.com')
        results = self.history.log_deletions(self.request, [self.test_user, second])
        self.assertEqual(len(results), 2)

    def test_log_deletions_single_object(self):
        results = self.history.log_deletions(self.request, [self.test_user])
        self.assertEqual(len(results), 1)

    # --- construct_change_message: no changes ---

    def test_construct_change_message_no_changes(self):
        mock_form = mock.MagicMock()
        mock_form.changed_data = []
        result = self.history.construct_change_message(self.request, mock_form, None)
        self.assertIsInstance(result, list)
        self.assertFalse(any("details" in item for item in result))

    # --- construct_change_message: plain text field ---

    def test_construct_change_message_text_field(self):
        mock_form = mock.MagicMock()
        mock_form.changed_data = ["username"]
        mock_form.initial = {"username": "oldname"}
        mock_form.cleaned_data = {"username": "newname"}
        mock_form.fields = {"username": _text_field_mock()}

        result = self.history.construct_change_message(self.request, mock_form, None)

        details_items = [item for item in result if "details" in item]
        self.assertEqual(len(details_items), 1)
        field_entry = details_items[0]["details"][0]["username"]
        self.assertEqual(field_entry["old"]["value"], "oldname")
        self.assertEqual(field_entry["new"]["value"], "newname")

    def test_construct_change_message_text_field_old_none(self):
        """When initial value is None (non-FK), it is str()-converted and stored."""
        mock_form = mock.MagicMock()
        mock_form.changed_data = ["username"]
        mock_form.initial = {"username": None}
        mock_form.cleaned_data = {"username": "newname"}
        mock_form.fields = {"username": _text_field_mock()}

        result = self.history.construct_change_message(self.request, mock_form, None)

        details_items = [item for item in result if "details" in item]
        field_entry = details_items[0]["details"][0]["username"]
        self.assertEqual(field_entry["old"]["value"], "None")

    # --- construct_change_message: password field masked ---

    def test_construct_change_message_password_field_masked(self):
        mock_form = mock.MagicMock()
        mock_form.changed_data = ["password"]
        mock_form.initial = {"password": "old_hash"}
        mock_form.cleaned_data = {"password": "newvalue"}
        mock_form.fields = {"password": _password_field_mock()}

        result = self.history.construct_change_message(self.request, mock_form, None)

        details_items = [item for item in result if "details" in item]
        self.assertEqual(len(details_items), 1)
        field_entry = details_items[0]["details"][0]["password"]
        self.assertEqual(field_entry["old"]["value"], "*****")
        self.assertEqual(field_entry["new"]["value"], "*****")

    # --- construct_change_message: add=True omits old values ---

    def test_construct_change_message_add_mode_no_old_values(self):
        mock_form = mock.MagicMock()
        mock_form.changed_data = ["username"]
        mock_form.initial = {}
        mock_form.cleaned_data = {"username": "newname"}
        mock_form.fields = {"username": _text_field_mock()}

        result = self.history.construct_change_message(self.request, mock_form, None, add=True)

        details_items = [item for item in result if "details" in item]
        self.assertEqual(len(details_items), 1)
        field_entry = details_items[0]["details"][0]["username"]
        self.assertNotIn("old", field_entry)
        self.assertIn("new", field_entry)

    # --- construct_change_message: FK field ---

    def test_construct_change_message_fk_field(self):
        mock_form = mock.MagicMock()
        mock_form.changed_data = ["group"]
        mock_form.initial = {"group": 1}

        new_obj = mock.MagicMock()
        new_obj.pk = 2
        new_obj.__str__ = lambda self: "Group B"
        mock_form.cleaned_data = {"group": new_obj}

        mock_field = mock.MagicMock()
        mock_field.label = "Group"
        mock_field.queryset.filter.return_value.first.return_value = mock.MagicMock(
            __str__=lambda self: "Group A"
        )
        mock_form.fields = {"group": mock_field}

        result = self.history.construct_change_message(self.request, mock_form, None)

        details_items = [item for item in result if "details" in item]
        self.assertEqual(len(details_items), 1)
        field_entry = details_items[0]["details"][0]["group"]
        self.assertEqual(field_entry["old"]["pk"], 1)
        self.assertEqual(field_entry["new"]["pk"], 2)

    # --- construct_change_message: ManyToMany field ---

    def test_construct_change_message_m2m_field(self):
        g1 = Group.objects.create(name="Group 1")
        g2 = Group.objects.create(name="Group 2")
        g3 = Group.objects.create(name="Group 3")

        mock_form = mock.MagicMock()
        mock_form.changed_data = ["groups"]
        # old had g1 and g2
        mock_form.initial = {"groups": [g1, g2]}
        # new has g2 and g3 (removed g1, added g3)
        new_qs = Group.objects.filter(pk__in=[g2.pk, g3.pk])
        mock_form.cleaned_data = {"groups": new_qs}

        mock_field = mock.MagicMock()
        mock_field.label = "Groups"
        mock_field.queryset.query.model = Group
        mock_form.fields = {"groups": mock_field}

        result = self.history.construct_change_message(self.request, mock_form, None)

        details_items = [item for item in result if "details" in item]
        self.assertEqual(len(details_items), 1)
        field_entry = details_items[0]["details"][0]["groups"]
        self.assertIn("removed", field_entry)
        self.assertIn("added", field_entry)
        removed_pks = [r["pk"] for r in field_entry["removed"]]
        added_pks = [a["pk"] for a in field_entry["added"]]
        self.assertIn(g1.pk, removed_pks)
        self.assertIn(g3.pk, added_pks)
        self.assertNotIn(g2.pk, removed_pks)
        self.assertNotIn(g2.pk, added_pks)

    def test_construct_change_message_m2m_no_removed(self):
        """Only additions, no removals."""
        g1 = Group.objects.create(name="Group A")
        g2 = Group.objects.create(name="Group B")

        mock_form = mock.MagicMock()
        mock_form.changed_data = ["groups"]
        mock_form.initial = {"groups": [g1]}
        mock_form.cleaned_data = {"groups": Group.objects.filter(pk__in=[g1.pk, g2.pk])}

        mock_field = mock.MagicMock()
        mock_field.label = "Groups"
        mock_field.queryset.query.model = Group
        mock_form.fields = {"groups": mock_field}

        result = self.history.construct_change_message(self.request, mock_form, None)

        details_items = [item for item in result if "details" in item]
        field_entry = details_items[0]["details"][0]["groups"]
        self.assertNotIn("removed", field_entry)
        self.assertIn("added", field_entry)

    # --- construct_change_message: formset added objects ---

    def test_construct_change_message_formset_added(self):
        mock_form = mock.MagicMock()
        mock_form.changed_data = []

        added_obj = mock.MagicMock()
        added_obj._meta.model_name = "profile"
        added_obj.__str__ = lambda self: "Profile 1"
        added_obj.bio = "Hello"

        mock_formset = mock.MagicMock()
        mock_formset.new_objects = [added_obj]
        mock_formset.changed_objects = []
        mock_formset.deleted_objects = []
        mock_formset.form.base_fields = ["bio"]

        result = self.history.construct_change_message(self.request, mock_form, [mock_formset])

        added_items = [item for item in result if "added related" in item]
        self.assertEqual(len(added_items), 1)
        self.assertEqual(len(added_items[0]["added related"]), 1)
        self.assertEqual(added_items[0]["added related"][0]["profile"], "Profile 1")

    def test_construct_change_message_formset_added_multiple(self):
        mock_form = mock.MagicMock()
        mock_form.changed_data = []

        def make_obj(name):
            obj = mock.MagicMock()
            obj._meta.model_name = "item"
            obj.__str__ = lambda self: name
            return obj

        mock_formset = mock.MagicMock()
        mock_formset.new_objects = [make_obj("A"), make_obj("B")]
        mock_formset.changed_objects = []
        mock_formset.deleted_objects = []
        mock_formset.form.base_fields = ["name"]

        result = self.history.construct_change_message(self.request, mock_form, [mock_formset])

        added_items = [item for item in result if "added related" in item]
        self.assertEqual(len(added_items[0]["added related"]), 2)

    # --- construct_change_message: formset changed objects ---

    def test_construct_change_message_formset_changed(self):
        mock_form = mock.MagicMock()
        mock_form.changed_data = []

        changed_obj = mock.MagicMock()
        changed_obj._meta.model_name = "profile"
        changed_obj.__str__ = lambda self: "Profile 1"

        mock_inner_form = mock.MagicMock()
        mock_inner_form.instance = changed_obj
        mock_inner_form.initial = {"bio": "Old bio"}
        mock_inner_form.cleaned_data = {"bio": "New bio"}

        # Use a field with no queryset attribute so the hasattr check is False
        bio_field = mock.MagicMock(spec=['label'])
        bio_field.label = "Bio"
        mock_inner_form.fields = {"bio": bio_field}

        mock_formset = mock.MagicMock()
        mock_formset.new_objects = []
        mock_formset.changed_objects = [(changed_obj, ["bio"])]
        mock_formset.deleted_objects = []
        mock_formset.initial_forms = [mock_inner_form]

        result = self.history.construct_change_message(self.request, mock_form, [mock_formset])

        changed_items = [item for item in result if "changed related" in item]
        self.assertEqual(len(changed_items), 1)
        object_changes = changed_items[0]["changed related"][0][0]
        self.assertEqual(object_changes["profile"], "Profile 1")
        field_change = object_changes["fields"][0]["bio"]
        self.assertEqual(field_change["old"], "Old bio")
        self.assertEqual(field_change["new"], "New bio")

    def test_construct_change_message_formset_changed_fk_field(self):
        """Formset changed object with a FK field uses queryset lookup for old value."""
        mock_form = mock.MagicMock()
        mock_form.changed_data = []

        changed_obj = mock.MagicMock()
        changed_obj._meta.model_name = "item"
        changed_obj.__str__ = lambda self: "Item 1"

        mock_inner_form = mock.MagicMock()
        mock_inner_form.instance = changed_obj
        mock_inner_form.initial = {"category": 5}
        mock_inner_form.cleaned_data = {"category": "New Category"}

        fk_field = mock.MagicMock()
        fk_field.label = "Category"
        fk_field.queryset.filter.return_value.first.return_value = "Old Category"
        mock_inner_form.fields = {"category": fk_field}

        mock_formset = mock.MagicMock()
        mock_formset.new_objects = []
        mock_formset.changed_objects = [(changed_obj, ["category"])]
        mock_formset.deleted_objects = []
        mock_formset.initial_forms = [mock_inner_form]

        result = self.history.construct_change_message(self.request, mock_form, [mock_formset])

        changed_items = [item for item in result if "changed related" in item]
        self.assertEqual(len(changed_items), 1)
        field_change = changed_items[0]["changed related"][0][0]["fields"][0]["category"]
        self.assertEqual(field_change["old"], "Old Category")

    def test_construct_change_message_formset_changed_m2m_field(self):
        """Formset changed object with an M2M field (initial is a list) records removed/added entries."""
        mock_form = mock.MagicMock()
        mock_form.changed_data = []

        changed_obj = mock.MagicMock()
        changed_obj._meta.model_name = "item"
        changed_obj.__str__ = lambda self: "Item 1"

        tag1 = mock.MagicMock()
        tag1.pk = 1
        tag1.__str__ = lambda self: "Tag 1"

        tag2 = mock.MagicMock()
        tag2.pk = 2
        tag2.__str__ = lambda self: "Tag 2"

        tag3 = mock.MagicMock()
        tag3.pk = 3
        tag3.__str__ = lambda self: "Tag 3"

        mock_inner_form = mock.MagicMock()
        mock_inner_form.instance = changed_obj
        # Initial state: tag1 and tag2 selected (list = M2M)
        mock_inner_form.initial = {"tags": [tag1, tag2]}
        # New state: tag2 and tag3 selected — tag1 removed, tag3 added
        mock_inner_form.cleaned_data = {"tags": mock.MagicMock()}
        mock_inner_form.cleaned_data["tags"].__iter__ = mock.Mock(return_value=iter([tag2, tag3]))

        m2m_field = mock.MagicMock()
        mock_inner_form.fields = {"tags": m2m_field}

        mock_formset = mock.MagicMock()
        mock_formset.new_objects = []
        mock_formset.changed_objects = [(changed_obj, ["tags"])]
        mock_formset.deleted_objects = []
        mock_formset.initial_forms = [mock_inner_form]

        result = self.history.construct_change_message(self.request, mock_form, [mock_formset])

        changed_items = [item for item in result if "changed related" in item]
        self.assertEqual(len(changed_items), 1)
        field_change = changed_items[0]["changed related"][0][0]["fields"][0]["tags"]
        self.assertIn("removed", field_change)
        self.assertIn("added", field_change)
        removed_pks = [e["pk"] for e in field_change["removed"]]
        added_pks = [e["pk"] for e in field_change["added"]]
        self.assertIn(1, removed_pks)
        self.assertNotIn(2, removed_pks)
        self.assertIn(3, added_pks)

    # --- construct_change_message: formset deleted objects ---

    def test_construct_change_message_formset_deleted(self):
        mock_form = mock.MagicMock()
        mock_form.changed_data = []

        deleted_obj = mock.MagicMock()
        deleted_obj._meta.model_name = "profile"
        deleted_obj.__str__ = lambda self: "Profile 1"
        deleted_obj.bio = "Some text"

        char_field = mock.MagicMock(spec=models.CharField)
        char_field.name = "bio"
        char_field.__class__ = models.CharField
        deleted_obj._meta.fields = [char_field]

        mock_inner_form = mock.MagicMock()
        mock_inner_form.instance = deleted_obj

        mock_formset = mock.MagicMock()
        mock_formset.new_objects = []
        mock_formset.changed_objects = []
        mock_formset.deleted_objects = [deleted_obj]
        mock_formset.initial_forms = [mock_inner_form]

        result = self.history.construct_change_message(self.request, mock_form, [mock_formset])

        deleted_items = [item for item in result if "deleted related" in item]
        self.assertEqual(len(deleted_items), 1)
        self.assertEqual(len(deleted_items[0]["deleted related"]), 1)

    def test_construct_change_message_formset_deleted_binary_field_base64_encoded(self):
        mock_form = mock.MagicMock()
        mock_form.changed_data = []

        deleted_obj = mock.MagicMock()
        deleted_obj._meta.model_name = "profile"
        deleted_obj.__str__ = lambda self: "Profile 1"
        deleted_obj.data = b'binarydata'

        bin_field = mock.MagicMock()
        bin_field.name = "data"
        bin_field.__class__ = models.BinaryField
        deleted_obj._meta.fields = [bin_field]

        mock_inner_form = mock.MagicMock()
        mock_inner_form.instance = deleted_obj

        mock_formset = mock.MagicMock()
        mock_formset.new_objects = []
        mock_formset.changed_objects = []
        mock_formset.deleted_objects = [deleted_obj]
        mock_formset.initial_forms = [mock_inner_form]

        result = self.history.construct_change_message(self.request, mock_form, [mock_formset])

        deleted_items = [item for item in result if "deleted related" in item]
        field_value = deleted_items[0]["deleted related"][0][0]["fields"][0]["data"]["old"]
        self.assertEqual(field_value, base64.b64encode(b'binarydata').decode('utf-8'))

    def test_construct_change_message_formset_deleted_auto_fields_excluded(self):
        """AutoField, BigAutoField, SmallAutoField should not appear in deleted fields."""
        mock_form = mock.MagicMock()
        mock_form.changed_data = []

        deleted_obj = mock.MagicMock()
        deleted_obj._meta.model_name = "item"
        deleted_obj.__str__ = lambda self: "Item"

        auto_field = mock.MagicMock()
        auto_field.name = "id"
        auto_field.__class__ = models.AutoField

        char_field = mock.MagicMock()
        char_field.name = "name"
        char_field.__class__ = models.CharField
        deleted_obj.name = "Value"

        deleted_obj._meta.fields = [auto_field, char_field]

        mock_inner_form = mock.MagicMock()
        mock_inner_form.instance = deleted_obj

        mock_formset = mock.MagicMock()
        mock_formset.new_objects = []
        mock_formset.changed_objects = []
        mock_formset.deleted_objects = [deleted_obj]
        mock_formset.initial_forms = [mock_inner_form]

        result = self.history.construct_change_message(self.request, mock_form, [mock_formset])

        deleted_items = [item for item in result if "deleted related" in item]
        field_names = [list(f.keys())[0] for f in deleted_items[0]["deleted related"][0][0]["fields"]]
        self.assertNotIn("id", field_names)
        self.assertIn("name", field_names)

    # --- construct_change_message: exception swallowed ---

    def test_construct_change_message_exception_swallowed(self):
        """Exceptions in the extended logging are caught; a base message is still returned."""
        mock_form = mock.MagicMock()
        mock_form.changed_data = ["field"]
        mock_form.initial = {"field": "old"}
        # Trigger the exception by making cleaned_data raise on __contains__
        mock_form.cleaned_data = mock.MagicMock()
        mock_form.cleaned_data.__contains__ = mock.Mock(side_effect=RuntimeError("boom"))

        with mock.patch('django_extended_history.admin.messages') as mock_messages:
            result = self.history.construct_change_message(self.request, mock_form, None)
            mock_messages.warning.assert_called_once()

        self.assertIsInstance(result, list)


# ---------------------------------------------------------------------------
# LogEntryAdmin
# ---------------------------------------------------------------------------

class TestLogEntryAdmin(TestCase):

    def setUp(self):
        self.site = AdminSite()
        self.admin = LogEntryAdmin(LogEntry, self.site)
        self.factory = RequestFactory()

        self.superuser = User.objects.create_superuser(
            username='admin', email='admin@example.com', password='password'
        )
        self.staff_user = User.objects.create_user(
            username='staff', email='staff@example.com', password='password', is_staff=True
        )

        self.user_ct = ContentType.objects.get_for_model(User)
        self.permission = Permission.objects.get(
            codename='view_user', content_type=self.user_ct,
        )
        self.staff_user.user_permissions.add(self.permission)

        self.log_entry = LogEntry.objects.create(
            user_id=self.superuser.id,
            content_type_id=self.user_ct.id,
            object_id=self.staff_user.id,
            object_repr=str(self.staff_user),
            action_flag=CHANGE,
            change_message='[{"changed": {"fields": ["username"]}}]',
        )

        self.superuser_request = make_request_with_messages(self.factory, self.superuser)
        self.staff_request = make_request_with_messages(self.factory, self.staff_user)

    # --- get_queryset ---

    def test_get_queryset_superuser_sees_all(self):
        qs = self.admin.get_queryset(self.superuser_request)
        self.assertEqual(qs.count(), 1)

    def test_get_queryset_staff_sees_permitted_content_types_only(self):
        group_ct = ContentType.objects.get_for_model(Group)
        group = Group.objects.create(name="Test Group")
        LogEntry.objects.create(
            user_id=self.superuser.id,
            content_type_id=group_ct.id,
            object_id=group.id,
            object_repr=str(group),
            action_flag=CHANGE,
            change_message='[]',
        )
        qs = self.admin.get_queryset(self.staff_request)
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.first().content_type, self.user_ct)

    def test_get_queryset_staff_via_group_permission(self):
        perm_group = Group.objects.create(name="editors")
        perm_group.permissions.add(self.permission)
        new_staff = User.objects.create_user(
            username='gstaff', email='g@example.com', password='pw', is_staff=True
        )
        new_staff.groups.add(perm_group)
        request = make_request_with_messages(self.factory, new_staff)

        LogEntry.objects.create(
            user_id=self.superuser.id,
            content_type_id=self.user_ct.id,
            object_id=new_staff.id,
            object_repr=str(new_staff),
            action_flag=CHANGE,
            change_message='[]',
        )
        qs = self.admin.get_queryset(request)
        self.assertGreater(qs.count(), 0)

    def test_get_queryset_staff_no_permissions_sees_nothing(self):
        bare_staff = User.objects.create_user(
            username='bare', email='bare@example.com', password='pw', is_staff=True
        )
        request = make_request_with_messages(self.factory, bare_staff)
        qs = self.admin.get_queryset(request)
        self.assertEqual(qs.count(), 0)

    # --- get_change_message ---

    def test_get_change_message_json_converted(self):
        log_entry = mock.MagicMock()
        log_entry.change_message = '[{"changed": {"fields": ["username"]}}]'
        with mock.patch('django_extended_history.admin.json2html.convert', return_value='<div>Changed</div>'):
            result = self.admin.get_change_message(log_entry)
        self.assertEqual(result, '<div>Changed</div>')

    def test_get_change_message_plain_text_returned_as_is(self):
        log_entry = mock.MagicMock()
        log_entry.change_message = 'Changed username field'
        result = self.admin.get_change_message(log_entry)
        self.assertEqual(result, 'Changed username field')

    def test_get_change_message_invalid_json_falls_back_gracefully(self):
        """When json2html raises json.JSONDecodeError, original string is returned."""
        log_entry = mock.MagicMock()
        log_entry.change_message = '[invalid json'
        with mock.patch(
            'django_extended_history.admin.json2html.convert',
            side_effect=json.JSONDecodeError("err", "[invalid json", 0),
        ):
            result = self.admin.get_change_message(log_entry)
        self.assertEqual(result, '[invalid json')

    def test_get_change_message_empty_string(self):
        log_entry = mock.MagicMock()
        log_entry.change_message = ''
        result = self.admin.get_change_message(log_entry)
        self.assertEqual(result, '')

    def test_get_change_message_non_json_prefix_skips_conversion(self):
        """Strings not starting with '[' bypass json2html."""
        log_entry = mock.MagicMock()
        log_entry.change_message = 'No changes'
        with mock.patch('django_extended_history.admin.json2html.convert') as mock_j:
            result = self.admin.get_change_message(log_entry)
            mock_j.assert_not_called()
        self.assertEqual(result, 'No changes')

    # --- get_url_to_obj ---

    def test_get_url_to_obj_returns_link_for_valid_object(self):
        log_entry = mock.MagicMock()
        log_entry.content_type = self.user_ct
        log_entry.object_id = self.staff_user.id
        log_entry.object_repr = str(self.staff_user)
        with mock.patch(
            'django_extended_history.admin.reverse',
            return_value='/admin/auth/user/1/change/',
        ):
            result = self.admin.get_url_to_obj(log_entry)
        self.assertIn('/admin/auth/user/1/change/', result)
        self.assertIn(str(self.staff_user), result)

    def test_get_url_to_obj_no_reverse_match_returns_repr(self):
        log_entry = mock.MagicMock()
        log_entry.content_type = self.user_ct
        log_entry.object_id = 9999
        log_entry.object_repr = "Deleted User"
        with mock.patch('django_extended_history.admin.reverse', side_effect=NoReverseMatch):
            result = self.admin.get_url_to_obj(log_entry)
        self.assertEqual(result, "Deleted User")

    def test_get_url_to_obj_no_content_type_returns_repr(self):
        log_entry = mock.MagicMock()
        log_entry.content_type = None
        log_entry.object_repr = "Some Object"
        result = self.admin.get_url_to_obj(log_entry)
        self.assertEqual(result, "Some Object")

    def test_get_url_to_obj_no_object_id_returns_repr(self):
        log_entry = mock.MagicMock()
        log_entry.content_type = self.user_ct
        log_entry.object_id = None
        log_entry.object_repr = "Some Object"
        result = self.admin.get_url_to_obj(log_entry)
        self.assertEqual(result, "Some Object")

    # --- permissions ---

    def test_no_add_permission(self):
        self.assertFalse(self.admin.has_add_permission(self.superuser_request))

    def test_no_change_permission(self):
        self.assertFalse(self.admin.has_change_permission(self.superuser_request))

    def test_no_delete_permission(self):
        self.assertFalse(self.admin.has_delete_permission(self.superuser_request))

    def test_no_add_permission_with_obj(self):
        self.assertFalse(self.admin.has_add_permission(self.superuser_request, self.log_entry))

    def test_no_change_permission_with_obj(self):
        self.assertFalse(self.admin.has_change_permission(self.superuser_request, self.log_entry))

    def test_no_delete_permission_with_obj(self):
        self.assertFalse(self.admin.has_delete_permission(self.superuser_request, self.log_entry))


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class IntegrationTests(TestCase):

    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username='admin', email='admin@example.com', password='password'
        )
        self.test_user = User.objects.create(username='testuser', email='user@example.com')
        self.user_ct = ContentType.objects.get_for_model(User)
        self.client.login(username='admin', password='password')

    def test_log_entry_list_view(self):
        LogEntry.objects.create(
            user_id=self.superuser.id,
            content_type_id=self.user_ct.id,
            object_id=self.test_user.id,
            object_repr=str(self.test_user),
            action_flag=CHANGE,
            change_message='[{"changed": {"fields": ["username"]}}]',
        )
        url = reverse('admin:admin_logentry_changelist')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'testuser')

    def test_log_entry_detail_view(self):
        log_entry = LogEntry.objects.create(
            user_id=self.superuser.id,
            content_type_id=self.user_ct.id,
            object_id=self.test_user.id,
            object_repr=str(self.test_user),
            action_flag=CHANGE,
            change_message='[{"changed": {"fields": ["username"]}}]',
        )
        url = reverse('admin:admin_logentry_change', args=[log_entry.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_log_deletion_integration(self):
        factory = RequestFactory()
        request = make_request_with_messages(factory, self.superuser)

        class TestAdmin(DjangoExtendedHistory):
            def __init__(self):
                pass

        instance = TestAdmin()
        log_entry = instance.log_deletion(request, self.test_user, str(self.test_user))
        self.assertEqual(log_entry.action_flag, DELETION)
        self.assertEqual(log_entry.user_id, self.superuser.id)
        self.assertEqual(LogEntry.objects.filter(action_flag=DELETION).count(), 1)

    def test_log_deletions_integration(self):
        factory = RequestFactory()
        request = make_request_with_messages(factory, self.superuser)
        second = User.objects.create(username='user2', email='u2@example.com')

        class TestAdmin(DjangoExtendedHistory):
            def __init__(self):
                pass

        instance = TestAdmin()
        results = instance.log_deletions(request, [self.test_user, second])
        self.assertEqual(len(results), 2)
        self.assertEqual(LogEntry.objects.filter(action_flag=DELETION).count(), 2)

    def test_construct_change_message_integration(self):
        factory = RequestFactory()
        request = make_request_with_messages(factory, self.superuser)

        class TestAdmin(DjangoExtendedHistory):
            def __init__(self):
                pass

        instance = TestAdmin()
        mock_form = mock.MagicMock()
        mock_form.changed_data = ["username"]
        mock_form.initial = {"username": "oldname"}
        mock_form.cleaned_data = {"username": "newname"}
        mock_form.fields = {"username": _text_field_mock()}

        change_message = instance.construct_change_message(request, mock_form, None)
        self.assertTrue(any("details" in item for item in change_message))
        details = next(item for item in change_message if "details" in item)
        self.assertEqual(details["details"][0]["username"]["old"]["value"], "oldname")
        self.assertEqual(details["details"][0]["username"]["new"]["value"], "newname")
