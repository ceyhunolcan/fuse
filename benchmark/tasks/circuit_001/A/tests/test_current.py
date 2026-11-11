import itertools
import random
import os
import subprocess
import sys
from evaluator.contract import Contract,run_candidate


class Current(Contract):
    def test_cli_help_and_missing_arguments(self):
        prefix = [sys.executable,"-m","fusecircuit"]
        result = run_candidate(prefix+["--help"],os.environ['FUSE_REPO'])
        self.assertEqual(result.returncode,0)
        self.assertIn("--inputs",result.stdout)
        result = run_candidate(prefix,os.environ['FUSE_REPO'])
        self.assertNotEqual(result.returncode,0)
    def test_all_scalar_truth_tables(self):
        functions = {"AND": lambda a,b:a&b, "OR": lambda a,b:a|b, "XOR": lambda a,b:a^b,
                     "NAND": lambda a,b:1-(a&b), "NOR": lambda a,b:1-(a|b), "XNOR": lambda a,b:1-(a^b)}
        for op, function in functions.items():
            for a,b in itertools.product(range(2), repeat=2):
                with self.subTest(op=op,a=a,b=b):
                    self.good(f"INPUT a 1\nINPUT b 1\ny = {op}(a,b)\nOUTPUT y", {"a":str(a),"b":str(b)}, {"y":str(function(a,b))})
        for a in range(2):
            self.good("INPUT a 1\nn = NOT(a)\nb = BUF(a)\nOUTPUT n\nOUTPUT b", {"a":str(a)}, {"n":str(1-a),"b":str(a)})

    def test_random_vectors(self):
        rng = random.Random(819)
        for width in [2,3,7,17,64]:
            a = "".join(rng.choice("01") for _ in range(width))
            b = "".join(rng.choice("01") for _ in range(width))
            expected = "".join(str(int(x)^int(y)) for x,y in zip(a,b))
            self.good(f"INPUT a {width}\nINPUT b {width}\ny = XOR(a,b)\nOUTPUT y", {"a":a,"b":b}, {"y":expected})

    def test_vector_all_operators(self):
        self.good("INPUT a 4\nINPUT b 4\nand = AND(a,b)\nor = OR(a,b)\nxor = XOR(a,b)\nnand = NAND(a,b)\nnor = NOR(a,b)\nxnor = XNOR(a,b)\nnot = NOT(a)\nbuf = BUF(a)\n" + "\n".join("OUTPUT "+op for op in ["and","or","xor","nand","nor","xnor","not","buf"]),
                  {"a":"0011","b":"0101"}, {"and":"0001","or":"0111","xor":"0110","nand":"1110","nor":"1000","xnor":"1001","not":"1100","buf":"0011"})

    def test_broadcast_each_side(self):
        self.good("INPUT a 4\nINPUT b 1\nu = AND(a,b)\nv = XOR(b,a)\nw = OR(0,a)\nOUTPUT u\nOUTPUT v\nOUTPUT w", {"a":"0101","b":"1"}, {"u":"0101","v":"1010","w":"0101"})

    def test_literals_order_comments_outputs(self):
        self.good(" # comment\nOUTPUT y\n y = XOR(a,1100) # note\nINPUT a 4\nOUTPUT a\n", {"a":"1010"}, {"y":"0110","a":"1010"})
        self.good("y = NOT(0011)\nOUTPUT y", {}, {"y":"1100"})

    def test_names_and_signal_X(self):
        self.good("INPUT X 1\nINPUT x 1\n_result = XOR(X,x)\nOUTPUT _result", {"X":"1","x":"0"}, {"_result":"1"})

    def test_bad_values(self):
        for values in [{},{"a":"0","extra":"1"},{"a":1},{"a":True},{"a":None},{"a":""},{"a":"2"},{"a":"Z"},{"a":"00"},[],None]:
            with self.subTest(values=values):
                self.bad("INPUT a 1\nOUTPUT a", values)

    def test_bad_syntax(self):
        for source in ["", "INPUT a 0\nOUTPUT a", "INPUT 9a 1\nOUTPUT 9a", "INPUT a -1\nOUTPUT a", "INPUT a 1\nINPUT a 1\nOUTPUT a", "INPUT a 1\na = BUF(0)\nOUTPUT a", "a = BUF(0)\na = NOT(1)\nOUTPUT a", "a = MUX(0,1)\nOUTPUT a", "a = and(0,1)\nOUTPUT a", "a = NOT(0,1)\nOUTPUT a", "a = AND(0)\nOUTPUT a", "a = BUF()\nOUTPUT a", "a = AND(0,)\nOUTPUT a", "a = BUF(2)\nOUTPUT a", "a = BUF(1)\nOUTPUT a\nOUTPUT a", "OUTPUT missing", "a = BUF(b)\nb = BUF(a)\nOUTPUT a", "a = BUF(1)\nb = BUF(missing)\nOUTPUT a", "a = BUF(1)\nb = BUF(c)\nc = BUF(b)\nOUTPUT a"]:
            with self.subTest(source=source):
                self.bad(source, {})

    def test_bad_vector_widths(self):
        self.bad("INPUT a 2\nINPUT b 3\ny = AND(a,b)\nOUTPUT y", {"a":"01","b":"010"})
        self.bad("INPUT a 4\ny = AND(a,01)\nOUTPUT y", {"a":"0101"})
