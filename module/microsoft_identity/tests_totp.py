import re

from django.test import SimpleTestCase

from module.microsoft_identity.domain.totp import generate_secret, otpauth_uri, totp_code

# Clé de test de la RFC 6238 : ASCII « 12345678901234567890 » encodée en base32.
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


class TotpTests(SimpleTestCase):
    def test_rfc6238_vectors(self):
        # Valeurs de l'annexe B de la RFC (SHA-1), tronquées à 6 chiffres.
        for at, expected in ((59, "287082"), (1111111109, "081804"), (1111111111, "050471"),
                             (1234567890, "005924"), (2000000000, "279037")):
            self.assertEqual(totp_code(RFC_SECRET, at), expected, msg=str(at))

    def test_code_changes_each_30_seconds_and_is_6_digits(self):
        a, b, c = totp_code(RFC_SECRET, 60), totp_code(RFC_SECRET, 89), totp_code(RFC_SECRET, 90)
        self.assertEqual(a, b)
        self.assertNotEqual(b, c)
        self.assertRegex(a, r"^\d{6}$")

    def test_generated_secret_is_valid_base32_and_unique(self):
        s = generate_secret()
        self.assertRegex(s, r"^[A-Z2-7]{32}$")
        self.assertNotEqual(s, generate_secret())
        totp_code(s)  # décodable

    def test_otpauth_uri(self):
        uri = otpauth_uri("OverView", "bob@cimtech.ca", RFC_SECRET)
        self.assertTrue(uri.startswith("otpauth://totp/OverView%3Abob%40cimtech.ca?"))
        for part in (f"secret={RFC_SECRET}", "issuer=OverView", "algorithm=SHA1", "digits=6", "period=30"):
            self.assertIn(part, uri)
