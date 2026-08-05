import argparse
import getpass
import os
import secrets
import sys
import time
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv

from social_publishers import (
    AccountStore,
    PublishError,
    TikTokPublisher,
    discover_direct_instagram_account,
    discover_facebook_instagram_accounts,
    exchange_tiktok_code,
    tiktok_authorize_url,
)

load_dotenv()


def _required_env(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise PublishError(f"set {name} in .env first")
    return value


def _secret(env_name, prompt):
    value = os.getenv(env_name, "").strip()
    return value or getpass.getpass(prompt).strip()


def _code_and_state(value):
    value = value.strip()
    if "://" not in value:
        return value, None
    query = parse_qs(urlparse(value).query)
    if query.get("error"):
        raise PublishError(f"TikTok authorization failed: {query['error'][0]}")
    code = (query.get("code") or [""])[0]
    if not code:
        raise PublishError("the pasted redirect URL has no TikTok authorization code")
    return code, (query.get("state") or [None])[0]


def cmd_list(args, store):
    data = store.load()
    total = 0
    for platform in ("instagram", "tiktok"):
        print(f"{platform}:")
        for row in data[platform]:
            state = "enabled" if row.get("enabled", True) else "disabled"
            suffix = f" · {row.get('mode')}" if platform == "tiktok" else ""
            print(f"  - {row.get('name')} · {state}{suffix}")
            total += 1
        if not data[platform]:
            print("  (none)")
    print(f"\n{total} account(s) in {store.path}")


def cmd_instagram_facebook(args, store):
    token = _secret("META_USER_ACCESS_TOKEN", "Meta User Access Token (hidden): ")
    rows = discover_facebook_instagram_accounts(token, args.api_version)
    if not rows:
        raise PublishError("Meta returned no linked Instagram professional accounts")
    for row in rows:
        if args.prefix:
            row["name"] = f"{args.prefix}{row['name']}"
        store.upsert("instagram", row)
        print(f"added Instagram @{row['name']} ({row['user_id']})")
    print(f"added {len(rows)} Instagram account(s) with one Meta authorization")


def cmd_instagram_direct(args, store):
    token = _secret("INSTAGRAM_ACCESS_TOKEN", "Instagram access token (hidden): ")
    row = discover_direct_instagram_account(token, args.api_version)
    if args.name:
        row["name"] = args.name
    store.upsert("instagram", row)
    print(f"added Instagram @{row['name']} ({row['user_id']})")


def cmd_tiktok_start(args, store):
    client_key = _required_env("TIKTOK_CLIENT_KEY")
    redirect_uri = _required_env("TIKTOK_REDIRECT_URI")
    state = secrets.token_urlsafe(24)
    store.upsert(
        "tiktok",
        {
            "name": args.name,
            "mode": args.mode,
            "privacy_level": args.privacy,
            "pending_state": state,
            "enabled": False,
        },
    )
    print("Open this URL while logged into the TikTok account you want to connect:\n")
    print(tiktok_authorize_url(client_key, redirect_uri, state, args.mode))
    print("\nAfter approval, copy the complete redirected URL and run:")
    print(f"python setup_accounts.py tiktok-finish {args.name}")


def cmd_tiktok_finish(args, store):
    data = store.load()
    pending = next(
        (row for row in data["tiktok"] if row.get("name") == args.name), None
    )
    if not pending or not pending.get("pending_state"):
        raise PublishError(
            f"no pending TikTok authorization for {args.name}; run tiktok-start first"
        )
    redirected = (
        args.redirect_url or input("Paste the complete redirected URL: ").strip()
    )
    code, returned_state = _code_and_state(redirected)
    if returned_state and returned_state != pending["pending_state"]:
        raise PublishError(
            "TikTok OAuth state did not match; start the authorization again"
        )

    now = int(time.time())
    token = exchange_tiktok_code(
        code,
        _required_env("TIKTOK_CLIENT_KEY"),
        _required_env("TIKTOK_CLIENT_SECRET"),
        _required_env("TIKTOK_REDIRECT_URI"),
    )
    record = {
        "name": args.name,
        "mode": pending.get("mode", "direct"),
        "privacy_level": pending.get("privacy_level", "SELF_ONLY"),
        "open_id": token.get("open_id"),
        "access_token": token["access_token"],
        "refresh_token": token["refresh_token"],
        "expires_at": now + int(token.get("expires_in", 86400)),
        "refresh_expires_at": now + int(token.get("refresh_expires_in", 31536000)),
        "scope": token.get("scope", ""),
        "pending_state": None,
        "enabled": True,
    }
    store.upsert("tiktok", record)
    print(
        f"added TikTok {args.name} ({record['mode']} mode); token refresh is automatic"
    )


def cmd_set_enabled(args, store):
    store.update(args.platform, args.name, {"enabled": args.enabled})
    print(f"{args.platform}/{args.name}: {'enabled' if args.enabled else 'disabled'}")


def cmd_health(args, store):
    data = store.load()
    failures = 0
    for row in data["instagram"]:
        if not row.get("enabled", True):
            continue
        base = row.get("api_base", "https://graph.instagram.com").rstrip("/")
        version = row.get("api_version", os.getenv("INSTAGRAM_API_VERSION", "v24.0"))
        response = requests.get(
            f"{base}/{version}/{row['user_id']}",
            headers={"Authorization": f"Bearer {row['access_token']}"},
            params={"fields": "username"},
            timeout=60,
        )
        if response.status_code < 400:
            print(f"OK   instagram/{row['name']}")
        else:
            failures += 1
            print(f"FAIL instagram/{row['name']} (HTTP {response.status_code})")

    tiktok = TikTokPublisher(store)
    for row in data["tiktok"]:
        if not row.get("enabled", True):
            continue
        try:
            token = tiktok._refresh(row)
            response = requests.get(
                "https://open.tiktokapis.com/v2/user/info/",
                headers={"Authorization": f"Bearer {token}"},
                params={"fields": "open_id,display_name,username"},
                timeout=60,
            )
            if response.status_code >= 400:
                raise PublishError(f"HTTP {response.status_code}")
            print(f"OK   tiktok/{row['name']}")
        except Exception as exc:
            failures += 1
            print(f"FAIL tiktok/{row['name']} ({exc})")
    if failures:
        raise PublishError(f"{failures} account health check(s) failed")


def parser():
    root = argparse.ArgumentParser(
        description="Fast official Instagram/TikTok account onboarding"
    )
    root.add_argument("--accounts-file", help="override SOCIAL_ACCOUNTS_FILE")
    commands = root.add_subparsers(dest="command", required=True)

    commands.add_parser("list", help="list configured accounts").set_defaults(
        func=cmd_list
    )

    meta = commands.add_parser(
        "instagram-facebook",
        help="add every linked professional account from one Meta login",
    )
    meta.add_argument(
        "--api-version", default=os.getenv("INSTAGRAM_API_VERSION", "v24.0")
    )
    meta.add_argument("--prefix", default="")
    meta.set_defaults(func=cmd_instagram_facebook)

    direct = commands.add_parser(
        "instagram-direct", help="add one account using Instagram Login"
    )
    direct.add_argument("--name")
    direct.add_argument(
        "--api-version", default=os.getenv("INSTAGRAM_API_VERSION", "v24.0")
    )
    direct.set_defaults(func=cmd_instagram_direct)

    start = commands.add_parser(
        "tiktok-start", help="start official TikTok OAuth for one account"
    )
    start.add_argument("name")
    start.add_argument("--mode", choices=("direct", "inbox"), default="direct")
    start.add_argument(
        "--privacy",
        choices=(
            "PUBLIC_TO_EVERYONE",
            "MUTUAL_FOLLOW_FRIENDS",
            "FOLLOWER_OF_CREATOR",
            "SELF_ONLY",
        ),
        default="SELF_ONLY",
    )
    start.set_defaults(func=cmd_tiktok_start)

    finish = commands.add_parser(
        "tiktok-finish", help="finish TikTok OAuth from the redirected URL"
    )
    finish.add_argument("name")
    finish.add_argument("--redirect-url")
    finish.set_defaults(func=cmd_tiktok_finish)

    toggle = commands.add_parser("enable", help="enable a configured account")
    toggle.add_argument("platform", choices=("instagram", "tiktok"))
    toggle.add_argument("name")
    toggle.set_defaults(func=cmd_set_enabled, enabled=True)

    toggle = commands.add_parser("disable", help="disable a configured account")
    toggle.add_argument("platform", choices=("instagram", "tiktok"))
    toggle.add_argument("name")
    toggle.set_defaults(func=cmd_set_enabled, enabled=False)

    commands.add_parser(
        "health", help="validate every enabled account token"
    ).set_defaults(func=cmd_health)
    return root


def main():
    args = parser().parse_args()
    store = AccountStore(args.accounts_file)
    try:
        args.func(args, store)
    except (PublishError, KeyError, ValueError, requests.RequestException) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
