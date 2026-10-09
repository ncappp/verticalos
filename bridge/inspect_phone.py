import argparse, json
from adb_control import ADB, parse_nodes

p = argparse.ArgumentParser()
p.add_argument('--serial', required=True)
p.add_argument('--out', default='inspection')
a = p.parse_args()
adb = ADB(a.serial)
adb.ready()
out = adb.capture(a.out)
out.joinpath('nodes.json').write_text(
    json.dumps(parse_nodes(out.joinpath('ui.xml').read_text()), ensure_ascii=False, indent=2), encoding='utf8'
)
print(
    'Captured UI and screenshot. Inspect and redact account names, notifications and personal information before sharing.'
)
