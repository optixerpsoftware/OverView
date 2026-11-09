from django.db import models

from shared_kernel.infrastructure.base import BaseModel


class Organization(BaseModel):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)

    class Meta:
        db_table = 'organization'


