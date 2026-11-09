from django.db import models
import uuid


class IdentifierModel(models.Model):
    """
    Provides an abstract base model for identifier-based models.

    This class serves as a base model for other models that require a unique
    identifier using a UUID. It simplifies the process of adding a UUID-based
    primary key to models while ensuring immutable and non-editable identifiers.

    :ivar id: The universally unique identifier (UUID) serving as the primary key.
    :type id: UUID
    """
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    class Meta:
        abstract = True


class BaseModel(IdentifierModel):
    org_id = models.UUIDField(null=True)

    create_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True