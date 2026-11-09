"""Create token.json for the project Gmail sender (run once on your laptop).

1. Google Cloud console > APIs & Services > Credentials > Create OAuth client ID (Desktop app)
2. Download it as credentials.json into the repo root (it is git-ignored)
3. pip install google-auth-oauthlib && python scripts/get_gmail_token.py
4. gcloud secrets versions add gmail-token --data-file=token.json
"""
from google_auth_oauthlib.flow import InstalledAppFlow

flow = InstalledAppFlow.from_client_secrets_file("credentials.json", ["https://www.googleapis.com/auth/gmail.send"])
creds = flow.run_local_server(port=0)
with open("token.json", "w") as f:
    f.write(creds.to_json())
print("token.json written. Do not commit it.")
