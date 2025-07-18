from proxmoxer import ProxmoxAPI
from proxmoxer.core import ResourceException
from subprocess import run, PIPE, CalledProcessError
import time

from lib.log import *


class ProxmoxNode(object):

    def __init__(self, host, user, password, ssh_port=22, node=None, **kwargs):
        self.host = host
        self.user = user
        self.password = password
        self.ssh_port = str(ssh_port)

        self.api = ProxmoxAPI(host=host, user=user, password=password, **kwargs)
        nodes = [d['node'] for d in self.api.nodes.get()]
        # select node - if not specified, pick the first one
        if node:
            if node not in nodes:
                error('Specified node {} not configured on host {}'.format(
                    host, node))
            self.node_name = node
        else:
            self.node_name = nodes[0]
        self.node = self.api.nodes(self.node_name)
        self.get_vm_ids()

    def get_vm_ids(self):
        self.vm_ids = []
        for vm in self.api.cluster.resources.get():
            if vm['type'] in ('lxc', 'qemu'):
                self.vm_ids.append(int(vm['vmid']))

    def get_available_id(self, base_id, descending=False):
        increment = 1
        if descending:
            increment = -1

        # find next free vm id starting at base_id
        self.get_vm_ids()
        new_id = base_id + increment
        for id in sorted(self.vm_ids, reverse=descending):
            if id == new_id:
                new_id += increment
        return new_id

    def find_vm_id(self, name, ignore_missing=False):
        for vm in self.node.qemu.get():
            if name == vm['name']:
                return vm['vmid']
        if not ignore_missing:
            error('ID could not be found for VM name:', name)

    def get(self, id):
        vm = self.node.qemu(id)
        return vm

    def exists(self, id):
        try:
            vm = self.get(id).status.current.get()
            return True
        except ResourceException:
            return False

    def get_name(self, id):
        vm = self.get(id).status.current.get()
        return vm['name']

    def clone(self, template_id, vm_id):
        self.get(template_id).clone.create(newid=vm_id, pool=self.pool)

    def create(self, id, name, vm_options):
        if 'cpu' in vm_options and not vm_options['cpu']:
            del(vm_options['cpu'])
        return self.node.qemu.create(vmid=id, name=name, **vm_options)

    def start(self, id):
        self.get(id).status.start.post()

    def is_running(self, id):
        return self.get(id).status.current.get().get('status') == 'running'

    def convert(self, id):
        name = self.get_name(id)
        vm = self.get(id)
        description = vm.config.get().get('description')
        vm.config.set(description=f'Branched off {name} -- {description}')
        vm.template().post()

    def destroy(self, id):
        i = 0
        while i < 30 and self.is_running(id):
            if not i:
                info('Stopping VM', id)
            self.get(id).status.stop.post()
            time.sleep(1)
            i += 1
        self.get(id).delete()

    def set_options(self, id, options):
        self.get(id).config.set(**options)

    def set_image_origin(self, id, image):
        description = f'Created based on {image}'
        self.get(id).config.set(description=description)

    def run_ssh(self, command, user='root', return_stdout=False):
        ssh_command = ['ssh', '-p', self.ssh_port, f'{user}@{self.host}', command]
        debug('Run:', ' '.join(ssh_command))
        try:
            result = run(ssh_command, stdout=PIPE, check=True, encoding='utf8')
        except CalledProcessError:
            error('Could not execute ssh command: "{}"'.format(
                  ' '.join(ssh_command)))
        if return_stdout:
            return result.stdout
        if result.stdout:
            debug('SSH output:', result.stdout)

    def find_storages(self, type='lvmthin'):
        storage_list = self.api.storage.get(type=type)
        storages = {}
        if storage_list:
            for element in storage_list:
                name = element.get('storage')
                available = self.node.storage(name).get('status').get('avail', 0)
                storages[name] = available
            return storages

    def get_disk_path(self, volume_id):
        storages = self.find_storages()
        storage = volume_id.split(':')[0]
        if storage in storages:
            volume = self.node.storage.get(f'{storage}/content/{volume_id}')
            return volume.get('path')
        else:
            return None
