#!/usr/bin/env python3

import argparse
import os
import pathlib
import requests
import time
import uuid
import yaml
from getpass import getpass
from proxmoxer.core import AuthenticationError
from urllib import parse as urlparse

from lib.config import load_config
from lib.defaults import load_defaults
from lib.log import *
from lib.proxmox import ProxmoxNode


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


def save_credentials(filename, server, password):
    data = load_credentials(filename)
    data.update({ server: password })
    try:
        with open(filename, 'w') as fd:
            info('Saving credentials to:', filename)
            yaml.dump(data, fd)
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


def get_username_password(args):
    username = args.username
    password = args.password
    if not password:
        credentials = load_credentials(CREDENTIALS_FILE)
        key = args.server
        if key in credentials:
            password = credentials.get(key)
        else:
            password = getpass('Password:')
            if not args.no_password_cache:
                save_credentials(CREDENTIALS_FILE, args.server, password)
    return username, password


def parse_arguments():
    """Get commandline arguments."""
    parser = argparse.ArgumentParser('create virtual machines and templates on Proxmox')
    parser.add_argument('name', help='Name of the virtual machine (template)')
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

    username, password = get_username_password(args)
    try:
        proxmox = ProxmoxNode(
            host=args.server,
            user=username,
            password=password,
            ssh_port=args.ssh_port,
        )
    except AuthenticationError:
        clean_credentials(CREDENTIALS_FILE, args.server)
        error('Proxmox login credentials are not correct')

    vm_options = load_defaults(preset=args.preset)
    if args.config:
        vm_options.update(load_config(args.config))
        show_config(vm_options, debug)
        if 'id' in vm_options:
            new_id = vm_options.pop('id', '')
        if 'image' in vm_options:
            image = vm_options.pop('image', '')
    else:
        warning('No config file specified. Using defaults only:')
        show_config(vm_options)

    vm_name = args.name
    label = 'VM'
    if args.template:
        vm_name = f'{args.name}-template'
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
    proxmox.create(new_id, vm_name, vm_options)

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
            temp_dir = f'/tmp/{uuid.uuid4()}'
            image_path = f'{temp_dir}/qcow2-image'
            info('Create temp directory on the server')
            proxmox.run_ssh(f'mkdir {temp_dir}; ls -l /tmp')

            url_parts = image.split('://')
            host_location = url_parts[1].split('@')[-1]
            display_image = f'{url_parts[0]}://{host_location}'
            info('Downloading image:', display_image)
            proxmox.run_ssh(f'curl -Lo {image_path} {image}')
            image = display_image
        else:
            # check if image exists on server
            stdout = proxmox.run_ssh(f'ls {image} 2>/dev/null', return_stdout=True).strip()
            if stdout != image:
                error('Image does not exist on the server:', image)
            image_path = image

        info('Converting qcow2 image to LVM thin volume')
        proxmox.run_ssh(f'qemu-img convert -f qcow2 -O raw {image_path} -S 4096 {disk_path}')
        proxmox.set_image_origin(new_id, image)

        if not args.no_cleanup and image.startswith('http'):
            info('Cleaning up')
            proxmox.run_ssh(f'rm -rf {temp_dir}')
    else:
        warn(f'No image provided. Creating an empty {label}')

    if args.template:
        info('Converting VM into template')
        proxmox.convert(new_id)
    elif args.autostart:
        info('Starting VM')
        proxmox.start(new_id)


if __name__ == '__main__':
    main()
