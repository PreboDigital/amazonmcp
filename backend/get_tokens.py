"""
Amazon LwA Token Exchange Script
Run this after getting your authorization code from the OAuth flow.

Usage:
    python3 get_tokens.py YOUR_AUTH_CODE

Requires environment variables (or a .env file):
    AMAZON_CLIENT_ID      — Your Amazon Ads OAuth client ID
    AMAZON_CLIENT_SECRET  — Your Amazon Ads OAuth client secret
    AMAZON_REDIRECT_URI   — (optional) defaults to https://localhost/callback
"""

import os
import sys
import json
import urllib.request
import urllib.parse

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional for this standalone script

# ── Credentials from environment ──────────────────────────────────────
CLIENT_ID = os.environ.get("AMAZON_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("AMAZON_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("AMAZON_REDIRECT_URI", "https://localhost/callback")

if not CLIENT_ID or not CLIENT_SECRET:
    print("ERROR: AMAZON_CLIENT_ID and AMAZON_CLIENT_SECRET environment variables are required.")
    print("Set them in your .env file or export them in your shell.")
    sys.exit(1)

# ── Step 1: Generate the authorization URL ────────────────────────────
AUTH_URL = (
    f"https://www.amazon.com/ap/oa"
    f"?client_id={CLIENT_ID}"
    f"&scope=advertising::campaign_management"
    f"&response_type=code"
    f"&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
)

TOKEN_URL = "https://api.amazon.com/auth/o2/token"


def exchange_code(auth_code: str) -> dict:
    """Exchange authorization code for access + refresh tokens."""
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }).encode()

    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode())
            return result
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"\nError {e.code}: {error_body}")
        return {}


def refresh_access_token(refresh_token: str) -> dict:
    """Use refresh token to get a new access token."""
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }).encode()

    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode())
            return result
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"\nError {e.code}: {error_body}")
        return {}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("=" * 60)
        print("AMAZON ADS TOKEN EXCHANGE")
        print("=" * 60)
        print()
        print("STEP 1: Add this Return URL to your Security Profile:")
        print(f"  {REDIRECT_URI}")
        print()
        print("STEP 2: Open this URL in your browser and authorize:")
        print()
        print(AUTH_URL)
        print()
        print("STEP 3: After redirect, copy the 'code' parameter from the URL")
        print("  Example: https://localhost/callback?code=ANdNAVhyhqirUelHGEHA")
        print()
        print("STEP 4: Run this script with the code:")
        print(f"  python3 get_tokens.py YOUR_AUTH_CODE")
        print()
        print("  Or to refresh an existing token:")
        print(f"  python3 get_tokens.py --refresh YOUR_REFRESH_TOKEN")
        sys.exit(0)

    if sys.argv[1] == "--refresh":
        if len(sys.argv) < 3:
            print("Usage: python3 get_tokens.py --refresh YOUR_REFRESH_TOKEN")
            sys.exit(1)
        print("Refreshing access token...")
        tokens = refresh_access_token(sys.argv[2])
    else:
        auth_code = sys.argv[1]
        print(f"Exchanging authorization code for tokens...")
        tokens = exchange_code(auth_code)

    if tokens:
        print()
        print("=" * 60)
        print("SUCCESS! Your tokens:")
        print("=" * 60)
        print()
        if "access_token" in tokens:
            print(f"Access Token:  {tokens['access_token'][:50]}...")
        if "refresh_token" in tokens:
            print(f"Refresh Token: {tokens['refresh_token'][:50]}...")
        if "expires_in" in tokens:
            print(f"Expires In:    {tokens['expires_in']} seconds")
        if "token_type" in tokens:
            print(f"Token Type:    {tokens['token_type']}")
        print()
        print("Full response:")
        print(json.dumps(tokens, indent=2))
        print()
        print("Add these to your app via the Settings page or .env file.")
    else:
        print("\nFailed to get tokens. Check the error above.")
