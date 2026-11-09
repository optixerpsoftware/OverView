from rest_framework import serializers

from module.microsoft_connector.infrastructure.models import MicrosoftConnector


class MicrosoftConnectorSerializer(serializers.ModelSerializer):
    # org_id = Organization.id ; le secret est en écriture seule (jamais renvoyé).
    client_secret = serializers.CharField(write_only=True, trim_whitespace=True)

    class Meta:
        model = MicrosoftConnector
        fields = [
            'id', 'org_id', 'name', 'tenant_id', 'client_id', 'client_secret',
            'is_active', 'last_checked_at', 'last_error',
            'create_at', 'modified_at',
        ]
        read_only_fields = ['id', 'last_checked_at', 'last_error', 'create_at', 'modified_at']

    def validate_org_id(self, value):
        if value is None:
            raise serializers.ValidationError("org_id est requis.")
        return value
