import json, random, unittest
from evaluator.json_contract import JsonContract
class Acceptance(JsonContract):
    module='fusecodesearch'

    def search(self,source,pattern,language='python',**extra):return self.request({'files':[{'path':'a','language':language,'source':source}],'pattern':pattern,'pattern_mode':'structural',**extra})

    def test_repeated_named_holes_and_capture_offsets(self):
        result=self.search('a+a\na+b\nz+z\n','$x + $x')['matches'];self.assertEqual([m['text'] for m in result],['a+a','z+z']);self.assertEqual([m['captures'] for m in result],[{'x':'a'},{'x':'z'}])
    def test_balanced_ellipsis_nested_calls_zero_and_shortest(self):
        found=self.search('call()\ncall(a, nested(b,c))\ncall("(")\n','call(...)')['matches'];self.assertEqual([m['text'] for m in found],['call()','call(a, nested(b,c))','call("(")'])
        result=self.search('f(a,b); f(c);','f(...)','javascript')['matches'];self.assertEqual([m['text'] for m in result],['f(a,b)','f(c)'])
    def test_identifier_holes_exclude_literals_and_pattern_needs_literal(self):
        self.assertEqual(len(self.search('f(1)\nf(x)\n','f($x)')['matches']),1)
        self.bad({'files':[{'path':'a','language':'python','source':'x\n'}],'pattern':'$x','pattern_mode':'structural'})
        self.bad({'files':[{'path':'a','language':'javascript','source':'x;'}],'pattern':'...','pattern_mode':'structural'})

    def test_structural_mode_is_explicit_and_invalid_modes_fail(self):
        request={'files':[{'path':'a','language':'javascript','source':'$x+x;'}],'pattern':'$x'}
        self.assertEqual(self.request(request)['matches'][0]['text'],'$x')
        self.assertNotIn('captures',self.request(request)['matches'][0])
        self.assertEqual(self.request(dict(request,pattern_mode='exact')),self.request(request))
        self.bad(dict(request,pattern_mode='other'))
        self.bad(dict(request,pattern_mode=True))
        self.bad(dict(request,pattern_mode='structural'))

    def test_ellipsis_cannot_cross_a_semicolon(self):
        result = self.search('a;b', 'a ... b', language='javascript')
        self.assertEqual(result['matches'], [])

    def test_ellipsis_chooses_shortest_complete_match(self):
        result = self.search('a b b', 'a ... b', language='javascript')
        self.assertEqual([m['text'] for m in result['matches']], ['a b'])
        self.assertEqual(result['matches'][0]['captures'], {})

    def test_multiple_ellipses_choose_lengths_left_to_right(self):
        result = self.search('a b b c c', 'a ... b ... c', language='javascript')
        self.assertEqual([m['text'] for m in result['matches']], ['a b b c'])


    def test_crossed_unmatched_delimiters_and_opaque_string_tokens(self):
        for source in ('a ([)] b','a ( b','a ) b','a [{]} b'):
            self.assertEqual(self.search(source,'a ... b','javascript'),{'matches':[]})
        for source in ('a ([{x}]) b','a "([)]" b'):
            self.assertEqual(self.search(source,'a ... b','javascript'),{'matches':[
                {'path':'a','start':0,'end':len(source),'text':source,'captures':{}}]})

    def test_backtracking_restores_capture_bindings(self):
        source='start a end b end b'
        self.assertEqual(self.search(source,'start ... $x end $x','javascript'),{'matches':[
            {'path':'a','start':0,'end':len(source),'text':source,'captures':{'x':'b'}}]})
        self.assertEqual(self.search('start a end b','start ... $x end $x','javascript'),{'matches':[]})

    def test_ascii_capture_names_with_unicode_source_identifiers(self):
        for language in ('python','javascript'):
            payload={'files':[{'path':'a','language':language,'source':'π+π'}],
                     'pattern':'$é + $é','pattern_mode':'structural'}
            self.bad(payload)
            result=self.search('π+π','$x + $x',language)
            self.assertEqual(result,{'matches':[{'path':'a','start':0,'end':3,'text':'π+π','captures':{'x':'π'}}]})
        self.assertEqual(self.request({'files':[{'path':'a','language':'javascript','source':'$é'}],
            'pattern':'$é','pattern_mode':'exact'}),{'matches':[{'path':'a','start':0,'end':2,'text':'$é'}]})
