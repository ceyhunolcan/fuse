import json, random, unittest
from evaluator.json_contract import JsonContract
class Acceptance(JsonContract):
    module='fusetextdrop'

    def step(self,state,op,**fields):return self.request({'state':state,'command':{'op':op,**fields}})
    def note(self,body='hello',format='plain'):return self.step(None,'create',title='Note',body=body,format=format)

    def test_persistence_unicode_update_delete_ids(self):
        one=self.note('Merhaba 測');two=self.step(json.loads(json.dumps(one['state'])),'create',title='Two',body='B')
        edit=self.step(two['state'],'update',id=1,title='Edited',body='C',format='markdown')
        self.assertEqual(edit['result']['body'],'C');deleted=self.step(edit['state'],'delete',id=1)
        three=self.step(deleted['state'],'create',title='Three',body='D');self.assertEqual(three['result']['id'],3)
        self.assertEqual([n['id'] for n in self.step(three['state'],'list')['result']],[2,3])
    def test_export_import_roundtrip(self):
        state=self.note('é 測')['state'];serialized=self.step(state,'export')['result']['serialized']
        loaded=self.step(None,'import',serialized=serialized);self.assertEqual(loaded['state'],state)
        self.assertEqual(self.step(loaded['state'],'get',id=1)['result']['body'],'é 測')
    def test_plain_escaping(self):
        n=self.note('<script>" &');self.assertEqual(self.step(n['state'],'render',id=1)['result']['html'],'<pre>&lt;script&gt;&quot; &amp;</pre>')
    def test_markdown_blocks_and_inline(self):
        n=self.note('# Title\n\n**bold** and *soft*\n\n```\n<x>&\n```','markdown')
        self.assertEqual(self.step(n['state'],'render',id=1)['result']['html'],'<h1>Title</h1>\n<p><strong>bold</strong> and <em>soft</em></p>\n<pre><code>&lt;x&gt;&amp;</code></pre>')
    def test_safe_and_unsafe_links(self):
        n=self.note('[go](https://example.org?q=1&x=2) [bad](javascript:alert) <img>','markdown')
        result=self.step(n['state'],'render',id=1)['result']['html']
        self.assertIn('<a href="https://example.org?q=1&amp;x=2">go</a>',result)
        self.assertNotIn('href="javascript',result);self.assertIn('&lt;img&gt;',result)
    def test_validation_and_unclosed_fence(self):
        for command in [{'op':'create','title':'','body':'x'}, {'op':'create','title':'x','body':4},{'op':'create','title':'x','body':'a'*10001},{'op':'get','id':True},{'op':'create','title':'x','body':'x','extra':1},{'op':'import','serialized':'{}'}]:self.bad({'command':command})
        state=self.note()['state'];self.bad({'state':state,'command':{'op':'update','id':1}})
        fenced=self.note('```\nnever closed','markdown');self.bad({'state':fenced['state'],'command':{'op':'render','id':1}})

    def test_malformed_empty_state_is_not_initialization(self):
        self.bad({'state':{},'command':{'op':'list'}})

    def test_inline_markup_cannot_rewrite_link_attributes_or_nest(self):
        note=self.note('[**go**](https://example.org/a*b*) ***x***','markdown')
        rendered=self.step(note['state'],'render',id=1)['result']['html']
        self.assertEqual(rendered,'<p><a href="https://example.org/a*b*">**go**</a> ***x***</p>')

    def test_state_id_spelling_and_boolean_ids_are_rejected(self):
        state=self.note()['state'];state['notes']['01']=state['notes'].pop('1')
        self.bad({'state':state,'command':{'op':'list'}})
        state=self.note()['state'];state['notes']['1']['id']=True
        self.bad({'state':state,'command':{'op':'list'}})

    def test_unicode_codepoint_lengths_and_exact_note_shape(self):
        title='測'*120;body='é'*10000
        result=self.step(None,'create',title=title,body=body)
        self.assertEqual(result['result'],{'id':1,'title':title,'body':body,'format':'plain'})
        self.assertEqual(result['state'],{'version':1,'next_id':2,'notes':{'1':result['result']}})
        for command in ({'op':'create','title':'測'*121,'body':''},
                        {'op':'create','title':'ok','body':'é'*10001}):
            self.bad({'command':command})
        state=json.loads(json.dumps(result['state']));state['notes']['1']['extra']=1
        self.bad({'state':state,'command':{'op':'list'}})
