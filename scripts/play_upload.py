#!/usr/bin/env python3
"""Upload LifePi's Android bundle to a Google Play track (Google Play Developer API v3).

    python scripts/play_upload.py --check                        # list the tracks, change nothing
    python scripts/play_upload.py app-release.aab                # to closed testing ("alpha")
    python scripts/play_upload.py app-release.aab --track internal

The service-account key: --key, else $LIFEPI_PLAY_KEY, else %USERPROFILE%/dev/keys/lifepi-play.json
(outside the repo). Only the account's email is ever printed, never the key. The account needs, in
Play Console > Users and permissions, for this app only: "Release to testing tracks" and "Manage
testing tracks and edit tester lists".

Release notes (en-US) are the newest dated section of STATUS.md, cut to Play's 500 characters.
A brand-new app that Google hasn't reviewed yet only takes draft releases: then the release goes
up as a draft and Play Console's "Roll out" button finishes it. Needs: pip install google-auth requests
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

PACKAGE = "io.github.therealshadoh.lifepi"
ROOT = Path(__file__).resolve().parent.parent
API = f"https://androidpublisher.googleapis.com/androidpublisher/v3/applications/{PACKAGE}"
UPLOAD = f"https://androidpublisher.googleapis.com/upload/androidpublisher/v3/applications/{PACKAGE}"
SCOPE = "https://www.googleapis.com/auth/androidpublisher"


def key_path(arg):
    if arg:
        return Path(arg)
    if os.environ.get("LIFEPI_PLAY_KEY"):
        return Path(os.environ["LIFEPI_PLAY_KEY"])
    return Path.home() / "dev" / "keys" / "lifepi-play.json"


def session(path):
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    if not path.exists():
        sys.exit(f"no service-account key at {path} (use --key or $LIFEPI_PLAY_KEY)")
    creds = service_account.Credentials.from_service_account_file(str(path), scopes=[SCOPE])
    print("service account:", creds.service_account_email)
    return AuthorizedSession(creds)


def ok(r, what):
    if r.status_code >= 300:
        try:
            err = r.json().get("error", {})
            msg = f"{err.get('status', '')} {err.get('message', '')}".strip()
        except ValueError:
            msg = r.text[:800]
        raise RuntimeError(f"{what}: HTTP {r.status_code} - {msg}")
    return r.json() if r.content else {}


def release_notes():
    """The newest '## <date> ...' section of STATUS.md."""
    p = ROOT / "STATUS.md"
    if not p.exists():
        return "Bug fixes and improvements."
    text = p.read_text(encoding="utf-8")
    parts = re.split(r"(?m)^## ", text)
    sections = [s for s in parts[1:] if re.match(r"\d{4}-\d{2}-\d{2}", s)]
    if not sections:
        return "Bug fixes and improvements."
    body = sections[0].split("\n", 1)[1] if "\n" in sections[0] else ""
    lines = [re.sub(r"^\s*[-*]\s*", "- ", l).rstrip() for l in body.splitlines()
             if l.strip() and not l.lstrip().startswith("#")]
    notes = "\n".join(lines).strip()
    return (notes[:497] + "...") if len(notes) > 500 else notes or "Bug fixes and improvements."


def show_tracks(s, edit):
    tracks = ok(s.get(f"{API}/edits/{edit}/tracks"), "list tracks").get("tracks", [])
    if not tracks:
        print("  (no tracks yet)")
    top = 0
    for t in tracks:
        for rel in t.get("releases", []) or [{}]:
            codes = [int(c) for c in rel.get("versionCodes", [])]
            top = max([top, *codes])
            print(f"  {t['track']:<12} {rel.get('status', '-'):<11} codes {codes or '-'}  {rel.get('name', '')}")
    return top


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("aab", nargs="?", help="the signed .aab to upload")
    ap.add_argument("--track", default="alpha", help='"alpha" = closed testing (default), "internal", "production"')
    ap.add_argument("--key", help="service-account JSON key")
    ap.add_argument("--check", action="store_true", help="list tracks and versions; change nothing")
    args = ap.parse_args()
    s = session(key_path(args.key))
    edit = ok(s.post(f"{API}/edits"), "open an edit")["id"]
    try:
        if args.check or not args.aab:
            print(f"{PACKAGE} tracks:")
            top = show_tracks(s, edit)
            print("highest version code in Play:", top or "none")
            return
        aab = Path(args.aab)
        print(f"uploading {aab.name} ({aab.stat().st_size / 1e6:.1f} MB)...")
        with open(aab, "rb") as f:
            bundle = ok(s.post(f"{UPLOAD}/edits/{edit}/bundles", params={"uploadType": "media"}, data=f,
                               headers={"Content-Type": "application/octet-stream"}, timeout=600), "upload the bundle")
        code = str(bundle["versionCode"])
        print("uploaded version code", code)
        notes = release_notes()
        for status in ("completed", "draft"):
            body = {"track": args.track, "releases": [{"versionCodes": [code], "status": status,
                    "releaseNotes": [{"language": "en-US", "text": notes}]}]}
            ok(s.put(f"{API}/edits/{edit}/tracks/{args.track}", json=body), f"set the {args.track} track")
            try:
                ok(s.post(f"{API}/edits/{edit}:commit"), "commit")
                edit = None
                print(f"version {code} is on the {args.track} track as {status}")
                if status == "draft":
                    print("Play only takes drafts for this app so far: press 'Roll out' in Play Console "
                          f"(Test and release > {args.track} track).")
                return
            except RuntimeError as e:
                if status == "completed" and "draft" in str(e).lower():
                    print("Play wants a draft release (the app hasn't been reviewed yet) - trying that")
                    continue
                raise
    finally:
        if edit:                                  # nothing committed: throw the edit away
            s.delete(f"{API}/edits/{edit}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit(f"Play API: {e}")
