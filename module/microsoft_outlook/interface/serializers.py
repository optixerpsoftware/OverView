from rest_framework import serializers

from module.microsoft_outlook.infrastructure.models import EmailQueue, Mailbox, OutlookRules


class MailboxSerializer(serializers.ModelSerializer):
    class Meta:
        model = Mailbox
        fields = [
            'id', 'org_id', 'connector_id', 'user_principal_name', 'graph_user_id',
            'is_active', 'last_synced_at', 'create_at', 'modified_at',
        ]
        read_only_fields = ['id', 'graph_user_id', 'last_synced_at', 'create_at', 'modified_at']


class OutlookRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutlookRules
        fields = [
            'id', 'mailbox', 'rule_id', 'display_name', 'sequence', 'is_enabled',
            'has_error', 'is_read_only', 'conditions', 'actions',
            'create_at', 'modified_at',
        ]
        read_only_fields = ['id', 'create_at', 'modified_at']


class EmailQueueSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailQueue
        fields = ['id', 'org_id', 'email', 'description', 'create_at', 'modified_at']
        read_only_fields = ['id', 'create_at', 'modified_at']
