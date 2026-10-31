# Docent run — django__django-13964

resolved=0 turns=28 docent_run=71c78736-7500-4839-9bb9-ed2b67ef197d

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


**[2] assistant:** THOUGHT: I'll list the project files to find Django-like ORM code in testbed to reproduce and fix the issue. I'll search for model, ForeignKey, assignment, saving logic. I'll start by listing files.

<details><summary>[3] tool</summary>

```
Exit code: 0

total 176
drwxrwxrwx   1 root root  4096 Sep 10 11:37 .
drwxr-xr-x   1 root root  4096 Feb 16 06:39 ..
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


**[4] assistant:** THOUGHT: Search for foreign key related assignment descriptor implementation to locate where product_id is set during assignment. I'll grep for RelatedObject or ForwardManyToOneDescriptor.

<details><summary>[5] tool</summary>

```
Exit code: 0

django/contrib/admin/templatetags/admin_list.py:213:                if isinstance(f.remote_field, models.ManyToOneRel):
django/contrib/admin/views/main.py:20:from django.db.models import F, Field, ManyToOneRel, OrderBy
django/contrib/admin/views/main.py:515:                if isinstance(field.remote_field, ManyToOneRel):
django/contrib/contenttypes/fields.py:26:    ForwardManyToOneDescriptor) by adding itself as a model attribute.
django/core/serializers/base.py:243:            elif isinstance(field.remote_field, models.ManyToOneRel):
django/core/serializers/xml_serializer.py:226:            elif field.remote_field and isinstance(field.remote_field, models.ManyToOneRel):
django/core/serializers/python.py:132:            elif field.remote_field and isinstance(field.remote_field, models.ManyToOneRel):
django/db/models/__init__.py:34:    ForeignObjectRel, ManyToOneRel, ManyToManyRel, OneToOneRel,
django/db/models/__init__.py:51:    'ForeignObjectRel', 'ManyToOneRel', 'ManyToManyRel', 'OneToOneRel',
django/db/models/fields/related_descriptors.py:27:   ``ForwardManyToOneDescriptor``.
django/db/models/fields/related_descriptors.py:46:   slightly different from ``ForwardManyToOneDescriptor``.
django/db/models/fields/related_descriptors.py:82:class ForwardManyToOneDescriptor:
django/db/models/fields/related_descriptors.py:92:    ``Child.parent`` is a ``ForwardManyToOneDescriptor`` instance.
django/db/models/fields/related_descriptors.py:278:class ForwardOneToOneDescript ...
```
</details>


**[6] assistant:** 

<details><summary>[7] tool</summary>

```
Exit code: 0

None
```
</details>


**[8] assistant:** 

<details><summary>[9] tool</summary>

```
Exit code: 0

None
```
</details>


**[10] assistant:** 

<details><summary>[11] tool</summary>

```
Exit code: 0

import functools
import inspect
from functools import partial

from django import forms
from django.apps import apps
from django.conf import SettingsReference, settings
from django.core import checks, exceptions
from django.db import connection, router
from django.db.backends import utils
from django.db.models import Q
from django.db.models.constants import LOOKUP_SEP
from django.db.models.deletion import CASCADE, SET_DEFAULT, SET_NULL
from django.db.models.query_utils import PathInfo
from django.db.models.utils import make_model_tuple
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from . import Field
from .mixins import FieldCacheMixin
from .related_descriptors import (
    ForeignKeyDeferredAttribute, ForwardManyToOneDescriptor,
    ForwardOneToOneDescriptor, ManyToManyDescriptor,
    ReverseManyToOneDescriptor, ReverseOneToOneDescriptor,
)
from .related_lookups import (
    RelatedExact, RelatedGreaterThan, RelatedGreaterThanOrEqual, RelatedIn,
    RelatedIsNull, RelatedLessThan, RelatedLessThanOrEqual,
)
from .reverse_related import (
    ForeignObjectRel, ManyToManyRel, ManyToOneRel, OneToOneRel,
)

RECURSIVE_RELATIONSHIP_CONSTANT = 'self'


def resolve_relation(scope_model, relation):
    """
    Transform relation into a model or fully-qualified model string of the form
    "app_label.ModelName", relative to scope_model.

    The relation argument can be:
      * RECURSIVE_RELATIONSHIP_CONSTANT, ...
```
</details>


**[12] assistant:** 

<details><summary>[13] tool</summary>

```
Exit code: 0

None
```
</details>


**[14] assistant:** 

<details><summary>[15] tool</summary>

```
Exit code: 0

django/contrib/contenttypes/fields.py:256:        self.set_cached_value(instance, value)
django/db/models/fields/related_descriptors.py:261:        self.field.set_cached_value(instance, value)
django/db/models/fields/related_descriptors.py:483:            self.related.set_cached_value(instance, value)
```
</details>


**[16] assistant:** 

<details><summary>[17] tool</summary>

```
Exit code: 0

django/db/models/fields/related_descriptors.py:114:    def is_cached(self, instance):
django/db/models/fields/related_descriptors.py:359:    def is_cached(self, instance):
django/db/models/fields/mixins.py:21:    def is_cached(self, instance):
django/db/models/fields/mixins.py:24:    def set_cached_value(self, instance, value):
```
</details>


**[18] assistant:** 

<details><summary>[19] tool</summary>

```
Exit code: 0

from django.core import checks

NOT_PROVIDED = object()


class FieldCacheMixin:
    """Provide an API for working with the model's fields value cache."""

    def get_cache_name(self):
        raise NotImplementedError

    def get_cached_value(self, instance, default=NOT_PROVIDED):
        cache_name = self.get_cache_name()
        try:
            return instance._state.fields_cache[cache_name]
        except KeyError:
            if default is NOT_PROVIDED:
                raise
            return default

    def is_cached(self, instance):
        return self.get_cache_name() in instance._state.fields_cache

    def set_cached_value(self, instance, value):
        instance._state.fields_cache[self.get_cache_name()] = value

    def delete_cached_value(self, instance):
        del instance._state.fields_cache[self.get_cache_name()]


class CheckFieldDefaultMixin:
    _default_hint = ('<valid default>', '<invalid default>')

    def _check_default(self):
        if self.has_default() and self.default is not None and not callable(self.default):
            return [
                checks.Warning(
                    "%s default should be a callable instead of an instance "
                    "so that it's not shared between all field instances." % (
                        self.__class__.__name__,
                    ),
                    hint=(
                        'Use a callable instead, e.g., use `%s` instead of '
                        '`%s`.' % sel ...
```
</details>


**[20] assistant:** 

<details><summary>[21] tool</summary>

```
Exit code: 0

grep: django: Is a directory
```
</details>


**[22] assistant:** 

<details><summary>[23] tool</summary>

```
Exit code: 0

django/db/models/fields/related_descriptors.py:75:class ForeignKeyDeferredAttribute(DeferredAttribute):
django/db/models/fields/related.py:22:    ForeignKeyDeferredAttribute, ForwardManyToOneDescriptor,
django/db/models/fields/related.py:779:    descriptor_class = ForeignKeyDeferredAttribute
```
</details>


**[24] assistant:** 

<details><summary>[25] tool</summary>

```
Exit code: 0

direct=True,
            filtered_relation=filtered_relation,
        )]

    def get_reverse_path_info(self, filtered_relation=None):
        """Get path from the related model to this field's model."""
        opts = self.model._meta
        from_opts = self.remote_field.model._meta
        return [PathInfo(
            from_opts=from_opts,
            to_opts=opts,
            target_fields=(opts.pk,),
            join_field=self.remote_field,
            m2m=not self.unique,
            direct=False,
            filtered_relation=filtered_relation,
        )]

    @classmethod
    @functools.lru_cache(maxsize=None)
    def get_lookups(cls):
        bases = inspect.getmro(cls)
        bases = bases[:bases.index(ForeignObject) + 1]
        class_lookups = [parent.__dict__.get('class_lookups', {}) for parent in bases]
        return cls.merge_dicts(class_lookups)

    def contribute_to_class(self, cls, name, private_only=False, **kwargs):
        super().contribute_to_class(cls, name, private_only=private_only, **kwargs)
        setattr(cls, self.name, self.forward_related_accessor_class(self))

    def contribute_to_related_class(self, cls, related):
        # Internal FK's - i.e., those with a related name ending with '+' -
        # and swapped models don't get a related descriptor.
        if not self.remote_field.is_hidden() and not related.related_model._meta.swapped:
            setattr(cls._meta.concrete_model, related.get_accessor_name(), self.related_ ...
```
</details>


**[26] assistant:** 

<details><summary>[27] tool</summary>

```
Exit code: 0

django/contrib/admin/options.py:2096:                    if self.instance._state.adding:
django/contrib/admin/options.py:2126:                if not can_change and not self.instance._state.adding:
django/contrib/admin/options.py:2128:                if not can_add and self.instance._state.adding:
django/contrib/contenttypes/fields.py:615:                    if obj._state.adding or obj._state.db != db:
django/forms/models.py:461:                    'created' if self.instance._state.adding else 'changed',
django/forms/models.py:844:                pk_value = None if form.instance._state.adding else form.instance.pk
django/forms/models.py:982:        if self.instance._state.adding:
django/db/models/base.py:516:        new._state.adding = False
django/db/models/base.py:770:        self._state.adding = False
django/db/models/base.py:834:            self._state.adding and
django/db/models/base.py:1099:                if f.primary_key and not self._state.adding:
django/db/models/base.py:1117:            if not self._state.adding and model_class_pk is not None:
django/db/models/base.py:1148:            if not self._state.adding and self.pk is not None:
django/db/models/query.py:510:                    obj_with_pk._state.adding = False
django/db/models/query.py:522:                    obj_without_pk._state.adding = False
django/db/models/fields/related_descriptors.py:304:                obj._state.adding = instance._state.adding
django/db/models/fields/related_descriptor ...
```
</details>


**[28] assistant:** 

<details><summary>[29] tool</summary>

```
Exit code: 0

import copy
import inspect
import warnings
from functools import partialmethod
from itertools import chain

import django
from django.apps import apps
from django.conf import settings
from django.core import checks
from django.core.exceptions import (
    NON_FIELD_ERRORS, FieldDoesNotExist, FieldError, MultipleObjectsReturned,
    ObjectDoesNotExist, ValidationError,
)
from django.db import (
    DEFAULT_DB_ALIAS, DJANGO_VERSION_PICKLE_KEY, DatabaseError, connection,
    connections, router, transaction,
)
from django.db.models import (
    NOT_PROVIDED, ExpressionWrapper, IntegerField, Max, Value,
)
from django.db.models.constants import LOOKUP_SEP
from django.db.models.constraints import CheckConstraint, UniqueConstraint
from django.db.models.deletion import CASCADE, Collector
from django.db.models.fields.related import (
    ForeignObjectRel, OneToOneField, lazy_related_operation, resolve_relation,
)
from django.db.models.functions import Coalesce
from django.db.models.manager import Manager
from django.db.models.options import Options
from django.db.models.query import F, Q
from django.db.models.signals import (
    class_prepared, post_init, post_save, pre_init, pre_save,
)
from django.db.models.utils import make_model_tuple
from django.utils.encoding import force_str
from django.utils.hashable import make_hashable
from django.utils.text import capfirst, get_text_list
from django.utils.translation import gettext_lazy as _


class Deferred:
    def __repr__ ...
```
</details>


**[30] assistant:** 

<details><summary>[31] tool</summary>

```
Exit code: 0

grep: django: Is a directory
```
</details>


**[32] assistant:** 

<details><summary>[33] tool</summary>

```
Exit code: 0

grep: Unmatched ( or \(
```
</details>


**[34] assistant:** 

<details><summary>[35] tool</summary>

```
Exit code: 0

django/contrib/admin/options.py:            return self.response_post_save_add(request, obj)
django/contrib/admin/options.py:            return self.response_post_save_change(request, obj)
django/contrib/admin/options.py:    def _response_post_save(self, request, obj):
django/contrib/admin/options.py:    def response_post_save_add(self, request, obj):
django/contrib/admin/options.py:        return self._response_post_save(request, obj)
django/contrib/admin/options.py:    def response_post_save_change(self, request, obj):
django/contrib/admin/options.py:        return self._response_post_save(request, obj)
django/core/serializers/base.py:        # raw=True is passed to any pre/post_save signals.
django/dispatch/dispatcher.py:        @receiver(post_save, sender=MyModel)
django/dispatch/dispatcher.py:        @receiver([post_save, post_delete], sender=MyModel)
django/db/models/base.py:    class_prepared, post_init, post_save, pre_init, pre_save,
django/db/models/base.py:            post_save.send(
django/db/models/query.py:        save() on each of the instances, do not send any pre/post_save
django/db/models/signals.py:post_save = ModelSignal(use_caching=True)
```
</details>


**[36] assistant:** 

<details><summary>[37] tool</summary>

```
Exit code: 0

None
```
</details>


**[38] assistant:** 

<details><summary>[39] tool</summary>

```
Exit code: 0

django/db/models/fields/__init__.py:589:    def get_pk_value_on_save(self, instance):
```
</details>


**[40] assistant:** 

<details><summary>[41] tool</summary>

```
Exit code: 0

import collections.abc
import copy
import datetime
import decimal
import operator
import uuid
import warnings
from base64 import b64decode, b64encode
from functools import partialmethod, total_ordering

from django import forms
from django.apps import apps
from django.conf import settings
from django.core import checks, exceptions, validators
from django.db import connection, connections, router
from django.db.models.constants import LOOKUP_SEP
from django.db.models.query_utils import DeferredAttribute, RegisterLookupMixin
from django.utils import timezone
from django.utils.datastructures import DictWrapper
from django.utils.dateparse import (
    parse_date, parse_datetime, parse_duration, parse_time,
)
from django.utils.duration import duration_microseconds, duration_string
from django.utils.functional import Promise, cached_property
from django.utils.ipv6 import clean_ipv6_address
from django.utils.itercompat import is_iterable
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _

__all__ = [
    'AutoField', 'BLANK_CHOICE_DASH', 'BigAutoField', 'BigIntegerField',
    'BinaryField', 'BooleanField', 'CharField', 'CommaSeparatedIntegerField',
    'DateField', 'DateTimeField', 'DecimalField', 'DurationField',
    'EmailField', 'Empty', 'Field', 'FilePathField', 'FloatField',
    'GenericIPAddressField', 'IPAddressField', 'IntegerField', 'NOT_PROVIDED',
    'NullBooleanField', 'PositiveBigIntegerField', 'PositiveIntegerFie ...
```
</details>


**[42] assistant:** 

<details><summary>[43] tool</summary>

```
Exit code: 0

def __copy__(self):
        # We need to avoid hitting __reduce__, so define this
        # slightly weird copy construct.
        obj = Empty()
        obj.__class__ = self.__class__
        obj.__dict__ = self.__dict__.copy()
        return obj

    def __reduce__(self):
        """
        Pickling should return the model._meta.fields instance of the field,
        not a new copy of that field. So, use the app registry to load the
        model and then the field back.
        """
        if not hasattr(self, 'model'):
            # Fields are sometimes used without attaching them to models (for
            # example in aggregation). In this case give back a plain field
            # instance. The code below will create a new empty instance of
            # class self.__class__, then update its dict with self.__dict__
            # values - so, this is very close to normal pickle.
            state = self.__dict__.copy()
            # The _get_default cached_property can't be pickled due to lambda
            # usage.
            state.pop('_get_default', None)
            return _empty, (self.__class__,), state
        return _load_field, (self.model._meta.app_label, self.model._meta.object_name,
                             self.name)

    def get_pk_value_on_save(self, instance):
        """
        Hook to generate new PK values on save. This method is called when
        saving instances with no primary key value set. If this method returns
        somet ...
```
</details>


**[44] assistant:** THOUGHT: I'll modify ForwardManyToOneDescriptor.__set__ so that when assigning a related instance, it gets the foreign related values via the field API (get_foreign_related_value) instead of direct getattr on fields. This will ensure primary key logic (like using instance.pk) is used. I'll create the patch by editing the file.

<details><summary>[45] tool</summary>

```
Exit code: 0

main
```
</details>


**[46] assistant:** 

<details><summary>[47] tool</summary>

```
Exit code: 128

error: unrecognized input
```
</details>


**[48] assistant:** 

<details><summary>[49] tool</summary>

```
Exit code: 0

patched
```
</details>


**[50] assistant:** 

<details><summary>[51] tool</summary>

```
Exit code: 0

diff --git a/django/db/models/fields/related_descriptors.py b/django/db/models/fields/related_descriptors.py
index 872a4c98dc..0bc5ceb195 100644
--- a/django/db/models/fields/related_descriptors.py
+++ b/django/db/models/fields/related_descriptors.py
@@ -251,10 +251,15 @@ class ForwardManyToOneDescriptor:
             for lh_field, rh_field in self.field.related_fields:
                 setattr(instance, lh_field.attname, None)
 
-        # Set the values of the related field.
+        # Set the values of the related field. Use the field API to obtain
+        # the values from the related instance. This ensures any primary key
+        # handling (for example, returning instance.pk for primary key
+        # fields) is respected instead of directly reading the attribute
+        # which may be out of sync for non-auto primary keys.
         else:
-            for lh_field, rh_field in self.field.related_fields:
-                setattr(instance, lh_field.attname, getattr(value, rh_field.attname))
+            foreign_vals = self.field.get_foreign_related_value(value)
+            for index, (lh_field, _) in enumerate(self.field.related_fields):
+                setattr(instance, lh_field.attname, foreign_vals[index])
 
         # Set the related instance cache used by __get__ to avoid an SQL query
         # when accessing the attribute we just set.
```
</details>


**[52] assistant:** THOUGHT: I'll create the patch file containing the diff for the modified source file(s) as required.

<details><summary>[53] tool</summary>

```
Exit code: 0

-rw-r--r-- 1 root root 0 Feb 16 06:40 patch.txt
```
</details>


**[54] assistant:** 

<details><summary>[55] tool</summary>

```
Exit code: 0

diff --git a/django/db/models/fields/related_descriptors.py b/django/db/models/fields/related_descriptors.py
index 872a4c98dc..0bc5ceb195 100644
--- a/django/db/models/fields/related_descriptors.py
+++ b/django/db/models/fields/related_descriptors.py
@@ -251,10 +251,15 @@ class ForwardManyToOneDescriptor:
             for lh_field, rh_field in self.field.related_fields:
                 setattr(instance, lh_field.attname, None)
 
-        # Set the values of the related field.
+        # Set the values of the related field. Use the field API to obtain
+        # the values from the related instance. This ensures any primary key
+        # handling (for example, returning instance.pk for primary key
+        # fields) is respected instead of directly reading the attribute
+        # which may be out of sync for non-auto primary keys.
         else:
-            for lh_field, rh_field in self.field.related_fields:
-                setattr(instance, lh_field.attname, getattr(value, rh_field.attname))
+            foreign_vals = self.field.get_foreign_related_value(value)
+            for index, (lh_field, _) in enumerate(self.field.related_fields):
+                setattr(instance, lh_field.attname, foreign_vals[index])
 
         # Set the related instance cache used by __get__ to avoid an SQL query
         # when accessing the attribute we just set.
```
</details>


**[56] assistant:** THOUGHT: I updated ForwardManyToOneDescriptor.__set__ to use the field API (get_foreign_related_value) when copying values from the related instance to the local instance. This ensures correct handling of primary key values (for example using instance.pk) instead of directly reading the attribute which may be out of sync for non-auto primary keys. Now I'll create and output the patch file contents as required.
