"""Build Windows x64 portable ZIP with a verified CPython runtime and MT5 wheels.

Run on Linux with MinGW-w64, or on Windows with MinGW-w64 in PATH.
No executable content is downloaded without checking its pinned SHA256 hash.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verified_download(entry, cache):
    path = cache / entry['filename']
    if not path.exists():
        print('Downloading', entry['filename'], flush=True)
        temp = path.with_suffix(path.suffix + '.partial')
        with urllib.request.urlopen(entry['url'], timeout=120) as response, temp.open('wb') as output:
            shutil.copyfileobj(response, output)
        if hashlib.sha256(temp.read_bytes()).hexdigest() != entry['sha256']:
            temp.unlink()
            raise RuntimeError('SHA256 verification failed: ' + entry['filename'])
        temp.replace(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
        raise RuntimeError('Cached package SHA256 mismatch: ' + entry['filename'])
    return path


def extract_checked(archive, target, prefix=''):
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            if item.is_dir() or not item.filename.startswith(prefix):
                continue
            relative = Path(item.filename[len(prefix):])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('Unsafe archive member')
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.open(item) as input_file, destination.open('wb') as output:
                shutil.copyfileobj(input_file, output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', type=Path, default=ROOT / 'build' / 'downloads')
    parser.add_argument('--compiler', default=os.getenv('MINGW_CC', 'x86_64-w64-mingw32-gcc'))
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    target = ROOT / 'dist' / 'TESTMEV-Windows-x64'
    if target.exists():
        shutil.rmtree(target)  # This is only the generated package directory.
    runtime, app = target / 'runtime', target / 'app'
    runtime.mkdir(parents=True)
    app.mkdir()
    manifest = json.loads((ROOT / 'scripts' / 'windows-dependencies.json').read_text())
    for entry in manifest:
        archive = verified_download(entry, args.cache)
        extract_checked(archive, runtime if entry['kind'] == 'python' else runtime / 'Lib' / 'site-packages',
                        'tools/' if entry['kind'] == 'python' else '')
    # Explicit import paths protect the bundled runtime from user-installed Python.
    (runtime / 'python312._pth').write_text('.\nLib\nDLLs\nLib/site-packages\n../app\nimport site\n', encoding='utf-8')
    shutil.copy2(ROOT / 'main.py', app)
    shutil.copytree(ROOT / 'trader', app / 'trader', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(ROOT / 'README.md', target / 'README-TH.md')
    shutil.copy2(ROOT / 'scripts' / 'windows-dependencies.json', target / 'DEPENDENCIES.json')
    subprocess.run([args.compiler, '-Os', '-s', '-municode', '-mwindows', '-static',
                    str(ROOT / 'scripts' / 'windows_launcher.c'), '-o', str(target / 'TESTMEV.exe')], check=True)
    (target / 'START-HERE.txt').write_text(
        'TESTMEV - Windows 10/11 x64\n\n'
        '1. Extract ALL files from this ZIP into a folder.\n'
        '2. Double-click TESTMEV.exe. Python is included.\n'
        '3. Paper trading works immediately without an account or Internet.\n'
        '4. To READ Exness prices/account: open Exness MT5, log in there,\n'
        '   then use the MT5 connection button in TESTMEV.\n\n'
        'This version does NOT send orders to Exness / MT5.\n'
        'Keep app/ and runtime/ next to TESTMEV.exe.\n'
        'Use Exit in the sidebar to stop the background application.\n'
        'Closing only the window leaves the local process running.\n'
        'Settings, CSV session archives and launcher.log are in\n'
        '%LOCALAPPDATA%\\TESTMEV. Each launch starts a new paper session.\n\n'
        'Read README-TH.md for Thai instructions.\n', encoding='utf-8')
    output = shutil.make_archive(str(ROOT / 'dist' / 'TESTMEV-Windows-x64'), 'zip', target.parent, target.name)
    archive = Path(output)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(digest + '  ' + archive.name + '\n')
    print(json.dumps({'zip': str(archive), 'bytes': archive.stat().st_size, 'sha256': digest}, indent=2))


if __name__ == '__main__':
    main()
