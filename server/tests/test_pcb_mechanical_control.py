import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pcb_mechanical_control import build_mechanical_control_spec as build
P=r'C:\Boards\Test.PcbDoc'
T={'kind':'free_npth','name':'MH1','expected_key':'free_npth:74:true:100:200:0:0:100000:0'}
class Contract(unittest.TestCase):
 def test_inspect_cannot_contain_targets(self):
  with self.assertRaises(ValueError):build(P,'inspect',[T])
 def test_delete_requires_explicit_targets(self):
  with self.assertRaises(ValueError):build(P,'delete',[])
 def test_foreign_or_generic_targets_refused(self):
  for kind in ['pad','component','track','polygon','board_cutout']:
   with self.subTest(kind=kind),self.assertRaises(ValueError):build(P,'delete',[dict(T,kind=kind)])
 def test_injection_refused(self):
  for field in ['name','expected_key']:
   with self.subTest(field=field),self.assertRaises(ValueError):build(P,'delete',[dict(T,**{field:'a\n|PCB|foreign'})])
 def test_duplicate_names_refused(self):
  with self.assertRaises(ValueError):build(P,'delete',[T,dict(T,name='mh1')])
 def test_missing_keys_and_unknown_fields_refused(self):
  for item in [{'kind':'free_npth','name':'MH1'},dict(T,force=True)]:
   with self.subTest(item=item),self.assertRaises(ValueError):build(P,'delete',[item])
 def test_bounded_batch(self):
  with self.assertRaises(ValueError):build(P,'delete',[dict(T,name=str(i))for i in range(65)])
 def test_absolute_pcb_path(self):
  for path in ['x.PcbDoc',r'C:\Boards\x.SchDoc']:
   with self.subTest(path=path),self.assertRaises(ValueError):build(path,'inspect')
 def test_opaque_native_key_preserved(self):
  key='keepout_region:32:true:32;true;0,1000,2000;0,3000,4000;0,5000,6000'
  self.assertIn(key,build(P,'delete',[dict(T,kind='keepout_region',expected_key=key)]))
if __name__=='__main__':unittest.main()
