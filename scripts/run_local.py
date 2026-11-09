"""Start the complete loopback-only demo without changing .env or existing records."""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Run BoardCast locally. Emails always stay in the local outbox.')
    parser.add_argument('--port', type=int, default=8081)
    parser.add_argument('--offline', action='store_true', help='Disable Gemini calls; use manual poster details.')
    parser.add_argument('--data-dir', type=Path, help='Optional separate demo data directory.')
    parser.add_argument('--sample-items', action='store_true', help='Add missing, clearly labelled sample submissions.')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    from dotenv import load_dotenv
    load_dotenv(root / '.env')
    os.environ.update(LOCAL_MODE='true', DEV_MODE='true',
                      PUBLIC_BASE_URL=f'http://127.0.0.1:{args.port}',
                      BOARDCAST_API_URL=f'http://127.0.0.1:{args.port}')
    if args.data_dir:
        os.environ['LOCAL_DATA_DIR'] = str(args.data_dir.resolve())
    else:
        os.environ.setdefault('LOCAL_DATA_DIR', str(root / 'local_data'))
    if args.offline:
        os.environ.update(GOOGLE_API_KEY='', GEMINI_API_KEY='', GOOGLE_CLOUD_PROJECT='',
                          GOOGLE_GENAI_USE_VERTEXAI='FALSE')
    # Keep a local signing key across restarts without writing to .env or printing it.
    if not os.environ.get('LINK_SECRET') or os.environ['LINK_SECRET'] == 'dev-only-change-me':
        import secrets
        keyfile = Path(os.environ['LOCAL_DATA_DIR']) / '.link-secret'
        keyfile.parent.mkdir(parents=True, exist_ok=True)
        if not keyfile.exists():
            with keyfile.open('x', encoding='utf-8') as stream:
                stream.write(secrets.token_urlsafe(48))
        os.environ['LINK_SECRET'] = keyfile.read_text(encoding='utf-8').strip()
    from backend import checks, config, store
    from scripts.seed_roles import ROLES, sample_items, attach_sample_poster, _next_monday
    for email, role in ROLES.items():
        if not store.get_role(email):
            store.db().collection('roles').document(email).set(role)
    demo_email = 'student.demo@mail.apu.edu.my'
    if not store.get_subscriber(demo_email):
        store.upsert_subscriber({'email': demo_email, 'active': True})
    if args.sample_items:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        for item in sample_items(_next_monday(datetime.now(ZoneInfo(config.TIMEZONE)).date())):
            if not store.get_item(item['id']):
                item['issues'] = checks.run_all(item['type'], item['data'])
                if item['type'] != 'announcement':
                    attach_sample_poster(item)
                store.create_item(item)
    from backend.main import app
    import uvicorn
    print(f'BoardCast demo: http://127.0.0.1:{args.port}/demo', flush=True)
    print('Local mode: emails are saved to the local outbox. No Gmail messages are sent.', flush=True)
    print('Poster reading: manual entry only.' if args.offline else
          'Poster reading: uses your configured Gemini credentials; manual entry is always available.', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
