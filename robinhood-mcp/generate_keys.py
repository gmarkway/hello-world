"""Make the Ed25519 key pair Robinhood's Crypto API needs.

Run:  python generate_keys.py
- Paste the PUBLIC key into Robinhood when you create the API credential.
- Keep the PRIVATE key secret. You'll put it in RH_PRIVATE_KEY. Never commit it.
"""

import base64

from nacl.signing import SigningKey

key = SigningKey.generate()
print("PUBLIC key (give this to Robinhood):")
print(base64.b64encode(bytes(key.verify_key)).decode())
print()
print("PRIVATE key (keep secret, goes in RH_PRIVATE_KEY):")
print(base64.b64encode(bytes(key)).decode())
