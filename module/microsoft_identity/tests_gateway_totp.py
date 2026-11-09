import re

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from module.microsoft_identity.domain.entities import UserSecurityProfile
from module.microsoft_identity.infrastructure.graph_gateway import GraphIdentityGateway
from shared_kernel.graph_rest import BETA

SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


class StubRest:
    """Enregistre les appels envoyés à Graph (aucun réseau)."""
    def __init__(self, fail_assign=False):
        self.calls, self.fail_assign = [], fail_assign

    async def post(self, path, body, *, requires="", base=None):
        self.calls.append(("POST", path, body, base))
        if path.endswith("hardwareOathDevices"):
            return {"id": "dev-123"}
        if self.fail_assign:
            raise RuntimeError("boom")
        return {}

    async def delete(self, path, *, requires="", base=None):
        self.calls.append(("DELETE", path, None, base))


def gateway(stub):
    gw = GraphIdentityGateway.__new__(GraphIdentityGateway)   # sans client Graph réel
    gw.rest = stub

    async def profile(upn):
        return UserSecurityProfile("user-id", "Bob", True, None, None)

    gw.get_profile = profile
    return gw


class AddTotpGatewayTests(SimpleTestCase):
    def test_create_then_assign_and_activate_on_the_users_oath_collection(self):
        stub = StubRest()
        async_to_sync(gateway(stub).add_totp_method)("bob@cimtech.ca", SECRET, "OV123", "Application TOTP")
        (m1, p1, b1, base1), (m2, p2, b2, base2) = stub.calls
        self.assertEqual((m1, p1, base1), ("POST", "/directory/authenticationMethodDevices/hardwareOathDevices", BETA))
        self.assertEqual((b1["secretKey"], b1["serialNumber"], b1["hashFunction"], b1["timeIntervalInSeconds"]),
                         (SECRET, "OV123", "hmacsha1", 30))
        # action liée à la collection de l'utilisateur (et non au jeton) : voir $metadata bêta
        self.assertEqual((m2, p2, base2),
                         ("POST", "/users/bob@cimtech.ca/authentication/hardwareOathMethods/assignAndActivate", BETA))
        self.assertEqual(b2["device"], {"id": "dev-123"})
        self.assertRegex(b2["verificationCode"], r"^\d{6}$")

    def test_orphan_device_is_deleted_when_assignment_fails(self):
        stub = StubRest(fail_assign=True)
        with self.assertRaises(RuntimeError):
            async_to_sync(gateway(stub).add_totp_method)("bob@cimtech.ca", SECRET, "OV123", "Application TOTP")
        self.assertEqual(stub.calls[-1][:2], ("DELETE", "/directory/authenticationMethodDevices/hardwareOathDevices/dev-123"))
