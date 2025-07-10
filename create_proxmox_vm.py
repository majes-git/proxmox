#!/usr/bin/env python3

import argparse
import os
import pathlib
import requests
import time
import urllib3
import uuid
import yaml
from getpass import getpass
from proxmoxer.core import AuthenticationError
from proxmoxer.tools import Tasks
from urllib import parse as urlparse

from lib.config import load_config
from lib.defaults import load_defaults
from lib.log import *
from lib.proxmox import ProxmoxNode
from tkinter import Image


CREDENTIALS_FILE = os.path.join(pathlib.Path.home(), '.proxmox_credentials.yaml')


def load_credentials(filename):
    try:
        with open(filename) as fd:
            data = yaml.safe_load(fd)
            if data:
                debug('Loading credentials from:', filename)
                return data
    except FileNotFoundError:
        pass
    except:
        raise
        warning('Could not access file:', filename)
    return {}


def save_credentials(filename, server, value, suppress_message=False):
    data = load_credentials(filename)
    if server in data:
        _value = data.get(server)
        if type(_value) == str:
            data.update({ server: value })
        if type(_value) == dict:
            _value.update(value)
            data.update({ server: _value })
    else:
        data.update({ server: value })
    try:
        with open(filename, 'w') as fd:
            if not suppress_message:
                info('Saving credentials to:', filename)
            yaml.dump(data, fd)
        os.chmod(filename, 0o600)
    except:
        pass
        warning('Could not access file:', filename)


def clean_credentials(filename, server):
    data = load_credentials(filename)
    del(data[server])
    try:
        with open(filename, 'w') as fd:
            debug('Cleaning credentials from:', filename)
            yaml.dump(data, fd)
    except:
        pass


def get_username(prefix):
    return input(f'Please enter {prefix}: ')

def get_password(prefix):
    return getpass(f'Please enter {prefix}: ')

def pretty_prefix(prefix):
    return prefix.strip('_').replace('_', ' ').title().replace('Ssr', 'SSR')

def get_username_password(type, server, username=None, password=None, cache_passwords=True):
    if username.startswith('_') and username.endswith('_'):
        prefix_username = pretty_prefix(username)
        prefix_password = pretty_prefix(password)
        username = None
        password = None

    if not (username and password):
        credentials = load_credentials(CREDENTIALS_FILE)

    if not username and type == 'image':
        if server in credentials:
            username = credentials.get(server).get('username')
        else:
            username = get_username(prefix_username)
            if cache_passwords:
                save_credentials(CREDENTIALS_FILE, server, {'username': username}, suppress_message=True)

    if not password:
        if type == 'proxmox':
            if server in credentials:
                password = credentials.get(server)
            else:
                password = get_password(f'Proxmox password for {server}')
                if cache_passwords:
                    save_credentials(CREDENTIALS_FILE, server, password)
        if type == 'image':
            if server in credentials:
                password = credentials.get(server).get('password')
            else:
                password = get_password(prefix_password)
                if cache_passwords:
                    save_credentials(CREDENTIALS_FILE, server, {'password': password})

    return username, password


def parse_arguments():
    """Get commandline arguments."""
    parser = argparse.ArgumentParser('create virtual machines and templates on Proxmox')
    parser.add_argument('name', nargs='?', help='Name of the virtual machine (template)')
    parser.add_argument('--server', '-s', required=True,
                        help='Proxmox server name/address')
    parser.add_argument('--username', '-u', default='root@pam',
                        help='username for connecting to proxmox (default: root@pam)')
    parser.add_argument('--password', '-p',
                        help='password for connecting to proxmox')
    parser.add_argument('--ssh-port', default=22,
                        help='SSH port to be used to connect to the server')
    parser.add_argument('--config', '-c',
                        help='config file for VM settings')
    parser.add_argument('--image', '-i',
                        help='location (url or file) to the VM disk image')
    parser.add_argument('--template', '-t', action='store_true',
                        help='convert VM into template')
    parser.add_argument('--autostart', action='store_true',
                        help='automatically start VMs after deployment')
    parser.add_argument('--debug', action='store_true',
                        help='show debug messages')
    parser.add_argument('--preset', help='preset for VM options (e.g. "debian")')
    parser.add_argument('--base-id', help='base ID for virtual machine (template)')
    parser.add_argument('--replace', action='store_true',
                        help='replaced the VM if exists')
    parser.add_argument('--id', help='VM ID to be used')
    parser.add_argument('--no-cleanup', action='store_true',
                        help='do not remove downloaded image')
    parser.add_argument('--no-password-cache', action='store_true',
                        help='do not cache proxmox passwords')
    parser.add_argument('--insecure', action='store_true',
                        help='skip TLS certificate validation')
    parser.add_argument('--assumeyes', '-y', action='store_true',
                        help='answer "yes" for all questions')
    return parser.parse_args()


def step(*message, **kwargs):
    print(' •', *message, **kwargs)


def show_config(config, func=print):
    delimiter = '-'*55
    func(delimiter + '\n' + yaml.dump(config).strip())
    func(delimiter)


def encode_ssh_keys(keys):
    if keys.startswith('/'):
        key_file = keys
        try:
            with open(key_file) as fd:
                keys = fd.read()
                if not keys:
                    error('There is no key in file:', key_file)
        except FileNotFoundError:
            error('Could not find sshkeys file:', key_file)
        except PermissionError:
            error('Could not open sshkeys file:', key_file)
        except:
            error('Could not read sshkeys file:', key_file)

    if keys.startswith('http'):
        url = keys
        try:
            keys = requests.get(url).text
        except:
            error('Could not load sshkeys from url:', url)
    return urlparse.quote(keys, safe='')


def main():
    new_id = 0
    image = None
    args = parse_arguments()
    if args.debug:
        set_debug()

    verify_ssl=True
    if args.insecure:
        urllib3.disable_warnings()
        verify_ssl=False

    username, password = get_username_password(
        'proxmox', args.server, args.username, args.password, not args.no_password_cache)
    try:
        proxmox = ProxmoxNode(
            host=args.server,
            user=username,
            password=password,
            ssh_port=args.ssh_port,
            verify_ssl=verify_ssl,
        )
    except AuthenticationError:
        clean_credentials(CREDENTIALS_FILE, args.server)
        error('Proxmox login credentials are not correct')

    vm_options = load_defaults(preset=args.preset)
    if args.config:
        config = load_config(args.config)
        if not config:
            error('Could not load config:', args.config)
        vm_options.update(config)
        show_config(vm_options, debug)
        if 'id' in vm_options:
            new_id = vm_options.pop('id', '')
        if 'image' in vm_options:
            image = vm_options.pop('image', '')
    else:
        warning('No config file specified. Using defaults only:')
        show_config(vm_options)

    if 'scsi0' not in vm_options:
        error('Your config has no disk specified. Stopping here.')

    storages = proxmox.find_storages().copy()
    for disk in [k for k in vm_options.keys() if k.startswith('scsi')]:
        try:
            num = int(disk.strip('scsi'))
        except ValueError:
        # ignore this option, which is no disk specification
            continue

        # replace _lvmthin_ by first suitable storage
        name, size = vm_options[disk].split(':')
        if name == '_lvmthin_':
            selected = None
            for storage, available in storages.items():
                bytes = int(size) * 2**30
                if bytes <= available:
                    if selected:
                        warning(f'Found more than 1 suitable storages for disk "{disk}". Using storage {selected}.')
                        break
                    selected = storage
                    # update available
                    storages[selected] -= bytes
            if not selected:
                error(f'Could not find suitable storage for disk "{disk}". Stopping here.')
            vm_options[disk] = vm_options[disk].replace('_lvmthin_', selected)

    vm_name = args.name
    if not vm_name:
        vm_name = os.path.splitext(os.path.basename(args.config))[0].lower()
    label = 'VM'
    if args.template:
        vm_name = f'{vm_name}-template'
        label = 'template'

    replace = False
    existing_id = proxmox.find_vm_id(vm_name, ignore_missing=True)
    if args.id:
        new_id = args.id
    if args.replace:
        if new_id and proxmox.exists(new_id):
            replace = True
        elif existing_id:
            new_id = existing_id
            replace = True
        if replace:
            existing_vm_name = proxmox.get_name(new_id)
            info(f'Replacing {label}: {existing_vm_name} (id: {new_id})')
    else:
        if new_id and proxmox.exists(new_id):
            caps_label = label if label.isupper() else label.capitalize()
            error(f'{caps_label} with ID {new_id} already exists. Please specify --replace to replace it.')
        if existing_id:
            warning(f'Another {label} with the same name (id: {existing_id}) already exists!')
        if not new_id:
            # Search for a new id
            base_id = args.base_id
            descending = False
            if base_id:
                base_id = int(args.base_id)
            else:
                base_id = 100
                if args.template:
                    base_id = 2000
            if args.template:
                descending = True
            new_id = proxmox.get_available_id(base_id, descending=descending)

    info(f'About to create a new {label}:')
    step(f'ID: {new_id}')
    step(f'Name: {vm_name}')
    if not args.assumeyes:
        yn = input('Continue [yN]? ')
        if yn != 'y' and yn != 'Y':
            return

    if replace:
        proxmox.destroy(new_id)

    # handle ssh_keys
    if 'sshkeys' in vm_options:
        ssh_keys = encode_ssh_keys(vm_options['sshkeys'])
        vm_options['sshkeys'] = ssh_keys

    info('Creating VM')
    task_id = proxmox.create(new_id, vm_name, vm_options)
    r = Tasks.blocking_status(proxmox.api, task_id)
    if r.get('status') == 'stopped' and r.get('exitstatus') != 'OK':
        error(r.get('exitstatus'))

    # Find disk_path
    disk_path = None
    more_attempts = 10
    while not disk_path:
        # trying to read vm config
        # (may take more than one call - depending on the performance of the host)
        disk0 = proxmox.get(new_id).config.get().get('scsi0')
        if disk0:
            volume_id = disk0.split(',')[0]
            disk_path = proxmox.get_disk_path(volume_id)
        more_attempts -= 1
        if not more_attempts:
            error('Could not find disk definition.')
        time.sleep(1)

    if args.image:
        image = args.image

    if image:
        if image.startswith('http'):
            image_url = image
            temp_dir = f'/tmp/{uuid.uuid4()}'
            image_path = f'{temp_dir}/qcow2-image'
            info('Creating temp directory on the server')
            proxmox.run_ssh(f'mkdir {temp_dir}; ls -l /tmp')

            url_parts = image_url.split('://')
            host_location = url_parts[1].split('@')[-1]
            host = host_location.split('/')[0]
            if '@' in url_parts[1]:
                user_password = url_parts[1].split('@')[0]
                if ':' not in user_password:
                    error('Password part is missing in image URL')
                username, password = get_username_password(
                    'image', host, *user_password.split(':'), not args.no_password_cache)
                image_url = f'{url_parts[0]}://{username}:{password}@{host_location}'
                # test if image can be loaded
                r = requests.head(image_url)
                if r.status_code != 200:
                    # wrong url or credentials
                    clean_credentials(CREDENTIALS_FILE, host)
                    error('Image URL cannot be loaded. Please check URL/credentials!')
            display_image = f'{url_parts[0]}://{host_location}'
            info('Downloading image:', display_image)
            proxmox.run_ssh(f'curl -Lo {image_path} {image_url}')
            image = display_image
        else:
            # check if image exists on server
            stdout = proxmox.run_ssh(f'ls {image} 2>/dev/null', return_stdout=True).strip()
            if stdout != image:
                error('Image does not exist on the server:', image)
            image_path = image

        info('Converting qcow2 image to LVM thin volume')
        proxmox.run_ssh(f'qemu-img convert -O raw {image_path} -S 4096 {disk_path}')
        proxmox.set_image_origin(new_id, image)

        if not args.no_cleanup and image.startswith('http'):
            info('Cleaning up')
            proxmox.run_ssh(f'rm -rf {temp_dir}')
    else:
        warning(f'No image provided. Creating an empty {label}')

    if args.template:
        info('Converting VM into template')
        proxmox.convert(new_id)
    elif args.autostart:
        info('Starting VM')
        proxmox.start(new_id)


if __name__ == '__main__':
    main()
