"""JSON storage for local sandboxes."""
import json
from os import path as os_path
from dataclasses import asdict
from pathlib import Path
from tempfile import NamedTemporaryFile

from .sandbox import DirectorCue, SandboxConfig, SandboxDrone, SandboxEmergency, SandboxSite, SandboxStaff, SandboxStrategy


def decode_config(data):
    config = SandboxConfig(**dict(
        data, center=tuple(data['center']),
        sites=tuple(SandboxSite(**site) for site in data['sites']),
        drones=tuple(SandboxDrone(**dict(row, waypoints=tuple(map(tuple, row['waypoints'])),
                                        emergencies=tuple(SandboxEmergency(**e) for e in row['emergencies'])))
                     for row in data['drones']),
        staff=tuple(SandboxStaff(**dict(row, custom_routes=tuple(tuple(map(tuple, route))
                                                               for route in row.get('custom_routes', ()))))
                    for row in data['staff']),
        strategy=SandboxStrategy(**data['strategy']),
        cues=tuple(DirectorCue(cue['action'], cue['at'], tuple(cue['targets'])) for cue in data.get('cues', ())),
    ))
    config.validate(draft=True)
    return config


class SandboxLibrary:
    def __init__(self, app_data):
        self.root = Path(app_data) / 'sandboxes'

    def _path(self, name):
        if (not name or name != name.strip() or name.endswith('.') or
                any(char in '<>:"/\\|?*' or ord(char) < 32 for char in name) or
                getattr(os_path, 'isreserved', lambda _: False)(name + '.json')):
            raise ValueError('请使用普通文字作为预案名称')
        return self.root / (name + '.json')

    def list(self):
        return sorted(path.stem for path in self.root.glob('*.json') if path.is_file())

    def save(self, name, config):
        config.validate(draft=True)
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False, suffix='.tmp') as handle:
            temp = Path(handle.name)
        try:
            temp.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    def load(self, name):
        return decode_config(json.loads(self._path(name).read_text(encoding='utf-8')))

    def delete(self, name):
        self._path(name).unlink()
