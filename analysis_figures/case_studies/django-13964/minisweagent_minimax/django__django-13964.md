# Docent run — django__django-13964

resolved=1 turns=60 docent_run=31101ac9-34d2-42a0-9261-67044d74c693

<details><summary>[0] system</summary>

```
You are a helpful assistant that can interact with a computer shell to solve programming tasks.
```
</details>

<details><summary>[1] user</summary>

```
<pr_description>
Consider the following PR description:
Saving parent object after setting on child leads to data loss for parents with non-numeric primary key.
Description
	 
		(last modified by Charlie DeTar)
	 
Given a model with a foreign key relation to another model that has a non-auto CharField as its primary key:
class Product(models.Model):
	sku = models.CharField(primary_key=True, max_length=50)
class Order(models.Model):
	product = models.ForeignKey(Product, on_delete=models.CASCADE)
If the relation is initialized on the parent with an empty instance that does not yet specify its primary key, and the primary key is subsequently defined, the parent does not "see" the primary key's change:
with transaction.atomic():
	order = Order()
	order.product = Product()
	order.product.sku = "foo"
	order.product.save()
	order.save()
	assert Order.objects.filter(product_id="").exists() # Succeeds, but shouldn't
	assert Order.objects.filter(product=order.product).exists() # Fails
Instead of product_id being populated with product.sku, it is set to emptystring. The foreign key constraint which would enforce the existence of a product with sku="" is deferred until the transaction commits. The transaction does correctly fail on commit with a ForeignKeyViolation due to the non-existence of a product with emptystring as its primary key.
On the other hand, if the related unsaved instance is initialized with its primary key before assignment to the parent, it is persisted correctly:
with ...
```
</details>


**[2] assistant:** I'll analyze this issue and work on fixing it. Let me start by exploring the codebase to understand the structure and find relevant files.

<details><summary>[3] tool</summary>

```
Exit code: 0

/testbed/tests/custom_migration_operations/more_operations.py
/testbed/tests/custom_migration_operations/__init__.py
/testbed/tests/custom_migration_operations/operations.py
/testbed/tests/admin_registration/models.py
/testbed/tests/admin_registration/__init__.py
/testbed/tests/admin_registration/tests.py
/testbed/tests/dates/models.py
/testbed/tests/dates/__init__.py
/testbed/tests/dates/tests.py
/testbed/tests/queries/test_bulk_update.py
/testbed/tests/queries/test_iterator.py
/testbed/tests/queries/models.py
/testbed/tests/queries/__init__.py
/testbed/tests/queries/test_db_returning.py
/testbed/tests/queries/test_query.py
/testbed/tests/queries/tests.py
/testbed/tests/queries/test_qs_combinators.py
/testbed/tests/queries/test_q.py
/testbed/tests/queries/test_explain.py
/testbed/tests/shortcuts/__init__.py
```
</details>


**[4] assistant:** 

<details><summary>[5] tool</summary>

```
Exit code: 0

total 176
drwxrwxrwx   1 root root  4096 Sep 10 11:37 .
drwxr-xr-x   1 root root  4096 Feb 17 07:08 ..
-rw-r--r--   1 root root   807 Sep 10 11:30 .editorconfig
-rw-r--r--   1 root root   120 Sep 10 11:30 .eslintignore
-rw-r--r--   1 root root  1407 Sep 10 11:30 .eslintrc
drwxrwxrwx   8 root root  4096 Sep 10 11:37 .git
-rw-r--r--   1 root root   297 Sep 10 11:30 .gitattributes
drwxrwxrwx   3 root root  4096 Sep 10 11:30 .github
-rw-r--r--   1 root root   353 Sep 10 11:30 .gitignore
-rw-r--r--   1 root root   281 Sep 10 11:30 .pre-commit-config.yaml
drwxrwxrwx   2 root root  4096 Sep 10 11:30 .tx
-rw-r--r--   1 root root 38514 Sep 10 11:30 AUTHORS
-rw-r--r--   1 root root  1115 Sep 10 11:30 CONTRIBUTING.rst
drwxr-xr-x   2 root root  4096 Sep 10 11:37 Django.egg-info
-rw-r--r--   1 root root   369 Sep 10 11:30 Gruntfile.js
-rw-r--r--   1 root root   236 Sep 10 11:30 INSTALL
-rw-r--r--   1 root root  1552 Sep 10 11:30 LICENSE
-rw-r--r--   1 root root 13227 Sep 10 11:30 LICENSE.python
-rw-r--r--   1 root root   276 Sep 10 11:30 MANIFEST.in
-rw-r--r--   1 root root  2131 Sep 10 11:30 README.rst
drwxrwxrwx  18 root root  4096 Sep 10 11:37 django
drwxrwxrwx  13 root root  4096 Sep 10 11:30 docs
drwxrwxrwx   2 root root  4096 Sep 10 11:30 extras
drwxrwxrwx   4 root root  4096 Sep 10 11:30 js_tests
-rw-r--r--   1 root root   334 Sep 10 11:30 package.json
drwxrwxrwx   2 root root  4096 Sep 10 11:30 scripts
-rw-r--r--   1 root root  2137 Sep 10 11:30 setup.cfg
-rw-r--r--  ...
```
</details>


**[6] assistant:** 

<details><summary>[7] tool</summary>

```
Exit code: 0

total 392
drwxrwxrwx 5 root root  4096 Sep 10 11:30 .
drwxrwxrwx 5 root root  4096 Sep 10 11:30 ..
-rw-r--r-- 1 root root  2522 Sep 10 11:30 __init__.py
-rw-r--r-- 1 root root  5933 Sep 10 11:30 aggregates.py
-rw-r--r-- 1 root root 89262 Sep 10 11:30 base.py
-rw-r--r-- 1 root root   117 Sep 10 11:30 constants.py
-rw-r--r-- 1 root root  7796 Sep 10 11:30 constraints.py
-rw-r--r-- 1 root root 19750 Sep 10 11:30 deletion.py
-rw-r--r-- 1 root root  2740 Sep 10 11:30 enums.py
-rw-r--r-- 1 root root 52319 Sep 10 11:30 expressions.py
drwxrwxrwx 2 root root  4096 Sep 10 11:30 fields
drwxrwxrwx 2 root root  4096 Sep 10 11:30 functions
-rw-r--r-- 1 root root 11281 Sep 10 11:30 indexes.py
-rw-r--r-- 1 root root 22750 Sep 10 11:30 lookups.py
-rw-r--r-- 1 root root  6778 Sep 10 11:30 manager.py
-rw-r--r-- 1 root root 36791 Sep 10 11:30 options.py
-rw-r--r-- 1 root root 84103 Sep 10 11:30 query.py
-rw-r--r-- 1 root root 11719 Sep 10 11:30 query_utils.py
-rw-r--r-- 1 root root  1573 Sep 10 11:30 signals.py
drwxrwxrwx 2 root root  4096 Sep 10 11:30 sql
-rw-r--r-- 1 root root  1564 Sep 10 11:30 utils.py
```
</details>


**[8] assistant:** 

<details><summary>[9] tool</summary>

```
Exit code: 0

total 296
drwxrwxrwx 2 root root  4096 Sep 10 11:30 .
drwxrwxrwx 5 root root  4096 Sep 10 11:30 ..
-rw-r--r-- 1 root root 92346 Sep 10 11:30 __init__.py
-rw-r--r-- 1 root root 18288 Sep 10 11:30 files.py
-rw-r--r-- 1 root root 19459 Sep 10 11:30 json.py
-rw-r--r-- 1 root root  1801 Sep 10 11:30 mixins.py
-rw-r--r-- 1 root root   515 Sep 10 11:30 proxy.py
-rw-r--r-- 1 root root 70194 Sep 10 11:30 related.py
-rw-r--r-- 1 root root 54061 Sep 10 11:30 related_descriptors.py
-rw-r--r-- 1 root root  7073 Sep 10 11:30 related_lookups.py
-rw-r--r-- 1 root root 11195 Sep 10 11:30 reverse_related.py
```
</details>


**[10] assistant:** 

<details><summary>[11] tool</summary>

```
Exit code: 1


```
</details>


**[12] assistant:** 

<details><summary>[13] tool</summary>

```
Exit code: 0

82:class ForwardManyToOneDescriptor:
278:class ForwardOneToOneDescriptor(ForwardManyToOneDescriptor):
326:class ReverseOneToOneDescriptor:
494:class ReverseManyToOneDescriptor:
761:class ManyToManyDescriptor(ReverseManyToOneDescriptor):
```
</details>


**[14] assistant:** 

<details><summary>[15] tool</summary>

```
Exit code: 0

class ForwardManyToOneDescriptor:
    """
    Accessor to the related object on the forward side of a many-to-one or
    one-to-one (via ForwardOneToOneDescriptor subclass) relation.

    In the example::

        class Child(Model):
            parent = ForeignKey(Parent, related_name='children')

    ``Child.parent`` is a ``ForwardManyToOneDescriptor`` instance.
    """

    def __init__(self, field_with_rel):
        self.field = field_with_rel

    @cached_property
    def RelatedObjectDoesNotExist(self):
        # The exception can't be created at initialization time since the
        # related model might not be resolved yet; `self.field.model` might
        # still be a string model reference.
        return type(
            'RelatedObjectDoesNotExist',
            (self.field.remote_field.model.DoesNotExist, AttributeError), {
                '__module__': self.field.model.__module__,
                '__qualname__': '%s.%s.RelatedObjectDoesNotExist' % (
                    self.field.model.__qualname__,
                    self.field.name,
                ),
            }
        )

    def is_cached(self, instance):
        return self.field.is_cached(instance)

    def get_queryset(self, **hints):
        return self.field.remote_field.model._base_manager.db_manager(hints=hints).all()

    def get_prefetch_queryset(self, instances, queryset=None):
        if queryset is None:
            queryset = self.get_queryset()
        queryset._add_hints(insta ...
```
</details>


**[16] assistant:** 

<details><summary>[17] tool</summary>

```
Exit code: 0

else:
            return rel_obj

    def __set__(self, instance, value):
        """
        Set the related instance through the forward relation.

        With the example above, when setting ``child.parent = parent``:

        - ``self`` is the descriptor managing the ``parent`` attribute
        - ``instance`` is the ``child`` instance
        - ``value`` is the ``parent`` instance on the right of the equal sign
        """
        # An object must be an instance of the related class.
        if value is not None and not isinstance(value, self.field.remote_field.model._meta.concrete_model):
            raise ValueError(
                'Cannot assign "%r": "%s.%s" must be a "%s" instance.' % (
                    value,
                    instance._meta.object_name,
                    self.field.name,
                    self.field.remote_field.model._meta.object_name,
                )
            )
        elif value is not None:
            if instance._state.db is None:
                instance._state.db = router.db_for_write(instance.__class__, instance=value)
            if value._state.db is None:
                value._state.db = router.db_for_write(value.__class__, instance=instance)
            if not router.allow_relation(value, instance):
                raise ValueError('Cannot assign "%r": the current database router prevents this relation.' % value)

        remote_field = self.field.remote_field
        # If we're setting the value of a On ...
```
</details>


**[18] assistant:** 

<details><summary>[19] tool</summary>

```
Exit code: 0

340:            for _, rh_field in self.related_fields
352:            for lh_field, rh_field in self.related_fields
515:            self.foreign_related_fields
519:        if not self.foreign_related_fields:
535:        foreign_fields = {f.name for f in self.foreign_related_fields}
538:        if not has_unique_constraint and len(self.foreign_related_fields) > 1:
540:                "'%s'" % rel_field.name for rel_field in self.foreign_related_fields
558:            field_name = self.foreign_related_fields[0].name
611:    def resolve_related_fields(self):
616:        related_fields = []
627:            related_fields.append((from_field, to_field))
628:        return related_fields
631:    def related_fields(self):
632:        return self.resolve_related_fields()
635:    def reverse_related_fields(self):
636:        return [(rhs_field, lhs_field) for lhs_field, rhs_field in self.related_fields]
639:    def local_related_fields(self):
640:        return tuple(lhs_field for lhs_field, rhs_field in self.related_fields)
643:    def foreign_related_fields(self):
644:        return tuple(rhs_field for lhs_field, rhs_field in self.related_fields if rhs_field)
647:        return self.get_instance_value_for_fields(instance, self.local_related_fields)
650:        return self.get_instance_value_for_fields(instance, self.foreign_related_fields)
675:        source = self.reverse_related_fields if reverse_join else self.related_fields
717:            target_fields=self.foreig ...
```
</details>


**[20] assistant:** 

<details><summary>[21] tool</summary>

```
Exit code: 0

33:    class_prepared, post_init, post_save, pre_init, pre_save,
396:    # If true, uniqueness validation checks will consider this a new, unsaved
399:    # on the actual save.
672:    def save(self, force_insert=False, force_update=False, using=None,
679:        that the "save" must be an SQL insert or update (or equivalent for
682:        self._prepare_related_fields_for_save(operation_name='save')
690:            # If update_fields is empty, skip the save. We do also check for
691:            # no-op saves later on for inheritance cases. This bailout is
716:        # automatically do an "update_fields" save on the loaded fields.
726:        self.save_base(using=using, force_insert=force_insert,
728:    save.alters_data = True
730:    def save_base(self, raw=False, force_insert=False,
733:        Handle the parts of saving which should be done only once per save,
734:        yet need to be done in raw saves, too. This includes some sanity
737:        The 'raw' argument is telling save_base not to save any parent
738:        models and not to do any changes to the values before save. This
750:            pre_save.send(
756:            context_manager = transaction.atomic(using=using, savepoint=False)
762:                parent_inserted = self._save_parents(cls, using, update_fields)
763:            updated = self._save_table(
767:        # Store the database on which the object was saved
769:        # Once saved, this is no longer a to-be-added instance.
772:   ...
```
</details>


**[22] assistant:** 

<details><summary>[23] tool</summary>

```
Exit code: 0

682:        self._prepare_related_fields_for_save(operation_name='save')
911:    def _prepare_related_fields_for_save(self, operation_name):
```
</details>


**[24] assistant:** 

<details><summary>[25] tool</summary>

```
Exit code: 0

def _prepare_related_fields_for_save(self, operation_name):
        # Ensure that a model instance without a PK hasn't been assigned to
        # a ForeignKey or OneToOneField on this model. If the field is
        # nullable, allowing the save would result in silent data loss.
        for field in self._meta.concrete_fields:
            # If the related field isn't cached, then an instance hasn't been
            # assigned and there's no need to worry about this check.
            if field.is_relation and field.is_cached(self):
                obj = getattr(self, field.name, None)
                if not obj:
                    continue
                # A pk may have been assigned manually to a model instance not
                # saved to the database (or auto-generated in a case like
                # UUIDField), but we allow the save to proceed and rely on the
                # database to raise an IntegrityError if applicable. If
                # constraints aren't supported by the database, there's the
                # unavoidable risk of data corruption.
                if obj.pk is None:
                    # Remove the object from a related instance cache.
                    if not field.remote_field.multiple:
                        field.remote_field.delete_cached_value(obj)
                    raise ValueError(
                        "%s() prohibited to prevent data loss due to unsaved "
                        "related object '%s'." % (operati ...
```
</details>


**[26] assistant:** 

<details><summary>[27] tool</summary>

```
Exit code: 1

Traceback (most recent call last):
  File "/testbed/test_issue.py", line 24, in <module>
    django.setup()
  File "/testbed/django/__init__.py", line 14, in setup
    from django.apps import apps
  File "/testbed/django/apps/__init__.py", line 1, in <module>
    from .config import AppConfig
  File "/testbed/django/apps/config.py", line 7, in <module>
    from django.utils.deprecation import RemovedInDjango41Warning
  File "/testbed/django/utils/deprecation.py", line 5, in <module>
    from asgiref.sync import sync_to_async
ModuleNotFoundError: No module named 'asgiref'
```
</details>


**[28] assistant:** 

<details><summary>[29] tool</summary>

```
Exit code: 0

Collecting asgiref
  Downloading asgiref-3.11.1-py3-none-any.whl.metadata (9.3 kB)
Downloading asgiref-3.11.1-py3-none-any.whl (24 kB)
Installing collected packages: asgiref
Successfully installed asgiref-3.11.1
WARNING: Running pip as the 'root' user can result in broken permissions and conflicting behaviour with the system package manager. It is recommended to use a virtual environment instead: https://pip.pypa.io/warnings/venv
```
</details>


**[30] assistant:** 

<details><summary>[31] tool</summary>

```
Exit code: 1

Traceback (most recent call last):
  File "/testbed/test_issue.py", line 24, in <module>
    django.setup()
  File "/testbed/django/__init__.py", line 16, in setup
    from django.urls import set_script_prefix
  File "/testbed/django/urls/__init__.py", line 1, in <module>
    from .base import (
  File "/testbed/django/urls/base.py", line 8, in <module>
    from .exceptions import NoReverseMatch, Resolver404
  File "/testbed/django/urls/exceptions.py", line 1, in <module>
    from django.http import Http404
  File "/testbed/django/http/__init__.py", line 5, in <module>
    from django.http.response import (
  File "/testbed/django/http/response.py", line 16, in <module>
    from django.core.serializers.json import DjangoJSONEncoder
  File "/testbed/django/core/serializers/__init__.py", line 23, in <module>
    from django.core.serializers.base import SerializerDoesNotExist
  File "/testbed/django/core/serializers/base.py", line 7, in <module>
    from django.db import models
  File "/testbed/django/db/models/__init__.py", line 3, in <module>
    from django.db.models.aggregates import *  # NOQA
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/testbed/django/db/models/aggregates.py", line 5, in <module>
    from django.db.models.expressions import Case, Func, Star, When
  File "/testbed/django/db/models/expressions.py", line 10, in <module>
    from django.db.models import fields
  File "/testbed/django/db/models/fields/__init__.py", line 11, in <module>
   ...
```
</details>


**[32] assistant:** 

<details><summary>[33] tool</summary>

```
Exit code: 0

Collecting pytz
  Downloading pytz-2025.2-py2.py3-none-any.whl.metadata (22 kB)
Downloading pytz-2025.2-py2.py3-none-any.whl (509 kB)
Installing collected packages: pytz
Successfully installed pytz-2025.2
WARNING: Running pip as the 'root' user can result in broken permissions and conflicting behaviour with the system package manager. It is recommended to use a virtual environment instead: https://pip.pypa.io/warnings/venv
```
</details>


**[34] assistant:** 

<details><summary>[35] tool</summary>

```
Exit code: 1

Traceback (most recent call last):
  File "/testbed/test_issue.py", line 24, in <module>
    django.setup()
  File "/testbed/django/__init__.py", line 24, in setup
    apps.populate(settings.INSTALLED_APPS)
  File "/testbed/django/apps/registry.py", line 114, in populate
    app_config.import_models()
  File "/testbed/django/apps/config.py", line 301, in import_models
    self.models_module = import_module(models_module_name)
                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/opt/miniconda3/lib/python3.11/importlib/__init__.py", line 126, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1204, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1176, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1147, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 690, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 940, in exec_module
  File "<frozen importlib._bootstrap>", line 241, in _call_with_frames_removed
  File "/testbed/django/contrib/auth/models.py", line 3, in <module>
    from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
  File "/testbed/django/contrib/auth/base_user.py", line 47, in <module>
    class AbstractBaseUser(models.Model):
  File "/testbed/django/db/models/base.py", line 122, in __new__
    new_class.ad ...
```
</details>


**[36] assistant:** 

<details><summary>[37] tool</summary>

```
Exit code: 0

Collecting sqlparse
  Downloading sqlparse-0.5.5-py3-none-any.whl.metadata (4.7 kB)
Downloading sqlparse-0.5.5-py3-none-any.whl (46 kB)
Installing collected packages: sqlparse
Successfully installed sqlparse-0.5.5
WARNING: Running pip as the 'root' user can result in broken permissions and conflicting behaviour with the system package manager. It is recommended to use a virtual environment instead: https://pip.pypa.io/warnings/venv
```
</details>


**[38] assistant:** 

<details><summary>[39] tool</summary>

```
Exit code: 0

Test case 1: Setting pk after assignment
order.product_id = ''
Test 1 FAILED as expected: Product matching query does not exist.

Test case 2: Setting pk before assignment
order2.product_id = 'bar'
order2.product.pk = 'bar'
Test 2 PASSED

Test case 3: Using None as initial pk
order3.product_id = 'baz'
order3.product.pk = 'baz'
Test 3 PASSED
```
</details>


**[40] assistant:** 

<details><summary>[41] tool</summary>

```
Exit code: 0

def _prepare_related_fields_for_save(self, operation_name):
        # Ensure that a model instance without a PK hasn't been assigned to
        # a ForeignKey or OneToOneField on this model. If the field is
        # nullable, allowing the save would result in silent data loss.
        for field in self._meta.concrete_fields:
            # If the related field isn't cached, then an instance hasn't been
            # assigned and there's no need to worry about this check.
            if field.is_relation and field.is_cached(self):
                obj = getattr(self, field.name, None)
                if not obj:
                    continue
                # A pk may have been assigned manually to a model instance not
                # saved to the database (or auto-generated in a case like
                # UUIDField), but we allow the save to proceed and rely on the
                # database to raise an IntegrityError if applicable. If
                # constraints aren't supported by the database, there's the
                # unavoidable risk of data corruption.
                if obj.pk is None:
                    # Remove the object from a related instance cache.
                    if not field.remote_field.multiple:
                        field.remote_field.delete_cached_value(obj)
                    raise ValueError(
                        "%s() prohibited to prevent data loss due to unsaved "
                        "related object '%s'." % (operati ...
```
</details>


**[42] assistant:** 

<details><summary>[43] tool</summary>

```
Exit code: 0

)
                elif getattr(self, field.attname) is None:
                    # Use pk from related object if it has been saved after
                    # an assignment.
                    setattr(self, field.attname, obj.pk)
                # If the relationship's pk/to_field was changed, clear the
                # cached relationship.
                if getattr(obj, field.target_field.attname) != getattr(self, field.attname):
                    field.delete_cached_value(self)

    def delete(self, using=None, keep_parents=False):
        using = using or router.db_for_write(self.__class__, instance=self)
        assert self.pk is not None, (
            "%s object can't be deleted because its %s attribute is set to None." %
            (self._meta.object_name, self._meta.pk.attname)
        )
```
</details>


**[44] assistant:** 

<details><summary>[45] tool</summary>

```
Exit code: 0


```
</details>


**[46] assistant:** 

<details><summary>[47] tool</summary>

```
Exit code: 1


```
</details>


**[48] assistant:** 

<details><summary>[49] tool</summary>

```
Exit code: 1


```
</details>


**[50] assistant:** 

<details><summary>[51] tool</summary>

```
Exit code: 0

531:        my_pk = self.pk
534:        return my_pk == other.pk
583:    pk = property(_get_pk_val, _set_pk_val)
1116:            model_class_pk = self._get_pk_val(model_class._meta)
```
</details>


**[52] assistant:** 

<details><summary>[53] tool</summary>

```
Exit code: 0

return getattr(self, meta.pk.attname)

    def _set_pk_val(self, value):
        for parent_link in self._meta.parents.values():
            if parent_link and parent_link != self._meta.pk:
                setattr(self, parent_link.target_field.attname, value)
        return setattr(self, self._meta.pk.attname, value)

    pk = property(_get_pk_val, _set_pk_val)

    def get_deferred_fields(self):
        """
        Return a set containing names of deferred fields for this instance.
        """
        return {
            f.attname for f in self._meta.concrete_fields
```
</details>


**[54] assistant:** 

<details><summary>[55] tool</summary>

```
Exit code: 0

def _prepare_related_fields_for_save(self, operation_name):
        # Ensure that a model instance without a PK hasn't been assigned to
        # a ForeignKey or OneToOneField on this model. If the field is
        # nullable, allowing the save would result in silent data loss.
        for field in self._meta.concrete_fields:
            # If the related field isn't cached, then an instance hasn't been
            # assigned and there's no need to worry about this check.
            if field.is_relation and field.is_cached(self):
                obj = getattr(self, field.name, None)
                if not obj:
                    continue
                # A pk may have been assigned manually to a model instance not
                # saved to the database (or auto-generated in a case like
                # UUIDField), but we allow the save to proceed and rely on the
                # database to raise an IntegrityError if applicable. If
                # constraints aren't supported by the database, there's the
                # unavoidable risk of data corruption.
                if obj.pk is None:
                    # Remove the object from a related instance cache.
                    if not field.remote_field.multiple:
                        field.remote_field.delete_cached_value(obj)
                    raise ValueError(
                        "%s() prohibited to prevent data loss due to unsaved "
                        "related object '%s'." % (operati ...
```
</details>


**[56] assistant:** 

<details><summary>[57] tool</summary>

```
Exit code: 0

True
```
</details>


**[58] assistant:** 

<details><summary>[59] tool</summary>

```
Exit code: 0


```
</details>


**[60] assistant:** 

<details><summary>[61] tool</summary>

```
Exit code: 0

Fix applied
```
</details>


**[62] assistant:** 

<details><summary>[63] tool</summary>

```
Exit code: 0

def _prepare_related_fields_for_save(self, operation_name):
        # Ensure that a model instance without a PK hasn't been assigned to
        # a ForeignKey or OneToOneField on this model. If the field is
        # nullable, allowing the save would result in silent data loss.
        for field in self._meta.concrete_fields:
            # If the related field isn't cached, then an instance hasn't been
            # assigned and there's no need to worry about this check.
            if field.is_relation and field.is_cached(self):
                obj = getattr(self, field.name, None)
                if not obj:
                    continue
                # A pk may have been assigned manually to a model instance not
                # saved to the database (or auto-generated in a case like
                # UUIDField), but we allow the save to proceed and rely on the
                # database to raise an IntegrityError if applicable. If
                # constraints aren't supported by the database, there's the
                # unavoidable risk of data corruption.
                if obj.pk is None:
                    # Remove the object from a related instance cache.
                    if not field.remote_field.multiple:
                        field.remote_field.delete_cached_value(obj)
                    raise ValueError(
                        "%s() prohibited to prevent data loss due to unsaved "
                        "related object '%s'." % (operati ...
```
</details>


**[64] assistant:** 

<details><summary>[65] tool</summary>

```
Exit code: 0

Test case 1: Setting pk after assignment
order.product_id = 'foo'
order.product.pk = 'foo'
Test 1 FAILED as expected: FAIL: product_id is empty string

Test case 2: Setting pk before assignment
order2.product_id = 'bar'
order2.product.pk = 'bar'
Test 2 PASSED

Test case 3: Using None as initial pk
order3.product_id = 'baz'
order3.product.pk = 'baz'
Test 3 PASSED
```
</details>


**[66] assistant:** 

<details><summary>[67] tool</summary>

```
Exit code: 0

import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'test_settings')

# Create a minimal settings module
import sys
sys.path.insert(0, '/testbed')

from django.conf import settings
settings.configure(
    DEBUG=True,
    DATABASES={
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': ':memory:',
        }
    },
    INSTALLED_APPS=[
        'django.contrib.contenttypes',
        'django.contrib.auth',
    ],
    USE_TZ=True,
)
django.setup()

from django.db import models, connection, transaction

# Create test models
class Product(models.Model):
    sku = models.CharField(primary_key=True, max_length=50)
    
    class Meta:
        app_label = 'test_app'

class Order(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    
    class Meta:
        app_label = 'test_app'

# Create tables
with connection.schema_editor() as schema_editor:
    schema_editor.create_model(Product)
    schema_editor.create_model(Order)

# Test case 1: The problem case - pk set after assignment
print("Test case 1: Setting pk after assignment")
try:
    with transaction.atomic():
        order = Order()
        order.product = Product()
        order.product.sku = "foo"
        order.product.save()
        order.save()
        print(f"order.product_id = '{order.product_id}'")
        print(f"order.product.pk = '{order.product.pk}'")
        assert Order.objects.filter(product_id="").exists(), "FAIL ...
```
</details>


**[68] assistant:** 

<details><summary>[69] tool</summary>

```
Exit code: 0

Test case 1: Setting pk after assignment
order.product_id = 'foo'
order.product.pk = 'foo'
Test 1 PASSED!

Test case 2: Setting pk before assignment
order2.product_id = 'bar'
order2.product.pk = 'bar'
Test 2 PASSED

Test case 3: Using None as initial pk
order3.product_id = 'baz'
order3.product.pk = 'baz'
Test 3 PASSED

Test case 4: Changing pk after assignment but before save
order4.product_id = 'changed'
order4.product.pk = 'changed'
Test 4 PASSED

Test case 5: Auto-increment primary key
item.category_id = 1
item.category.pk = 1
Test 5 PASSED
```
</details>


**[70] assistant:** 

<details><summary>[71] tool</summary>

```
Exit code: 0

Edge case 1: Explicitly setting foreign key value after assignment
order.product_id = 'prod2'
Edge case 1 PASSED

Edge case 2: Setting the related object to None
Edge case 2 FAILED: NOT NULL constraint failed: test_app_order.product_id

Edge case 3: Using nullable=False (default) - should raise error for None
Edge case 3 PASSED (expected error): IntegrityError: NOT NULL constraint failed: test_app_order.product_id

Edge case 4: Verify cached relationship is cleared when pk changes
order.product_id = 'prod5b'
order.product.pk = 'prod5b'
Edge case 4 PASSED

All edge case tests completed!
```
</details>


**[72] assistant:** 

<details><summary>[73] tool</summary>

```
Exit code: 0

# Use the cache directly, instead of the accessor; if we haven't
            # populated the cache, then we don't care - we're only accessing
            # the object to invalidate the accessor cache, so there's no
            # need to populate the cache just to expire it again.
            related = self.field.get_cached_value(instance, default=None)

            # If we've got an old related object, we need to clear out its
            # cache. This cache also might not exist if the related object
            # hasn't been accessed yet.
            if related is not None:
                remote_field.set_cached_value(related, None)

            for lh_field, rh_field in self.field.related_fields:
                setattr(instance, lh_field.attname, None)

        # Set the values of the related field.
        else:
            for lh_field, rh_field in self.field.related_fields:
                setattr(instance, lh_field.attname, getattr(value, rh_field.attname))

        # Set the related instance cache used by __get__ to avoid an SQL query
        # when accessing the attribute we just set.
```
</details>


**[74] assistant:** 

<details><summary>[75] tool</summary>

```
Exit code: 0

# assigned and there's no need to worry about this check.
            if field.is_relation and field.is_cached(self):
                obj = getattr(self, field.name, None)
                if not obj:
                    continue
                # A pk may have been assigned manually to a model instance not
                # saved to the database (or auto-generated in a case like
                # UUIDField), but we allow the save to proceed and rely on the
                # database to raise an IntegrityError if applicable. If
```
</details>


**[76] assistant:** 

<details><summary>[77] tool</summary>

```
Exit code: 0

/opt/miniconda3/bin/python: No module named pytest
```
</details>


**[78] assistant:** 

<details><summary>[79] tool</summary>

```
Exit code: 0

Traceback (most recent call last):
  File "/testbed/tests/runtests.py", line 14, in <module>
    import django
ModuleNotFoundError: No module named 'django'

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/testbed/tests/runtests.py", line 16, in <module>
    raise RuntimeError(
RuntimeError: Django module not found, reference tests/README.rst for instructions.
```
</details>


**[80] assistant:** 

<details><summary>[81] tool</summary>

```
Exit code: 0

Creating test database for alias 'default' ('file:memorydb_default?mode=memory&cache=shared')...
Testing against Django installed in '/testbed/django' with up to 32 processes
Importing application model_fields
Skipping setup of unused database(s): other.
Operations to perform:
  Synchronize unmigrated apps: auth, contenttypes, messages, model_fields, sessions, staticfiles
  Apply all migrations: admin, sites
Synchronizing apps without migrations:
  Creating tables...
    Creating table django_content_type
    Creating table auth_permission
    Creating table auth_group
    Creating table auth_user
    Creating table django_session
    Creating table model_fields_foo
    Creating table model_fields_bar
    Creating table model_fields_whiz
    Creating table model_fields_whizdelayed
    Creating table model_fields_whiziter
    Creating table model_fields_whiziterempty
    Creating table model_fields_choiceful
    Creating table model_fields_bigd
    Creating table model_fields_floatmodel
    Creating table model_fields_bigs
    Creating table model_fields_unicodeslugfield
    Creating table model_fields_automodel
    Creating table model_fields_bigautomodel
    Creating table model_fields_smallautomodel
    Creating table model_fields_smallintegermodel
    Creating table model_fields_integermodel
    Creating table model_fields_bigintegermodel
    Creating table model_fields_positivebigintegermodel
    Creating table model_fields_positivesmallintegermodel
    Crea ...
```
</details>


**[82] assistant:** 

<details><summary>[83] tool</summary>

```
Exit code: 0

Running migrations:
  Applying admin.0001_initial... OK
  Applying admin.0002_logentry_remove_auto_add... OK
  Applying admin.0003_logentry_add_action_flag_choices... OK
  Applying sites.0001_initial... OK
  Applying sites.0002_alter_domain_unique... OK
System check identified no issues (0 silenced).
test_add (many_to_one.tests.ManyToOneTests.test_add) ... ok
test_add_after_prefetch (many_to_one.tests.ManyToOneTests.test_add_after_prefetch) ... ok
test_add_remove_set_by_pk_raises (many_to_one.tests.ManyToOneTests.test_add_remove_set_by_pk_raises) ... ok
test_add_then_remove_after_prefetch (many_to_one.tests.ManyToOneTests.test_add_then_remove_after_prefetch) ... ok
test_assign (many_to_one.tests.ManyToOneTests.test_assign) ... ok
test_assign_fk_id_none (many_to_one.tests.ManyToOneTests.test_assign_fk_id_none) ... ok
test_assign_fk_id_value (many_to_one.tests.ManyToOneTests.test_assign_fk_id_value) ... ok
test_cached_foreign_key_with_to_field_not_cleared_by_save (many_to_one.tests.ManyToOneTests.test_cached_foreign_key_with_to_field_not_cleared_by_save) ... ok
test_cached_relation_invalidated_on_save (many_to_one.tests.ManyToOneTests.test_cached_relation_invalidated_on_save)
Model.save() invalidates stale ForeignKey relations after a primary key ... ok
test_clear_after_prefetch (many_to_one.tests.ManyToOneTests.test_clear_after_prefetch) ... ok
test_create (many_to_one.tests.ManyToOneTests.test_create) ... ok
test_create_relation_with_gettext_lazy (many_to_one.te ...
```
</details>


**[84] assistant:** 

<details><summary>[85] tool</summary>

```
Exit code: 0

Model.save() invalidates stale OneToOneField relations after a primary ... ok
test_create_models_m2m (one_to_one.tests.OneToOneTests.test_create_models_m2m)
Models are created via the m2m relation if the remote model has a ... ok
test_filter_one_to_one_relations (one_to_one.tests.OneToOneTests.test_filter_one_to_one_relations)
Regression test for #9968 ... ok
test_foreign_key (one_to_one.tests.OneToOneTests.test_foreign_key) ... ok
test_get_reverse_on_unsaved_object (one_to_one.tests.OneToOneTests.test_get_reverse_on_unsaved_object)
Regression for #18153 and #19089. ... ok
test_getter (one_to_one.tests.OneToOneTests.test_getter) ... ok
test_hasattr_related_object (one_to_one.tests.OneToOneTests.test_hasattr_related_object) ... ok
test_hidden_accessor (one_to_one.tests.OneToOneTests.test_hidden_accessor)
When a '+' ending related name is specified no reverse accessor should ... ok
test_manager_all (one_to_one.tests.OneToOneTests.test_manager_all) ... ok
test_manager_get (one_to_one.tests.OneToOneTests.test_manager_get) ... ok
test_multiple_o2o (one_to_one.tests.OneToOneTests.test_multiple_o2o) ... ok
test_nullable_o2o_delete (one_to_one.tests.OneToOneTests.test_nullable_o2o_delete) ... ok
test_o2o_primary_key_delete (one_to_one.tests.OneToOneTests.test_o2o_primary_key_delete) ... ok
test_primary_key_to_field_filter (one_to_one.tests.OneToOneTests.test_primary_key_to_field_filter) ... ok
test_rel_pk_exact (one_to_one.tests.OneToOneTests.test_rel_pk_exact) ... ok
t ...
```
</details>


**[86] assistant:** 

<details><summary>[87] tool</summary>

```
Exit code: 0

test_date_filter_null (model_regress.tests.ModelTests.test_date_filter_null) ... ok
test_date_lookup (model_regress.tests.ModelTests.test_date_lookup) ... ok
test_empty_choice (model_regress.tests.ModelTests.test_empty_choice) ... ok
test_get_next_prev_by_field (model_regress.tests.ModelTests.test_get_next_prev_by_field) ... ok
test_get_next_prev_by_field_unsaved (model_regress.tests.ModelTests.test_get_next_prev_by_field_unsaved) ... ok
test_long_textfield (model_regress.tests.ModelTests.test_long_textfield) ... ok
test_long_unicode_textfield (model_regress.tests.ModelTests.test_long_unicode_textfield) ... ok
test_metaclass_can_access_attribute_dict (model_regress.tests.ModelTests.test_metaclass_can_access_attribute_dict)
Model metaclasses have access to the class attribute dict in ... ok
test_model_init_too_many_args (model_regress.tests.ModelTests.test_model_init_too_many_args) ... ok
test_primary_key_foreign_key_types (model_regress.tests.ModelTests.test_primary_key_foreign_key_types) ... ok
test_related_gte_lookup (model_regress.tests.ModelTests.test_related_gte_lookup)
Regression test for #10153: foreign key __gte lookups. ... ok
test_related_lte_lookup (model_regress.tests.ModelTests.test_related_lte_lookup)
Regression test for #10153: foreign key __lte lookups. ... ok
test_sql_insert_compiler_return_id_attribute (model_regress.tests.ModelTests.test_sql_insert_compiler_return_id_attribute)
Regression test for #14019: SQLInsertCompiler.as_sql() failure ... ...
```
</details>


**[88] assistant:** 

<details><summary>[89] tool</summary>

```
Exit code: 0

Creating test database for alias 'default' ('file:memorydb_default?mode=memory&cache=shared')...
Testing against Django installed in '/testbed/django' with up to 32 processes
Importing application many_to_one
Skipping setup of unused database(s): other.
Operations to perform:
  Synchronize unmigrated apps: auth, contenttypes, many_to_one, messages, sessions, staticfiles
  Apply all migrations: admin, sites
Synchronizing apps without migrations:
  Creating tables...
    Creating table django_content_type
    Creating table auth_permission
    Creating table auth_group
    Creating table auth_user
    Creating table django_session
    Creating table many_to_one_reporter
    Creating table many_to_one_article
    Creating table many_to_one_country
    Creating table many_to_one_city
    Creating table many_to_one_district
    Creating table many_to_one_first
    Creating table many_to_one_second
    Creating table many_to_one_third
    Creating table many_to_one_parent
    Creating table many_to_one_child
    Creating table many_to_one_childnullableparent
    Creating table many_to_one_tofieldchild
    Creating table many_to_one_category
    Creating table many_to_one_record
    Creating table many_to_one_relation
    Creating table many_to_one_school
    Creating table many_to_one_student
    Running deferred SQL...
Running migrations:
  Applying admin.0001_initial... OK
  Applying admin.0002_logentry_remove_auto_add... OK
  Applying admin.0003_logentry_add_action ...
```
</details>


**[90] assistant:** 

<details><summary>[91] tool</summary>

```
Exit code: 0

Test case: Default value for pk should work
item.category_id = 'electronics'
item.category.pk = 'electronics'
Test PASSED!
```
</details>


**[92] assistant:** 

<details><summary>[93] tool</summary>

```
Exit code: 0

Test case: to_field should work
order.product_id = 'foo'
order.product.sku = 'foo'
Test PASSED!
```
</details>


**[94] assistant:** 

<details><summary>[95] tool</summary>

```
Exit code: 0

Cloning test database for alias 'default' ('file:memorydb_default?mode=memory&cache=shared')...
Cloning test database for alias 'default' ('file:memorydb_default?mode=memory&cache=shared')...
Cloning test database for alias 'default' ('file:memorydb_default?mode=memory&cache=shared')...
System check identified no issues (0 silenced).
test_check_composite_foreign_object (foreign_object.tests.TestModelCheckTests.test_check_composite_foreign_object) ... ok
test_check_subset_composite_foreign_object (foreign_object.tests.TestModelCheckTests.test_check_subset_composite_foreign_object) ... ok
test_deep_mixed_backward (foreign_object.test_agnostic_order_trimjoin.TestLookupQuery.test_deep_mixed_backward) ... ok
test_deep_mixed_forward (foreign_object.test_agnostic_order_trimjoin.TestLookupQuery.test_deep_mixed_forward) ... ok
test_empty_join_conditions (foreign_object.test_empty_join.RestrictedConditionsTests.test_empty_join_conditions) ... ok
test_restrictions_with_no_joining_columns (foreign_object.test_empty_join.RestrictedConditionsTests.test_restrictions_with_no_joining_columns)
It's possible to create a working related field that doesn't ... ok
test_extra_join_filter_q (foreign_object.tests.TestExtraJoinFilterQ.test_extra_join_filter_q) ... ok
test_batch_create_foreign_object (foreign_object.tests.MultiColumnFKTests.test_batch_create_foreign_object) ... ok
test_double_nested_query (foreign_object.tests.MultiColumnFKTests.test_double_nested_query) ... ok
test_forei ...
```
</details>


**[96] assistant:** 

<details><summary>[97] tool</summary>

```
Exit code: 0


```
</details>


**[98] assistant:** 

<details><summary>[99] tool</summary>

```
Exit code: 0

def _prepare_related_fields_for_save(self, operation_name):
        # Ensure that a model instance without a PK hasn't been assigned to
        # a ForeignKey or OneToOneField on this model. If the field is
        # nullable, allowing the save would result in silent data loss.
        for field in self._meta.concrete_fields:
            # If the related field isn't cached, then an instance hasn't been
            # assigned and there's no need to worry about this check.
            if field.is_relation and field.is_cached(self):
                obj = getattr(self, field.name, None)
                if not obj:
                    continue
                # A pk may have been assigned manually to a model instance not
                # saved to the database (or auto-generated in a case like
                # UUIDField), but we allow the save to proceed and rely on the
                # database to raise an IntegrityError if applicable. If
                # constraints aren't supported by the database, there's the
                # unavoidable risk of data corruption.
                if obj.pk is None:
                    # Remove the object from a related instance cache.
                    if not field.remote_field.multiple:
                        field.remote_field.delete_cached_value(obj)
                    raise ValueError(
                        "%s() prohibited to prevent data loss due to unsaved "
                        "related object '%s'." % (operati ...
```
</details>


**[100] assistant:** 

<details><summary>[101] tool</summary>

```
Exit code: 0

diff --git a/django/db/models/base.py b/django/db/models/base.py
index 2428218696..80f0b09b0b 100644
--- a/django/db/models/base.py
+++ b/django/db/models/base.py
@@ -933,10 +933,12 @@ class Model(metaclass=ModelBase):
                         "%s() prohibited to prevent data loss due to unsaved "
                         "related object '%s'." % (operation_name, field.name)
                     )
-                elif getattr(self, field.attname) is None:
-                    # Use pk from related object if it has been saved after
-                    # an assignment.
-                    setattr(self, field.attname, obj.pk)
+                # If the foreign key value doesn't match the related object's
+                # pk, update it. This handles the case when the related object
+                # was saved after assignment, or when its pk was changed after
+                # assignment.
+                if getattr(self, field.attname) in (None, '') or getattr(obj, field.target_field.attname) != getattr(self, field.attname):
+                    setattr(self, field.attname, getattr(obj, field.target_field.attname))
                 # If the relationship's pk/to_field was changed, clear the
                 # cached relationship.
                 if getattr(obj, field.target_field.attname) != getattr(self, field.attname):
```
</details>


**[102] assistant:** 

<details><summary>[103] tool</summary>

```
Exit code: 0


```
</details>


**[104] assistant:** 

<details><summary>[105] tool</summary>

```
Exit code: 0

Assigning an unsaved object to GenericForeignKey should raise an ... ok
test_update_or_create_defaults (generic_relations.tests.GenericRelationsTests.test_update_or_create_defaults) ... ok
test_absolute_max (generic_relations.test_forms.GenericInlineFormsetTests.test_absolute_max) ... ok
test_absolute_max_with_max_num (generic_relations.test_forms.GenericInlineFormsetTests.test_absolute_max_with_max_num) ... ok
test_can_delete_extra (generic_relations.test_forms.GenericInlineFormsetTests.test_can_delete_extra) ... ok
test_disable_delete_extra (generic_relations.test_forms.GenericInlineFormsetTests.test_disable_delete_extra) ... ok
test_get_queryset_ordering (generic_relations.test_forms.GenericInlineFormsetTests.test_get_queryset_ordering)
BaseGenericInlineFormSet.get_queryset() adds default ordering, if ... ok
test_incorrect_content_type (generic_relations.test_forms.GenericInlineFormsetTests.test_incorrect_content_type) ... ok
test_initial (generic_relations.test_forms.GenericInlineFormsetTests.test_initial) ... ok
test_initial_count (generic_relations.test_forms.GenericInlineFormsetTests.test_initial_count) ... ok
test_meta_widgets (generic_relations.test_forms.GenericInlineFormsetTests.test_meta_widgets)
TaggedItemForm has a widget defined in Meta. ... ok
test_options (generic_relations.test_forms.GenericInlineFormsetTests.test_options) ... ok
test_output (generic_relations.test_forms.GenericInlineFormsetTests.test_output) ... ok
test_save_as_new (generic_re ...
```
</details>


**[106] assistant:** 

<details><summary>[107] tool</summary>

```
Exit code: 0

Destroying test database for alias 'default'...
Destroying test database for alias 'default'...
Destroying test database for alias 'default'...
Destroying test database for alias 'default'...
Destroying test database for alias 'default'...
Destroying test database for alias 'default'...
Destroying test database for alias 'default'...
Destroying test database for alias 'default'...
Destroying test database for alias 'default'...
Destroying test database for alias 'default'...
Destroying test database for alias 'default'...
Destroying test database for alias 'default'...
Destroying test database for alias 'default'...
Destroying test database for alias 'default'...
Destroying test database for alias 'default'...
Destroying test database for alias 'default'...
Destroying test database for alias 'default'...
Destroying test database for alias 'default'...
Destroying test database for alias 'default'...
Destroying test database for alias 'default'...
```
</details>


**[108] assistant:** 

<details><summary>[109] tool</summary>

```
Exit code: 0

OK (skipped=13, expected failures=2)
```
</details>


**[110] assistant:** 

<details><summary>[111] tool</summary>

```
Exit code: 0

Testing the exact scenario from the PR description:
SUCCESS: The issue is fixed!
  order.product_id = 'foo'
  order.product.pk = 'foo'
```
</details>


**[112] assistant:** 

<details><summary>[113] tool</summary>

```
Exit code: 0


```
</details>


**[114] assistant:** 

<details><summary>[115] tool</summary>

```
Exit code: 0

diff --git a/django/db/models/base.py b/django/db/models/base.py
index 2428218696..80f0b09b0b 100644
--- a/django/db/models/base.py
+++ b/django/db/models/base.py
@@ -933,10 +933,12 @@ class Model(metaclass=ModelBase):
                         "%s() prohibited to prevent data loss due to unsaved "
                         "related object '%s'." % (operation_name, field.name)
                     )
-                elif getattr(self, field.attname) is None:
-                    # Use pk from related object if it has been saved after
-                    # an assignment.
-                    setattr(self, field.attname, obj.pk)
+                # If the foreign key value doesn't match the related object's
+                # pk, update it. This handles the case when the related object
+                # was saved after assignment, or when its pk was changed after
+                # assignment.
+                if getattr(self, field.attname) in (None, '') or getattr(obj, field.target_field.attname) != getattr(self, field.attname):
+                    setattr(self, field.attname, getattr(obj, field.target_field.attname))
                 # If the relationship's pk/to_field was changed, clear the
                 # cached relationship.
                 if getattr(obj, field.target_field.attname) != getattr(self, field.attname):
```
</details>


**[116] assistant:** 

<details><summary>[117] tool</summary>

```
Exit code: 0

diff --git a/django/db/models/base.py b/django/db/models/base.py
index 2428218696..80f0b09b0b 100644
--- a/django/db/models/base.py
+++ b/django/db/models/base.py
@@ -933,10 +933,12 @@ class Model(metaclass=ModelBase):
                         "%s() prohibited to prevent data loss due to unsaved "
                         "related object '%s'." % (operation_name, field.name)
                     )
-                elif getattr(self, field.attname) is None:
-                    # Use pk from related object if it has been saved after
-                    # an assignment.
-                    setattr(self, field.attname, obj.pk)
+                # If the foreign key value doesn't match the related object's
+                # pk, update it. This handles the case when the related object
+                # was saved after assignment, or when its pk was changed after
+                # assignment.
+                if getattr(self, field.attname) in (None, '') or getattr(obj, field.target_field.attname) != getattr(self, field.attname):
+                    setattr(self, field.attname, getattr(obj, field.target_field.attname))
                 # If the relationship's pk/to_field was changed, clear the
                 # cached relationship.
                 if getattr(obj, field.target_field.attname) != getattr(self, field.attname):
```
</details>


**[118] assistant:** 

<details><summary>[119] tool</summary>

```
Exit code: 0

On branch main
Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes in working directory)
	modified:   django/db/models/base.py

no changes added to commit (use "git add" and/or "git commit -a")
```
</details>


**[120] assistant:** 
