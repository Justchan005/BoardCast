"""Connect the Gmail account used for explicit real preview emails.

Enable the Gmail API in your Google Cloud project, configure the OAuth consent
screen (add the sender as a test user if the app is in Testing), then create an
OAuth client of type Desktop app. Download it as credentials.json in the project
root. Both credentials.json and the generated token.json are git-ignored.

Install backend/requirements.txt, then run:
    python -m scripts.get_gmail_token

The browser asks the sender to grant gmail.send only. No email is sent by this
script. Existing local outbox behavior is unchanged after connecting a sender.
"""
from __future__ import annotations

import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def _path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Connect a Gmail sender. No email will be sent.")
    parser.add_argument("--credentials", default="credentials.json", help="Desktop OAuth client JSON, relative to the project root")
    parser.add_argument("--output", default="token.json", help="Private token file, relative to the project root")
    args = parser.parse_args(argv)
    credentials, output = _path(args.credentials), _path(args.output)
    if not credentials.is_file():
        print("Desktop OAuth credentials were not found. Enable the Gmail API, create a Desktop app OAuth client,")
        print("and save its downloaded JSON as credentials.json in the project root.")
        return 1
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Install the updated backend requirements first: python -m pip install -r backend/requirements.txt")
        return 1
    print("A browser will open. Sign in to the Gmail account that should send previews.")
    print("This grants permission to send email; it does not grant permission to read your inbox.")
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials), SCOPES)
        creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
        if not creds.refresh_token:
            print("No refresh token was returned. Reconnect and grant consent to the selected sender.")
            return 1
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(creds.to_json(), encoding="utf-8")
    except Exception as exc:
        # Provider responses can contain credentials or authorization codes. Show only the type.
        print(f"Gmail connection did not complete ({type(exc).__name__}). Check the OAuth setup and try again.")
        return 1
    print("Gmail sender connected. Keep the token file private and do not commit it.")
    if output != PROJECT_ROOT / "token.json":
        print("Set GMAIL_TOKEN_FILE in .env to the output file path before starting BoardCast.")
    print("Restart BoardCast, build a newsletter preview, then use Send real email in Student Affairs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
