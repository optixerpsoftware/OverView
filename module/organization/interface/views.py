from shared_kernel.scope import OrgScopedQuerysetMixin
from rest_framework import status, viewsets
from rest_framework.response import Response

from module.microsoft_connector.application.services import ConnectorService
from module.microsoft_outlook.application.services import OutlookService
from module.organization.application.services import OrganizationMissing, OrganizationService
from module.organization.infrastructure.models import Organization
from module.organization.interface.serializers import OrganizationSerializer


class OrganizationViewSet(OrgScopedQuerysetMixin, viewsets.ModelViewSet):
    scope_field = 'id'
    """Lectures via le queryset (CQRS léger) ; la création passe par le service applicatif."""
    queryset = Organization.objects.all().order_by('name')
    serializer_class = OrganizationSerializer

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            entity = OrganizationService().create(**ser.validated_data)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        out = self.get_serializer(Organization.objects.get(pk=entity.id))
        return Response(out.data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        """Supprime l'organisation et ses données locales (Outlook d'abord, puis connecteurs).

        Racine de composition : c'est ici qu'on relie les contextes entre eux.
        Rien n'est supprimé côté Microsoft 365.
        """
        try:
            OrganizationService().delete(
                kwargs['pk'],
                purgers=[OutlookService().purge_org, ConnectorService().purge_org],
            )
        except OrganizationMissing:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)
