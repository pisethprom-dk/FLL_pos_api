# v1.0.0 — view mixins shared by every app
class ActiveFilterMixin:
    """?active=true or ?active=false narrows a list to live or retired rows."""

    def get_queryset(self):
        qs = super().get_queryset()
        active = self.request.query_params.get("active")
        if active == "true":
            qs = qs.filter(is_active=True)
        elif active == "false":
            qs = qs.filter(is_active=False)
        return qs


class AuditMixin:
    """Stamps created_by and updated_by from the signed-in user."""

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)
