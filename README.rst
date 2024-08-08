|PyPI| |PyPI download month|

.. |PyPI| image:: https://img.shields.io/pypi/pyversions/Django.svg?style=plastic
   :target: https://pypi.python.org/pypi/django-extended-history
.. |PyPI download month| image:: https://img.shields.io/pypi/dm/django-extended-history.svg
   :target: https://pypi.python.org/pypi/django-extended-history/


django-extended-history
========================

**django-extended-history** is (IMHO) the simplest way to record all changes made in admin-screens.

=============
Requirements
=============

- Django >=4.2

=============
Features
=============

-  Drop-in extension for Django history. No changes in any model, hence no migrations.
-  Records all changes in JSON format, extending what Django stores by default.
-  Adds a view 'Log entries' under 'Administration', showing all content types for which a user has permissions.
-  Safe to remove. Django will ignore all extra recorded information and revert to it's default history. So give it a spin!

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

------------
Misc
------------

Django extended history is released under the BSD-3 license, like Django. If you like it, please consider contributing.
