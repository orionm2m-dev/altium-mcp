"""Render the isolated inventory probe; never dispatch Altium."""
import argparse
from pathlib import Path, PureWindowsPath
import re
p = argparse.ArgumentParser()
p.add_argument('pcb_path'); p.add_argument('request_id'); p.add_argument('output_directory')
a = p.parse_args()
if not PureWindowsPath(a.pcb_path).is_absolute() or PureWindowsPath(a.pcb_path).suffix.lower() != '.pcbdoc' or any(c in a.pcb_path for c in '\r\n\0'):
    raise SystemExit('Absolute single-line PCB path required')
if not re.fullmatch('[0-9a-f]{32}', a.request_id): raise SystemExit('Unique lowercase 32-hex request ID required')
folder = Path(a.output_directory).resolve()
folder.mkdir(parents=True, exist_ok=True)
pas = folder / ('OpenDocuments_' + a.request_id + '.pas')
project = pas.with_suffix('.PrjScr'); receipt = folder / (a.request_id + '.json')
if any(x.exists() for x in (pas,project,receipt)): raise SystemExit('Existing operation refused; choose a new request ID')
quote = lambda s: "'" + str(s).replace("'", "''") + "'"
source = Path(__file__).with_name('open_documents.pas.in').read_text(encoding='utf-8-sig')
values = {'__PCB_PATH__':quote(a.pcb_path),'__REQUEST_ID__':quote(a.request_id),'__RECEIPT_PATH__':quote(receipt)}
source = re.sub('__PCB_PATH__|__REQUEST_ID__|__RECEIPT_PATH__', lambda m: values[m.group()], source)
with pas.open('x',encoding='utf-8') as f: f.write(source)
with project.open('x',encoding='utf-8') as f: f.write('[Design]\nVersion=1.0\n\n[Document1]\nDocumentPath='+pas.name+'\n')
print(str(project)); print(str(receipt))
