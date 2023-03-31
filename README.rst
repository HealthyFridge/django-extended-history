django-extended-history
========================

**django-extended-history** is (IMHO) the simplest way to record all changes made in admin-screens.

=============
Requirements
=============

- Django 4.x

=============
Features
=============

-  Drop-in replacement for default Django history. No changes in any model, hence no migrations.
-  Records all changes in JSON format as an extension to what Django already stores.
-  Adds a view 'Log entries' under 'Administration', showing only those entries to which a user has view-permissions.
-  Safe to remove if needed. Django will ignore the extra recorded information.

=============


------------
Installation
------------

.. code-block::

    pip install django-extended-history

------------
Setup
------------

Add **django_extended_history** to **INSTALLED_APPS** setting like this:

.. code-block:: python

    INSTALLED_APPS = [
    ...,
    'django_extended_history',
    ]

Done!

------------
Usage
------------

Apply the **DjangoExtendedHistory** mixin to all applicable admin-views:

.. code-block:: python
    
    from django.contrib import admin
    from .models import MyModel
    from django_extended_history.admin import DjangoExtendedHistory
    
    @admin.register(MyModel)
    class MyModelAdmin(DjangoExtendedHistory, admin.ModelAdmin):
        ...

