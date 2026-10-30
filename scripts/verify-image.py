#!/usr/bin/env python3
"""Authenticate the immutable Ubuntu manifest, then the image, before conversion."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

KEYRING = Path('/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg')


def verify_manifest(config, directory):
    with tempfile.TemporaryDirectory(prefix='agora-gpg-') as home:
        result = subprocess.run(['gpgv', '--homedir', home, '--status-fd', '1', '--keyring', str(KEYRING),
                                 str(directory / 'SHA256SUMS.gpg'), str(directory / 'SHA256SUMS')],
                                capture_output=True, text=True)
    if result.returncode:
        raise ValueError('Ubuntu manifest signature rejected: ' + result.stderr.strip())
    signers = [line.split()[2] for line in result.stdout.splitlines() if line.startswith('[GNUPG:] VALIDSIG ')]
    if signers != [config['signing_fingerprint']]:
        raise ValueError('Unexpected Ubuntu image signing fingerprint')
    matches = []
    for line in (directory / 'SHA256SUMS').read_text().splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].lstrip('*') == config['filename']:
            matches.append(fields[0])
    if matches != [config['sha256']]:
        raise ValueError('Signed manifest does not match the pinned image digest')


def verify_image(config, directory):
    verify_manifest(config, directory)
    with (directory / config['filename']).open('rb') as f:
        if hashlib.file_digest(f, 'sha256').hexdigest() != config['sha256']:
            raise ValueError('Ubuntu image checksum mismatch')


if __name__ == '__main__':
    try:
        verify_image(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2]))
        print('Ubuntu image: pinned release, GPG signer and SHA256 verified.')
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        sys.exit(f'error: {e}')
