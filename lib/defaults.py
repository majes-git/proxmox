from os.path import isfile
import yaml


DEFAULTS = {
    'cores': 1,
    'memory': 1024,
    'ostype': 'l26',
    'scsihw': 'virtio-scsi-pci',
    'serial0': 'socket',
    'vga': 'serial0',
}

PRESETS = {
    'debian': {
        'cores': 1,
        'cpu': '',
        'memory': 512,
    },
    'ssr': {
        'cores': 4,
        'cpu': 'host',
        'memory': 4096,
    },
}


def load_defaults(filename='default_vm_options.yaml', preset=''):
    defaults = DEFAULTS.copy()
    if isfile(filename):
        with open(filename) as fd:
            defaults.update(yaml.safe_load(fd))

    if preset:
        if preset not in PRESETS:
            warn('Unknown preset:', preset)
        else:
            defaults.update(PRESETS.get(preset))
    return defaults
