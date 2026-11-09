from rest_framework import serializers

from module.organization.infrastructure.models import Organization


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = ['id', 'name', 'description', 'create_at', 'modified_at']
        read_only_fields = ['id', 'create_at', 'modified_at']
