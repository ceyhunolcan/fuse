# Current work: vector-capable two-valued circuits

Extend the supplied scalar evaluator to support bit vectors while preserving scalar behavior. Only 0 and 1 are logical values. No new circuit file formats or public format-selection options are in scope.

The command is `python -m fusecircuit PATH --inputs JSON_OBJECT`. On success exit 0 and print exactly one JSON object mapping output names to nonempty bitstrings; output key order is irrelevant. On invalid input exit nonzero, print an explanation to stderr, and print no result to stdout. `--help` must work. Keep this interface; internal structure is unconstrained.

Source syntax (one declaration per line):
```
INPUT a 4
INPUT enable 1
n = AND(a,enable)
result = NOT(n)
OUTPUT result
```
Blank lines, surrounding whitespace, and `#` comments (whole-line or trailing) are ignored. Names match `[A-Za-z_][A-Za-z0-9_]*`, are case-sensitive, and share one namespace across inputs and gates. Input widths are positive decimal integers without leading zeros. Declarations can be in any order. Operands are names or nonempty literal bitstrings. `NOT` and `BUF` take exactly one operand; `AND`, `OR`, `XOR`, `NAND`, `NOR`, and `XNOR` take exactly two. Operators are uppercase and bitwise, following ordinary Boolean truth tables. An output refers to a declared input or gate. Multiple distinct outputs are allowed; at least one is required.

Each declared input must have exactly one supplied string value of exactly its declared width. Reject undeclared supplied inputs, missing inputs, non-string values, nonbinary characters, and width mismatches. Width-one operands broadcast to the other operand's width; unequal widths greater than one are invalid. Unary results retain operand width. Literals follow the same width/broadcast rules as signals.

Reject duplicate input/gate names, duplicate outputs, unsupported operators, wrong arity, malformed syntax, undefined signals, and cycles. Validate all gates, including unused ones. Existing scalar invocations remain valid. Do not add three-valued behavior or additional circuit formats.
