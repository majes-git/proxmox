import yaml


def load_config(filename):
    config = {}
    try:
        with open(filename) as fd:
            config.update(yaml.safe_load(fd))
    except FileNotFoundError:
        pass
    return config
