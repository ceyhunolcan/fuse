# Circuit evaluator

Run `python -m fusecircuit path.txt --inputs '{"a":"0"}'`.
The source declares scalar inputs (`INPUT a 1`), logical gates (`n = NOT(a)`), and outputs (`OUTPUT n`). Supports NOT, BUF, AND, OR, XOR, NAND, NOR, XNOR.
