#!/usr/bin/env python3
import hashlib
import http
import io
import os.path
import platform
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from distutils.command.build import build as orig_build
from distutils.core import Command
from typing import Tuple

from setuptools import setup
from setuptools.command.install import install as orig_install

SHELLCHECK_VERSION = '0.8.0'
POSTFIX_SHA256 = {
    ('linux', 'armv6hf'): (
        'linux.armv6hf.tar.xz',
        '17857c8a0a8f4001aa9638732991cbb6e85c4a410500b11e2e0a98d9858afca8',
    ),
    ('linux', 'aarch64'): (
        'linux.aarch64.tar.xz',
        '9f47bbff5624babfa712eb9d64ece14c6c46327122d0c54983f627ae3a30a4ac',
    ),
    ('linux', 'mips64'): (
        'http://ftp.cn.debian.org/debian/pool/main/s/shellcheck/shellcheck_0.8.0-2~bpo11%2B1_mips64el.deb',
        ''
    ),
    ('linux', 'x86_64'): (
        'linux.x86_64.tar.xz',
        'ab6ee1b178f014d1b86d1e24da20d1139656c8b0ed34d2867fbb834dad02bf0a',
    ),
    ('darwin', 'x86_64'): (
        'darwin.x86_64.tar.xz',
        'e065d4afb2620cc8c1d420a9b3e6243c84ff1a693c1ff0e38f279c8f31e86634',
    ),
    ('win32', 'AMD64'): (
        'zip',
        '2a616cbb5b15aec8238f22c0d62dede1b6d155798adc45ff4d0206395a8a5833',
    ),
}
POSTFIX_SHA256[('cygwin', 'x86_64')] = POSTFIX_SHA256[('win32', 'AMD64')]
POSTFIX_SHA256[('darwin', 'arm64')] = POSTFIX_SHA256[('darwin', 'x86_64')]
POSTFIX_SHA256[('linux', 'armv7l')] = POSTFIX_SHA256[('linux', 'armv6hf')]
PY_VERSION = '4'


def get_download_url() -> Tuple[str, str]:
    postfix, sha256 = POSTFIX_SHA256[(sys.platform, platform.machine())]
    url = (
        f'https://github.com/koalaman/shellcheck/releases/download/'
        f'v{SHELLCHECK_VERSION}/shellcheck-v{SHELLCHECK_VERSION}.{postfix}'
    ) if len(sha256) > 0 else postfix
    return url, sha256


def download(url: str, sha256: str) -> bytes:
    with urllib.request.urlopen(url) as resp:
        code = resp.getcode()
        if code != http.HTTPStatus.OK:
            raise ValueError(f'HTTP failure. Code: {code}')
        data = resp.read()

    checksum = hashlib.sha256(data).hexdigest()
    if len(sha256) > 0 and checksum != sha256:
        raise ValueError(f'sha256 mismatch, expected {sha256}, got {checksum}')

    return data


def extract(url: str, data: bytes) -> bytes:
    if url.endswith('.deb'):
        tmp_dir = f'/tmp/shellcheck-py-{int(time.time())}'
        file_name = os.path.basename(url)
        file_path = os.path.join(tmp_dir, file_name)
        bin = os.path.join(tmp_dir, 'usr/bin/shellcheck')
        os.mkdir(tmp_dir, 0o1777)
        with open(file_path, 'bw') as debf:
            debf.write(data)
        res = subprocess.run(['dpkg-deb', '-x', file_path, tmp_dir])
        if res.returncode == 0 and os.path.exists(bin):
            with open(bin, 'br') as binf:
                data = binf.read()
            subprocess.run(['rm', '-r', tmp_dir])
            return data
    with io.BytesIO(data) as bio:
        if '.tar.' in url:
            with tarfile.open(fileobj=bio) as tarf:
                for info in tarf.getmembers():
                    if info.isfile() and info.name.endswith('shellcheck'):
                        return tarf.extractfile(info).read()
        elif url.endswith('.zip'):
            with zipfile.ZipFile(bio) as zipf:
                for info in zipf.infolist():
                    if info.filename.endswith('.exe'):
                        return zipf.read(info.filename)

    raise AssertionError(f'unreachable {url}')


def save_executable(data: bytes, base_dir: str):
    exe = 'shellcheck' if sys.platform != 'win32' else 'shellcheck.exe'
    output_path = os.path.join(base_dir, exe)
    os.makedirs(base_dir)

    with open(output_path, 'wb') as fp:
        fp.write(data)

    # Mark as executable.
    # https://stackoverflow.com/a/14105527
    mode = os.stat(output_path).st_mode
    mode |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    os.chmod(output_path, mode)


class build(orig_build):
    sub_commands = orig_build.sub_commands + [('fetch_binaries', None)]


class install(orig_install):
    sub_commands = orig_install.sub_commands + [('install_shellcheck', None)]


class fetch_binaries(Command):
    build_temp = None

    def initialize_options(self):
        pass

    def finalize_options(self):
        self.set_undefined_options('build', ('build_temp', 'build_temp'))

    def run(self):
        # save binary to self.build_temp
        url, sha256 = get_download_url()
        archive = download(url, sha256)
        data = extract(url, archive)
        save_executable(data, self.build_temp)


class install_shellcheck(Command):
    description = 'install the shellcheck executable'
    outfiles = ()
    build_dir = install_dir = None

    def initialize_options(self):
        pass

    def finalize_options(self):
        # this initializes attributes based on other commands' attributes
        self.set_undefined_options('build', ('build_temp', 'build_dir'))
        self.set_undefined_options(
            'install', ('install_scripts', 'install_dir'),
        )

    def run(self):
        self.outfiles = self.copy_tree(self.build_dir, self.install_dir)

    def get_outputs(self):
        return self.outfiles


command_overrides = {
    'install': install,
    'install_shellcheck': install_shellcheck,
    'build': build,
    'fetch_binaries': fetch_binaries,
}


try:
    from wheel.bdist_wheel import bdist_wheel as orig_bdist_wheel
except ImportError:
    pass
else:
    class bdist_wheel(orig_bdist_wheel):
        def finalize_options(self):
            orig_bdist_wheel.finalize_options(self)
            # Mark us as not a pure python package
            self.root_is_pure = False

        def get_tag(self):
            _, _, plat = orig_bdist_wheel.get_tag(self)
            # We don't contain any python source, nor any python extensions
            return 'py2.py3', 'none', plat

    command_overrides['bdist_wheel'] = bdist_wheel

setup(version=f'{SHELLCHECK_VERSION}.{PY_VERSION}', cmdclass=command_overrides)
