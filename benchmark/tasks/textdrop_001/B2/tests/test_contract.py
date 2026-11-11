import json, random, unittest
from evaluator.json_contract import JsonContract
class Acceptance(JsonContract):
    module='fusetextdrop'

    def step(self,state,op,**fields):return self.request({'state':state,'command':{'op':op,**fields}})
    def note(self,body='hello',format='plain'):return self.step(None,'create',title='Note',body=body,format=format)

    def test_journal_append_prefix_reopen_and_read_does_not_append(self):
        first=self.request({'backend':'journal','command':{'op':'create','title':'One','body':'x'}});prefix=json.loads(json.dumps(first['state']['events']))
        second=self.request({'backend':'journal','state':first['state'],'command':{'op':'update','id':1,'body':'y'}})
        self.assertEqual(second['state']['events'][:1],prefix);self.assertEqual(len(second['state']['events']),2)
        read=self.request({'backend':'journal','state':json.loads(json.dumps(second['state'])),'command':{'op':'get','id':1}})
        self.assertEqual(read['state'],second['state']);self.assertEqual(read['result']['body'],'y')
        export=self.request({'backend':'journal','state':read['state'],'command':{'op':'export'}})
        snap=self.step(None,'import',serialized=export['result']['serialized']);self.assertEqual(snap['state']['notes']['1']['body'],'y')
        imported=self.request({'backend':'journal','command':{'op':'import','serialized':export['result']['serialized']}});self.assertEqual(imported['result'],{'imported':1})
    def test_unknown_backend_malformed_log_and_mismatch(self):
        for req in [{'backend':'other','command':{'op':'list'}},{'backend':'journal','state':{'version':1,'backend':'journal','events':[{}]},'command':{'op':'list'}},{'backend':'journal','state':{'version':1,'next_id':1,'notes':{}},'command':{'op':'list'}}]:self.bad(req)

    def test_journal_version_is_strict_integer(self):
        self.bad({'backend':'journal','state':{'version':True,'backend':'journal','events':[]},'command':{'op':'list'}})

    def test_empty_journal_and_noop_update_import_do_not_append(self):
        empty=self.request({'backend':'journal','command':{'op':'list'}})
        self.assertEqual(empty['state'],{'version':1,'backend':'journal','events':[]})
        created=self.request({'backend':'journal','state':empty['state'],'command':{'op':'create','title':'One','body':'x'}})
        no_op=self.request({'backend':'journal','state':created['state'],'command':{'op':'update','id':1,'body':'x'}})
        self.assertEqual(no_op['state'],created['state'])
        exported=self.request({'backend':'journal','state':no_op['state'],'command':{'op':'export'}})
        same=self.request({'backend':'journal','state':no_op['state'],'command':{'op':'import','serialized':exported['result']['serialized']}})
        self.assertEqual(same['state'],created['state'])

    def test_all_journal_reads_preserve_history_and_snapshot_results(self):
        created = self.request({'backend':'journal', 'command':{'op':'create', 'title':'One', 'body':'hello'}})
        journal = json.loads(json.dumps(created['state']))
        logical = journal['events'][-1]['snapshot']
        for command in ({'op':'get', 'id':1}, {'op':'list'}, {'op':'render', 'id':1}, {'op':'export'}):
            with self.subTest(op=command['op']):
                observed = self.request({'backend':'journal', 'state':journal, 'command':command})
                portable = self.request({'state':logical, 'command':command})
                self.assertEqual(observed['state'], journal)
                if command['op'] == 'export':
                    self.assertEqual(json.loads(observed['result']['serialized']), logical)
                    self.assertEqual(json.loads(observed['result']['serialized']),
                                     json.loads(portable['result']['serialized']))
                else:
                    self.assertEqual(observed['result'], portable['result'])


    def test_delete_and_changed_import_append_once_preserving_history(self):
        first=self.request({'backend':'journal','command':{'op':'create','title':'One','body':'x'}})
        second=self.request({'backend':'journal','state':first['state'],
            'command':{'op':'create','title':'Two','body':'y'}})
        prior=second['state'];logical=prior['events'][-1]['snapshot']
        snapshot=self.request({'state':logical,'command':{'op':'delete','id':1}})
        deleted=self.request({'backend':'journal','state':prior,'command':{'op':'delete','id':1}})
        self.assertEqual(deleted['result'],snapshot['result'])
        self.assertEqual(deleted['state']['events'],prior['events']+[{'snapshot':snapshot['state']}])
        imported=self.request({'backend':'journal','state':deleted['state'],
            'command':{'op':'import','serialized':json.dumps(first['state']['events'][-1]['snapshot'])}})
        self.assertEqual(imported['result'],{'imported':1})
        self.assertEqual(imported['state']['events'],deleted['state']['events']+first['state']['events'])
        self.assertEqual(self.request({'backend':'journal','state':imported['state'],
            'command':{'op':'list'}})['result'],[{'id':1,'title':'One','body':'x','format':'plain'}])

    def test_equal_import_ignores_recursive_key_order_but_not_field_presence(self):
        first=self.request({'backend':'journal','command':{'op':'create','title':'One','body':'x'}})
        def reverse(value):
            if isinstance(value,dict):return {k:reverse(v) for k,v in reversed(list(value.items()))}
            if isinstance(value,list):return [reverse(v) for v in value]
            return value
        snapshot=reverse(first['state']['events'][-1]['snapshot'])
        same=self.request({'backend':'journal','state':first['state'],
            'command':{'op':'import','serialized':json.dumps(snapshot,ensure_ascii=False)}})
        self.assertEqual(same['state'],first['state'])
        # A state requires format; equivalent formatting is not permission to omit fields.
        del snapshot['notes']['1']['format']
        self.bad({'backend':'journal','state':first['state'],
            'command':{'op':'import','serialized':json.dumps(snapshot)}})
