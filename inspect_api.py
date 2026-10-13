import json
from getpass import getpass

import requests


BASE_URL = (
    "https://vetone-api.kinddesert-e2c4fbdc"
    ".westeurope.azurecontainerapps.io"
)


def main():
    client_id = input("Client ID: ").strip()
    client_secret = input("Client secret: ").strip()

    with requests.Session() as session:
        # Authenticate. Allow time for the API to wake up.
        response = session.post(
            f"{BASE_URL}/auth/token",
            json={
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=(10, 60),
        )
        response.raise_for_status()

        auth = response.json()
        session.headers.update({
            "Authorization": f"Bearer {auth['access_token']}"
        })

        # Fetch the clinic response.
        response = session.get(
            f"{BASE_URL}/v1/clinics",
            timeout=(10, 60),
        )
        response.raise_for_status()

        print("\nCLINICS")
        print(json.dumps(response.json(), indent=2, ensure_ascii=False))

        # Inspect one small appointment page.
        # Without date filters, the API documents a last-7-days default.
        response = session.get(
            f"{BASE_URL}/v1/appointments",
            params={
                "from": "2026-10-05",
                "to": "2026-10-05",
                "limit": 5,
            },
            timeout=(10, 60),
        )
        response.raise_for_status()

        print("\nAPPOINTMENT SAMPLE")
        print(json.dumps(response.json(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()