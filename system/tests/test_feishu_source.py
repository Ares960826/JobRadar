import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skill/jobradar/scripts'))
import feishu_source

class FeishuTest(unittest.TestCase):
    def test_read_scope_projection_and_cache(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);cfg=dict(name='fixture',base_token='base',table_id='tbltest',view_id='vewtest',
              fields=['fldCompany','fldTitle'],search_fields=['fldTitle'],limit=20,cache_minutes=15)
            captured=[]
            def execute(command,**kw):
                captured.append(command)
                p=Path(command[command.index('--output')+1]);p.write_text('{"record_id":"one"}\n')
                p.with_suffix('.manifest.json').write_text(json.dumps({'has_more':True,'next_offset':1,'rev':42,'query_context':{'record_scope':'view_filtered_records'}}))
                return type('Result',(),{'returncode':0,'stderr':''})()
            with patch.object(feishu_source.subprocess,'run',side_effect=execute):
                a=feishu_source.collect(out,cfg,'人工智能');b=feishu_source.collect(out,cfg,'人工智能')
            self.assertFalse(a['query_complete']);self.assertTrue(b['cache_hit']);self.assertEqual(len(captured),1)
            cmd=captured[0];self.assertIn('+record-search',cmd);self.assertEqual(cmd[cmd.index('--view-id')+1],'vewtest')
            self.assertEqual(cmd.count('--field-id'),2);self.assertNotIn('--filter-json',cmd)
            self.assertEqual(cmd[cmd.index('--as')+1],'user')

    def test_unconfigured_source_never_reaches_lark(self):
        template=json.loads((Path(__file__).resolve().parents[2]/'templates/instance/system/config/feishu-aggregate.json').read_text())
        with tempfile.TemporaryDirectory() as d, patch.object(feishu_source.subprocess,'run') as run:
            for cfg,message in ((None,'No Feishu collection'),(template,'placeholders')):
                with self.assertRaisesRegex(ValueError,message):feishu_source.collect(Path(d),cfg,'人工智能')
        run.assert_not_called()

if __name__=='__main__':unittest.main()
