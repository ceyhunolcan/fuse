import json
from evaluator.contract import Contract


def circuit():
    return {"inputs":{"a":4,"b":1},"gates":[{"name":"n","op":"AND","args":["a","b"]},{"name":"y","op":"NOT","args":["n"]}],"outputs":["y"]}


class Formats(Contract):
    def test_json_vectors_and_explicit_source(self):
        self.good(json.dumps(circuit()), {"a":"0011","b":"1"}, {"y":"1100"}, "json")
        self.good("INPUT a 4\ny = NOT(a)\nOUTPUT y", {"a":"0011"}, {"y":"1100"}, "source")

    def test_scalar_equivalence_all_ops(self):
        for op,result in [("AND","0"),("OR","1"),("XOR","1"),("NAND","1"),("NOR","0"),("XNOR","0")]:
            obj={"inputs":{"a":1,"b":1},"gates":[{"name":"y","op":op,"args":["a","b"]}],"outputs":["y","a"]}
            self.good(json.dumps(obj), {"a":"0","b":"1"}, {"y":result,"a":"0"}, "json")
            self.good(f"OUTPUT(y)\n INPUT(a) # hi\nINPUT(b)\ny = {op}(a,b)\nOUTPUT(a)", {"a":"0","b":"1"}, {"y":result,"a":"0"}, "bench")
        self.good("INPUT(a)\ny = BUF(a)\nz = NOT(y)\nOUTPUT(z)", {"a":"1"}, {"z":"0"}, "bench")

    def test_json_validation(self):
        cases=[{},[],{"inputs":[],"gates":[],"outputs":["x"]}]
        for field,value in [("inputs",{"a":True}),("inputs",{"a":0}),("inputs",{"a":1.0}),("inputs",{"bad-name":1}),("outputs",[]),("outputs",[1]),("outputs",["missing"]),("gates",[{"name":"y","op":[],"args":[]}]),("gates",[{"name":"y","op":"NOT","args":[0]}]),("gates",[{"name":"y","op":"NOT","args":"a"}]),("gates",[{"name":"y","op":"NOT","args":["a"],"extra":1}])]:
            obj=circuit();obj[field]=value;cases.append(obj)
        obj=circuit();obj['extra']=0;cases.append(obj)
        obj=circuit();obj['gates'].append(obj['gates'][0]);cases.append(obj)
        for obj in cases:
            with self.subTest(obj=obj): self.bad(json.dumps(obj), {"a":"0011","b":"1"}, "json")
        for raw in ['{"inputs":{},"inputs":{},"gates":[],"outputs":["a"]}', '{"inputs":{"a":1,"a":1},"gates":[],"outputs":["a"]}', '{broken']:
            self.bad(raw, {"a":"1"}, "json")

    def test_bench_validation(self):
        for source in ["INPUT(a)\nINPUT(a)\nOUTPUT(a)", "INPUT(a)\nOUTPUT(a)\nOUTPUT(a)", "INPUT(a)\ny = BUF(z)\nOUTPUT(y)", "x = BUF(y)\ny = BUF(x)\nOUTPUT(x)", "y = BUF(01)\nOUTPUT(y)", "y = MUX(0,1)\nOUTPUT(y)", "INPUT a 1\nOUTPUT(a)", "INPUT(a)\ny = AND(a)\nOUTPUT(y)"]:
            with self.subTest(source=source): self.bad(source, {"a":"0"} if "INPUT" in source else {}, "bench")
        self.bad("INPUT(a)\nOUTPUT(a)", {"a":"01"}, "bench")

    def test_json_cycles_and_widths(self):
        self.bad(json.dumps({"inputs":{},"gates":[{"name":"a","op":"BUF","args":["b"]},{"name":"b","op":"BUF","args":["a"]}],"outputs":["a"]}), {}, "json")
        obj=circuit();obj['inputs']['b']=2
        self.bad(json.dumps(obj), {"a":"0011","b":"01"}, "json")

    def test_nested_gate_duplicate_keys(self):
        for gate in ('{"name":"y","name":"y","op":"BUF","args":["a"]}',
                     '{"name":"y","op":"BUF","op":"BUF","args":["a"]}',
                     '{"name":"y","op":"BUF","args":["a"],"args":["a"]}',
                     '{"name":"y","n\\u0061me":"y","op":"BUF","args":["a"]}'):
            self.bad('{"inputs":{"a":1},"gates":['+gate+'],"outputs":["y"]}',{'a':'1'},'json')
        self.good('{"inputs":{"a":1},"gates":[{"name":"y","op":"BUF","args":["a"]}],"outputs":["y"]}',{'a':'1'},{'y':'1'},'json')

    def test_unknown_format_and_unknown_values_remain_invalid(self):
        source='INPUT a 1\ny = BUF(a)\nOUTPUT y'
        self.bad(source,{'a':'1'},'unknown-format')
        obj={'inputs':{'a':1},'gates':[{'name':'y','op':'BUF','args':['a']}],'outputs':['y']}
        self.bad(json.dumps(obj),{'a':'X'},'json')
        obj['gates'][0]['args']=['X']
        self.bad(json.dumps(obj),{'a':'1'},'json')
        # X remains a legal declared signal name, distinct from unknown logic values.
        obj={'inputs':{'X':1},'gates':[],'outputs':['X']}
        self.good(json.dumps(obj),{'X':'0'},{'X':'0'},'json')
