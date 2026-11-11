import json, random, unittest
from evaluator.json_contract import JsonContract
class Acceptance(JsonContract):
    module='fusecodesearch'

    def search(self,source,pattern,language='python',**extra):return self.request({'files':[{'path':'a','language':language,'source':source}],'pattern':pattern,**extra})

    def test_python_sequences_comments_strings_and_spans(self):
        source='x = foo( 1 ) # foo(1)\ns = "foo(1)"\ny = foo(1)\n';result=self.search(source,'foo(1)')
        self.assertEqual([m['text'] for m in result['matches']],['foo( 1 )','foo(1)'])
        for m in result['matches']:self.assertEqual(source[m['start']:m['end']],m['text'])
        self.assertEqual(len(self.search(source,'foo')['matches']),2)
    def test_javascript_comments_escaped_strings_and_unicode_offsets(self):
        source='const ölçü = 1; // log(x)\nlog( ölçü ); const s="log(ölçü)"; /* log(ölçü) */\n'
        found=self.search(source,'log(ölçü)','javascript')['matches'];self.assertEqual(len(found),1);self.assertEqual(found[0]['text'],'log( ölçü )');self.assertEqual(found[0]['start'],source.index('log( ölçü'))
    def test_ordering_overlaps_and_exact_literal_spelling(self):
        files=[{'path':'z','language':'javascript','source':'a+a+a;'}, {'path':'b','language':'python','source':'a+a+a\n'}]
        matches=self.request({'files':files,'pattern':'a+a'})['matches'];self.assertEqual([m['path'] for m in matches],['b','b','z','z'])
        self.assertEqual([m['start'] for m in matches],[0,2,0,2])
        self.assertEqual(len(self.search('x="a"\ny=\'a\'\n','"a"')['matches']),1)
    def test_multiline_and_seeded_identifier_oracle(self):
        rng=random.Random(184);names=[rng.choice(['alpha','beta','gamma']) for _ in range(20)];source='\n'.join(n+' = 1' for n in names)+'\n'
        self.assertEqual(len(self.search(source,'beta')['matches']),names.count('beta'))
        source='call(\n    x, # keep\n    y\n)\n';self.assertEqual(self.search(source,'call(x,y)')['matches'][0]['text'],source.rstrip())
    def test_invalid_requests_and_empty_pattern(self):
        for req in [{'files':[],'pattern':''},{'files':[{'path':'x','language':'rust','source':'x'}],'pattern':'x'},{'files':[{'path':'x','language':'python','source':'x'},{'path':'x','language':'python','source':'x'}],'pattern':'x'},{'files':[{'path':'x','language':'javascript','source':'"unclosed'}],'pattern':'x'}]:self.bad(req)
        self.assertEqual(self.request({'files':[],'pattern':'x'})['matches'],[])

    def test_unclosed_javascript_comment_is_invalid(self):
        self.bad({'files':[{'path':'a','language':'javascript','source':'/* unclosed'}],'pattern':'x'})

    def test_reserved_looking_tokens_retain_literal_meaning(self):
        js=self.search('$x+$x;', '$x', 'javascript')['matches']
        self.assertEqual([m['text'] for m in js],['$x','$x'])
        self.assertTrue(all('captures' not in m for m in js))
        py=self.search('x = ...\n', '...')['matches']
        self.assertEqual([m['text'] for m in py],['...'])
        self.assertNotIn('captures',py[0])

    def test_comment_only_pattern_and_fragment_rules(self):
        self.bad({'files':[{'path':'a','language':'python','source':'x\n'}],'pattern':'# comment'})
        self.bad({'files':[{'path':'a','language':'javascript','source':'x;'}],'pattern':'// comment'})
        self.assertEqual(self.search('a a b','a b')['matches'][0]['text'],'a b')

    def test_exact_nested_match_shape_and_offset_types(self):
        result=self.search('x x','x')
        self.assertEqual(result,{'matches':[{'path':'a','start':0,'end':1,'text':'x'},
                                          {'path':'a','start':2,'end':3,'text':'x'}]})
        for match in result['matches']:
            self.assertIs(type(match['start']),int);self.assertIs(type(match['end']),int)
        for file in ({'path':'a','language':'python','source':False},
                     {'path':'a','language':'python','source':'x','extra':1}):
            self.bad({'files':[file],'pattern':'x'})
