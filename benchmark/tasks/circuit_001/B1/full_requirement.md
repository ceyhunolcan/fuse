# Add unknown logical values

Preserve the entire A contract. Allow uppercase X in every input bitstring and source literal alongside 0 and 1, without requiring a flag. A declared signal named X remains valid and takes priority over interpreting an operand X as a literal. An unbound operand X denotes the unknown literal. Lowercase x in bitstrings is invalid (it can still be an ordinary signal name). Width rules remain unchanged. Outputs are strings over 0,1,X.

BUF returns its input. NOT maps 0 to 1, 1 to 0, and X to X. AND returns 0 if either argument is 0, 1 if both are 1, otherwise X. OR returns 1 if either argument is 1, 0 if both are 0, otherwise X. XOR returns X if either argument is X, otherwise ordinary XOR. NAND, NOR, XNOR apply NOT to AND, OR, XOR respectively. Apply these rules independently to every vector position after scalar broadcasting.

Reject invalid characters, arities, widths, and all A validation errors. The existing command and source syntax remain available. JSON/BENCH circuit formats are not required. JSON transport of --inputs and output is already part of A, independent of circuit formats.
