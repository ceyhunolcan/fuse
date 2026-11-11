# Add JSON and BENCH circuit input

Preserve the entire A contract. Add `--format source|json|bench`, defaulting to source. Explicit source and omitted format must behave identically. Unknown formats must fail. This does not add X values.

JSON circuits contain exactly the keys `inputs`, `gates`, `outputs`. Inputs is an object from valid names to positive integer widths (booleans are invalid widths). Gates is an array of objects with exactly `name`, `op`, `args`; name/op are strings and args is an array of operand strings. Outputs is a nonempty array of signal-name strings. Reject duplicate JSON keys at every nesting depth and reject extra/missing keys or incorrect types. Operators, literals, broadcast rules, ordering, namespace uniqueness, and validation follow A.
```
{"inputs":{"a":4,"b":1},"gates":[{"name":"y","op":"AND","args":["a","b"]}],"outputs":["y"]}
```

BENCH is the following deliberately bounded dialect: `INPUT(name)` declares a width-one input; `OUTPUT(name)` selects an output; gates use A's `name = OP(operand,operand)` or unary syntax. Blank lines, whitespace, and # comments are accepted. Any declaration order is allowed. All signals/literals are scalar; reject vector literals. Support every A operator; no other BENCH dialect features are required. Reject duplicates, undefined signals, cycles, wrong arity, and malformed declarations. Supplied inputs must have width one.

Equivalent circuits in all three formats must evaluate identically. Vector circuits can be represented in source or JSON. A+B2 must operate independently of three-valued logic.
