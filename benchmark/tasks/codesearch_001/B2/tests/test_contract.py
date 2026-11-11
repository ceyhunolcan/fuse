import json, random, unittest
from evaluator.json_contract import JsonContract
class Acceptance(JsonContract):
    module='fusecodesearch'

    def search(self,source,pattern,language='python',**extra):return self.request({'files':[{'path':'a','language':language,'source':source}],'pattern':pattern,**extra})

    def test_preview_apply_preserve_other_text_and_idempotence(self):
        source='x = old( 1 ) # old(1)\ns="old(1)"\ny=old(1)\n';preview=self.search(source,'old(1)',replacement='new(2)')
        self.assertEqual(preview['files'][0]['source'],source);self.assertEqual(len(preview['changes']),1)
        expected='x = new(2) # old(1)\ns="old(1)"\ny=new(2)\n';self.assertEqual(preview['changes'][0]['after'],expected);self.assertIn('--- a',preview['changes'][0]['diff'])
        applied=self.search(source,'old(1)',replacement='new(2)',apply=True);self.assertEqual(applied['files'][0]['source'],expected)
        again=self.request({'files':applied['files'],'pattern':'old(1)','replacement':'new(2)','apply':True});self.assertEqual(again['matches'],[]);self.assertEqual(again['changes'],[])
    def test_javascript_multifile_and_invalid_overlap(self):
        req={'files':[{'path':'z','language':'javascript','source':'old(x);'}, {'path':'a','language':'python','source':'old(x)\n'}],'pattern':'old(x)','replacement':'new(x)','apply':True}
        result=self.request(req);self.assertEqual([c['path'] for c in result['changes']],['a','z']);self.assertEqual([f['path'] for f in result['files']],['z','a'])
        self.bad({'files':[{'path':'a','language':'javascript','source':'a+a+a;'}],'pattern':'a+a','replacement':'b','apply':True})
        self.bad({'files':req['files'],'pattern':'old(x)','replacement':'"unclosed'})
        self.bad({'files':req['files'],'pattern':'old(x)','apply':True})

    def test_boundary_matches_can_appear_on_a_later_invocation(self):
        first=self.search('a a b','a b',replacement='b',apply=True)
        self.assertEqual(first['files'][0]['source'],'a b')
        self.assertEqual(len(first['matches']),1)
        second=self.request({'files':first['files'],'pattern':'a b','replacement':'b','apply':True})
        self.assertEqual(second['files'][0]['source'],'b')
        self.assertEqual(len(second['matches']),1)

    def test_exact_diff_missing_newline_and_empty_replacement(self):
        import difflib
        for source, replacement, expected in (('old(x)', 'new(x)', 'new(x)'),
                ('old(x)\nkeep(y)\n', 'new(x)', 'new(x)\nkeep(y)\n'),
                ('old(x);', '', ';')):
            with self.subTest(source=source, replacement=replacement):
                language='javascript' if source.endswith(';') else 'python'
                for apply in (False,True):
                    result=self.search(source,'old(x)',language,replacement=replacement,apply=apply)
                    exact=''.join(difflib.unified_diff(source.splitlines(keepends=True),
                        expected.splitlines(keepends=True),fromfile='a',tofile='a',n=3,lineterm='\n'))
                    self.assertEqual(result['changes'],[{'path':'a','before':source,'after':expected,'diff':exact}])
                    self.assertEqual(result['files'][0]['source'],expected if apply else source)
                    self.assertEqual(result['matches'],[{'path':'a','start':0,'end':6,'text':'old(x)'}])

    def test_unchanged_file_omission_for_identical_and_absent_matches(self):
        files=[{'path':'z','language':'python','source':'keep(x)'},
               {'path':'a','language':'python','source':'old(x)'}]
        for apply in (False,True):
            result=self.request({'files':files,'pattern':'old(x)','replacement':'old(x)','apply':apply})
            self.assertEqual(result['changes'],[])
            self.assertEqual(result['files'],files)
            self.assertEqual(len(result['matches']),1)
        result=self.request({'files':files,'pattern':'old(x)','replacement':'new(x)'})
        self.assertEqual([c['path'] for c in result['changes']],['a'])
        self.assertEqual(result['files'],files)
