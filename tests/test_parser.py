import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from mihomo_editor import parse_wireguard, parse_vless


class TestParsers(unittest.TestCase):
    def test_amnezia_v3_1(self):
        conf = """[Interface]
Address = 10.8.1.3/32
DNS = 1.1.1.1, 1.0.0.1
PrivateKey = LLPyrNihPJ1HbktDkTyDCgcvj8RjNMcwRikxJ9iJ3CM=
Jc = 6
Jmin = 10
Jmax = 50
S1 = 122
S2 = 123
S3 = 19
S4 = 12
H1 = 1
H2 = 2
H3 = 3
H4 = 4
HeaderProtectionKey = AJTKKRTFcnTDxX7JRdiJIy2ZdLRjiL3ois0LopSDY0w=
ContentPaddingAddition = 10-100
RekeyAfterTime = 100-120
RekeyTimeout = 3-7
RejectAfterTime = 150-180
KeepaliveTimeout = 5-15
MaxHandshakeAttempts = 15-20
RandomTrailers = on
DisableCookies = on

[Peer]
PublicKey = SYgy+lyZkNV52GnJQckRsjUUInnOs4l0KEgXyZTponI=
PresharedKey = usa9qDYUqJUH64uzMmq9Q6gfRvkT1PW1aQO3Hc3yiqk=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 77.91.66.82:43095
PersistentKeepalive = 25-35
"""
        res, err = parse_wireguard(conf, custom_name="TestAWG31")
        self.assertIsNone(err)
        self.assertIsNotNone(res)
        y = res["yaml"]
        self.assertIn('header-protection-key: AJTKKRTFcnTDxX7JRdiJIy2ZdLRjiL3ois0LopSDY0w=', y)
        self.assertIn('content-padding-addition: 10-100', y)
        self.assertIn('random-trailers: true', y)
        self.assertIn('disable-cookies: true', y)
        self.assertIn('rekey-after-time: 100-120', y)
        self.assertIn('rekey-timeout: 3-7', y)
        self.assertIn('reject-after-time: 150-180', y)
        self.assertIn('keepalive-timeout: 5-15', y)
        self.assertIn('max-handshake-attempts: 15-20', y)
        self.assertIn('persistent-keepalive: 25', y)
        self.assertIn('version: 3', y)
        self.assertEqual(res.get("protocol"), "AmneziaWG v3.1")

    def test_wireguard_classic(self):
        conf = """[Interface]
Address = 10.0.0.2/32
PrivateKey = aaaaaa=

[Peer]
PublicKey = bbbbbb=
Endpoint = 1.2.3.4:51820
AllowedIPs = 0.0.0.0/0
"""
        res, err = parse_wireguard(conf, custom_name="ClassicWG")
        self.assertIsNone(err)
        self.assertIsNotNone(res)
        self.assertNotIn('amnezia-wg-option', res["yaml"])
        self.assertEqual(res.get("protocol"), "WireGuard Classic")

    def test_real_amnez_conf_file(self):
        real_file = r"D:\Data\Downloads\Telegram Desktop\amnez.conf"
        if os.path.exists(real_file):
            with open(real_file, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            res, err = parse_wireguard(content)
            self.assertIsNone(err)
            self.assertIsNotNone(res)
            self.assertEqual(res.get("protocol"), "AmneziaWG v3.1")
            self.assertIn("version: 3", res["yaml"])
            self.assertIn("header-protection-key: AJTKKRTFcnTDxX7JRdiJIy2ZdLRjiL3ois0LopSDY0w=", res["yaml"])
            self.assertIn("random-trailers: true", res["yaml"])
            self.assertIn("disable-cookies: true", res["yaml"])


if __name__ == '__main__':
    unittest.main()
