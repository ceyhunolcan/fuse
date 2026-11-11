import itertools
from evaluator.contract import Contract


def expected(op,a,b):
    if op in {"AND","NAND"}:
        value = "0" if "0" in (a,b) else ("1" if a==b=="1" else "X")
    elif op in {"OR","NOR"}:
        value = "1" if "1" in (a,b) else ("0" if a==b=="0" else "X")
    else:
        value = "X" if "X" in (a,b) else str(int(a)^int(b))
    return {"0":"1","1":"0","X":"X"}[value] if op in {"NAND","NOR","XNOR"} else value


class Unknown(Contract):
    def test_every_truth_table(self):
        for op in ["AND","OR","XOR","NAND","NOR","XNOR"]:
            for a,b in itertools.product("01X",repeat=2):
                with self.subTest(op=op,a=a,b=b):
                    self.good(f"INPUT a 1\nINPUT b 1\ny = {op}(a,b)\nOUTPUT y", {"a":a,"b":b}, {"y":expected(op,a,b)})

    def test_unknown_unary(self):
        self.good("INPUT a 3\nb = BUF(a)\nn = NOT(a)\nOUTPUT b\nOUTPUT n", {"a":"01X"}, {"b":"01X","n":"10X"})

    def test_literals_vectors_broadcast(self):
        self.good("INPUT a 4\nb = AND(a,X)\nc = OR(X,a)\nd = XOR(a,01XX)\nOUTPUT b\nOUTPUT c\nOUTPUT d", {"a":"010X"}, {"b":"0X0X","c":"X1XX","d":"00XX"})
        self.good("a = NOT(X)\nOUTPUT a", {}, {"a":"X"})

    def test_preserve_declared_X_name(self):
        self.good("INPUT X 3\nb = BUF(X)\nOUTPUT b", {"X":"01X"}, {"b":"01X"})

    def test_unknown_invalid(self):
        for value in ["x","?","Z","0X0"]:
            self.bad("INPUT a 1\nOUTPUT a", {"a":value})
        self.bad("a = AND(XX,101)\nOUTPUT a", {})
