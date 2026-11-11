import json, random, unittest
from evaluator.json_contract import JsonContract
class Acceptance(JsonContract):
    module='fusetextdrop'

    def step(self,state,op,**fields):return self.request({'state':state,'command':{'op':op,**fields}})
    def note(self,body='hello',format='plain'):return self.step(None,'create',title='Note',body=body,format=format)

    def test_identity_ownership_session_roundtrip_and_logout(self):
        r=self.step(None,'register',username='alice',password='password1');self.assertEqual(r['result'],{'registered':'alice'})
        self.assertNotIn('password1',json.dumps(r['state']))
        login=self.step(r['state'],'login',username='alice',password='password1');token=login['result']['session']
        note=self.request({'state':login['state'],'session':token,'command':{'op':'create','title':'Private','body':'secret'}})
        state=json.loads(json.dumps(note['state']));self.assertEqual(note['result']['owner'],'alice')
        self.bad({'state':state,'command':{'op':'get','id':1}})
        self.bad({'state':state,'command':{'op':'login','username':'alice','password':'wrongpass'}})
        bob=self.step(state,'register',username='bob',password='password2');boblogin=self.step(bob['state'],'login',username='bob',password='password2');bt=boblogin['result']['session']
        self.bad({'state':boblogin['state'],'session':bt,'command':{'op':'get','id':1}})
        self.assertEqual(self.request({'state':boblogin['state'],'session':bt,'command':{'op':'list'}})['result'],[])
        out=self.request({'state':boblogin['state'],'session':token,'command':{'op':'logout'}})
        self.bad({'state':out['state'],'session':token,'command':{'op':'get','id':1}})
    def test_delete_account_removes_owned_data_and_all_sessions(self):
        r=self.step(None,'register',username='alice',password='password1');one=self.step(r['state'],'login',username='alice',password='password1');t1=one['result']['session'];two=self.step(one['state'],'login',username='alice',password='password1');t2=two['result']['session']
        note=self.request({'state':two['state'],'session':t1,'command':{'op':'create','title':'x','body':'y'}})
        bob=self.step(note['state'],'register',username='bob',password='password2')
        deleted=self.request({'state':bob['state'],'session':t2,'command':{'op':'delete_account'}})
        self.assertEqual(set(deleted['state']['accounts']),{'bob'});self.assertEqual(deleted['state']['notes'],{});self.assertNotIn(t1,deleted['state']['sessions']);self.assertNotIn(t2,deleted['state']['sessions'])

    def test_last_account_deletion_restores_anonymous_access(self):
        anon=self.note('anonymous')['state']
        registered=self.step(anon,'register',username='alice',password='password1')
        login=self.step(registered['state'],'login',username='alice',password='password1')
        token=login['result']['session']
        self.assertEqual(self.request({'state':login['state'],'session':token,'command':{'op':'list'}})['result'],[])
        private=self.request({'state':login['state'],'session':token,'command':{'op':'create','title':'Private','body':'secret'}})
        deleted=self.request({'state':private['state'],'session':token,'command':{'op':'delete_account'}})
        self.assertEqual(deleted['result'],{'deleted_account':'alice'})
        self.assertEqual(deleted['state']['accounts'],{})
        self.assertEqual(deleted['state']['sessions'],{})
        visible=self.step(deleted['state'],'list')['result']
        self.assertEqual([n['id'] for n in visible],[1]);self.assertIsNone(visible[0]['owner'])
        created=self.step(deleted['state'],'create',title='New',body='anonymous')
        self.assertEqual(created['result']['id'],3);self.assertIsNone(created['result']['owner'])

    def test_authenticated_import_replaces_session_set(self):
        initial=self.step(None,'register',username='alice',password='password1')
        old=self.step(initial['state'],'login',username='alice',password='password1')
        backup=self.step(old['state'],'login',username='alice',password='password1')
        restored_token=backup['result']['session']
        out=self.request({'state':backup['state'],'session':old['result']['session'],'command':{'op':'logout'}})
        restored=self.request({'state':out['state'],'session':restored_token,'command':{'op':'import','serialized':json.dumps(backup['state'])}})
        self.assertEqual(self.request({'state':restored['state'],'session':old['result']['session'],'command':{'op':'list'}})['result'],[])
        anonymous=self.request({'state':restored['state'],'session':restored_token,'command':{'op':'import','serialized':json.dumps({'version':1,'next_id':1,'notes':{}})}})
        self.assertEqual(self.step(anonymous['state'],'list')['result'],[])
        self.assertNotIn('accounts',anonymous['state'])

    def test_owner_checks_cover_every_note_operation(self):
        registered = self.step(None, 'register', username='alice', password='password1')
        alice = self.step(registered['state'], 'login', username='alice', password='password1')
        alice_token = alice['result']['session']
        note = self.request({'state':alice['state'], 'session':alice_token,
                             'command':{'op':'create', 'title':'Alice', 'body':'private'}})
        identifier = note['result']['id']
        registered_bob = self.step(note['state'], 'register', username='bob', password='password2')
        bob = self.step(registered_bob['state'], 'login', username='bob', password='password2')
        bob_token = bob['result']['session']
        state = bob['state']
        for command in ({'op':'get', 'id':identifier}, {'op':'render', 'id':identifier},
                        {'op':'update', 'id':identifier, 'body':'changed'},
                        {'op':'delete', 'id':identifier}):
            with self.subTest(op=command['op']):
                self.bad({'state':state, 'session':bob_token, 'command':command})
                allowed = self.request({'state':state, 'session':alice_token, 'command':command})
                if command['op'] == 'get':
                    self.assertEqual(allowed['result']['body'], 'private')
                elif command['op'] == 'render':
                    self.assertEqual(allowed['result'], {'html':'<pre>private</pre>'})
                elif command['op'] == 'update':
                    self.assertEqual(allowed['result']['body'], 'changed')
                else:
                    self.assertNotIn(str(identifier), allowed['state']['notes'])

    def test_account_deletion_preserves_other_users_data_and_access(self):
        registered = self.step(None, 'register', username='alice', password='password1')
        alice = self.step(registered['state'], 'login', username='alice', password='password1')
        at = alice['result']['session']
        first = self.request({'state':alice['state'], 'session':at,
                              'command':{'op':'create', 'title':'Alice', 'body':'a'}})
        registered_bob = self.step(first['state'], 'register', username='bob', password='password2')
        bob = self.step(registered_bob['state'], 'login', username='bob', password='password2')
        bt = bob['result']['session']
        second = self.request({'state':bob['state'], 'session':bt,
                               'command':{'op':'create', 'title':'Bob', 'body':'b'}})
        before = second['state']
        deleted = self.request({'state':before, 'session':at, 'command':{'op':'delete_account'}})
        self.assertEqual(deleted['state']['accounts']['bob'], before['accounts']['bob'])
        self.assertEqual(deleted['state']['sessions'][bt], 'bob')
        self.assertEqual(deleted['state']['next_id'], before['next_id'])
        listing = self.request({'state':deleted['state'], 'session':bt, 'command':{'op':'list'}})
        self.assertEqual(listing['result'], [second['result']])
        self.assertEqual(deleted['state']['notes'], {str(second['result']['id']):second['result']})


    def test_backup_authorization_and_public_bootstrap(self):
        anonymous=self.step(None,'export')
        r=self.step(None,'register',username='alice',password='password1')
        login=self.request({'state':r['state'],'session':'ignored-bootstrap-token',
            'command':{'op':'login','username':'alice','password':'password1'}})
        token=login['result']['session'];state=login['state']
        self.assertIsInstance(token,str);self.assertTrue(token)
        bob=self.request({'state':state,'session':'ignored-bootstrap-token',
            'command':{'op':'register','username':'bob','password':'password2'}})
        self.assertEqual(bob['result'],{'registered':'bob'})
        for command in ({'op':'export'},{'op':'import','serialized':anonymous['result']['serialized']}):
            self.bad({'state':state,'command':command})
            self.bad({'state':state,'session':'unknown-token','command':command})
            allowed=self.request({'state':state,'session':token,'command':command})
            if command['op']=='export':self.assertEqual(json.loads(allowed['result']['serialized']),state)
            else:self.assertEqual(allowed['state'],anonymous['state'])

    def test_identity_session_and_owner_references_are_validated(self):
        import copy
        registered=self.step(None,'register',username='alice',password='password1')['state']
        # Credential records are obtained from the implementation, never prescribed here.
        for sessions in ({'':'alice'},{'valid-looking-token':'absent-user'}):
            state=copy.deepcopy(registered);state['sessions']=sessions
            self.bad({'state':state,'command':{'op':'register','username':'bob','password':'password2'}})
        login=self.step(registered,'login',username='alice',password='password1')
        created=self.request({'state':login['state'],'session':login['result']['session'],
            'command':{'op':'create','title':'One','body':'body'}})
        state=created['state'];state['notes']['1']['owner']='absent-user'
        self.bad({'state':state,'command':{'op':'register','username':'bob','password':'password2'}})
