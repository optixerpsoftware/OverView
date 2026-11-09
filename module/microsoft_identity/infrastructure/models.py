from django.db import models

from shared_kernel.infrastructure.base import BaseModel


class Device(BaseModel):
    row_id = models.UUIDField(max_length=36)
    device_id = models.UUIDField(max_length=36)
    device_name = models.CharField(max_length=255)
    domain_name = models.CharField(max_length=255)

    delete_datetime = models.DateTimeField(blank=True, null=True)
    account_enabled = models.BooleanField(default=True)
    last_sign_in_datetime = models.DateTimeField(blank=True, null=True)
    compliance_expiration_datetime = models.DateTimeField(blank=True, null=True)
    created_datetime = models.DateTimeField(auto_now_add=True)
    device_category = models.CharField(max_length=255)
    device_metadata = models.JSONField(blank=True, null=True)
    device_ownership = models.CharField(max_length=255)


    class Meta:
        db_table = 'organization'


