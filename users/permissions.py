# v1.0.3 — role gates for endpoints.
#
# These gate *access*. They do not enforce business rules — the discount cap,
# the no-selling-below-zero rule, the frozen-after-posting rule and so on live
# in the service layer, so they hold no matter where the call comes from.
from rest_framework.permissions import SAFE_METHODS, BasePermission

from users.scopes import has_scope


class IsAdmin(BasePermission):
    message = "Only an Admin may do this."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_admin)


class IsAdminOrReadOnly(BasePermission):
    """Everyone signed in may read; only an Admin may write.

    Used for the catalogue, customers and suppliers.
    """

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        return request.user.is_admin


class HasScope(BasePermission):
    """Checks view.required_scope against the role's scope list; a tuple of
    scopes lets in a user holding any one of them."""

    message = "Your role does not allow this."

    def has_permission(self, request, view):
        scope = getattr(view, "required_scope", None)
        if scope is None:
            return True
        scopes = (scope,) if isinstance(scope, str) else scope
        return bool(
            request.user
            and request.user.is_authenticated
            and any(has_scope(request.user, s) for s in scopes)
        )


class HasReadWriteScope(BasePermission):
    """Reads need view.read_scope; anything else needs view.write_scope. A
    view that names a delete_scope needs that one for DELETE instead.

    Used for the stock area, so who may open it is decided by scopes.py rather
    than by a role check written into the view.
    """

    message = "Your role does not allow this."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            scope = view.read_scope
        elif request.method == "DELETE" and getattr(view, "delete_scope", None):
            scope = view.delete_scope
        else:
            scope = view.write_scope
        return has_scope(user, scope)


class CanVoidInvoice(BasePermission):
    """Object-level rule for voiding.

    An Admin may void any completed invoice. A Seller may void only their own,
    and only on the same calendar day it was raised — there are no cash
    sessions in this system, so the day is the window.

    Lives here rather than in the sales app so every role rule is in one file.
    """

    message = "You can only void your own invoice, on the day it was raised."

    def has_object_permission(self, request, view, obj):
        from django.utils import timezone

        user = request.user
        if not (user and user.is_authenticated):
            return False
        if getattr(obj, "status", None) != "COMPLETED":
            return False
        if user.is_admin:
            return True
        same_seller = getattr(obj, "seller_id", None) == user.id
        raised = getattr(obj, "sale_date", None)
        same_day = bool(raised) and timezone.localdate(raised) == timezone.localdate()
        return same_seller and same_day
